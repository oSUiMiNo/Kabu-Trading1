"""
Discord 通知モジュール（shared）

Webhook で Embed メッセージを送信する。stdlib のみ使用（新規依存なし）。
サマリー生成は Monitor の notify-summarizer サブエージェントを呼び出す。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from notification_types import NotifyLabel, NotifyPayload, LABEL_COLOR, MARKET_JA

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = _PROJECT_ROOT / "Monitor" / ".claude" / "commands"

_RESULT_JA = {"OK": "OK", "NG": "NG", "ERROR": "エラー"}

_DECISION_JA = {
    "BUY": "買い",
    "SELL": "売り",
    "ADD": "買い増し",
    "REDUCE": "売り減らし",
    "HOLD": "現状維持",
}

_CONFIDENCE_JA = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

_VALID_RISK_FLAGS = {
    "価格乖離超過（下落）",
    "根拠崩壊",
    "規制リスク",
    "決算未達",
    "セクター悪化",
    "マクロショック",
    "経営陣変更",
    "価格乖離超過（上昇）",
    "目標価格到達",
    "決算好調",
    "懸念材料の解消",
}

_WATCH_KIND_JA = {
    "post_release_5m": "発表5分後",
    "post_release_20m": "発表20分後",
    "post_press_10m": "会見10分後",
    "jp_follow_tse_open": "翌日東証寄付",
    "us_follow_tse_open": "翌日東証寄付（米国向け）",
    "boj_midday": "日銀 昼",
    "boj_afternoon": "日銀 午後",
}

_LABEL_EMOJI = {
    NotifyLabel.URGENT: "🚨",
    NotifyLabel.GOOD_NEWS: "🎉",
    NotifyLabel.WARNING: "⚠️",
    NotifyLabel.CHECK: "🔍",
    NotifyLabel.COMPLETE: "✅",
    NotifyLabel.ERROR: "❌",
}


def _build_summary_prompt(payload: NotifyPayload) -> str:
    """notify-summarizer に渡すプロンプトを組み立てる。"""
    md = payload.monitor_data
    parts = [
        f"銘柄: {payload.display_name or payload.ticker}",
        f"判定: {md.get('result', '不明')}",
        f"変動率: {md.get('price_change_pct', '不明')}%",
        f"サマリー: {md.get('summary', '')}",
    ]
    if md.get("ng_reason"):
        parts.append(f"NG理由: {md['ng_reason']}")
    if payload.new_plan:
        plan = payload.new_plan
        parts.append(f"新プラン判定: {plan.get('decision_final', '不明')}")
        parts.append(f"confidence: {plan.get('confidence', '不明')}")
        alloc = plan.get("allocation_jpy")
        if alloc is not None:
            parts.append(f"配分額: ¥{alloc:,.0f}")
        qty = plan.get("quantity")
        if qty is not None:
            parts.append(f"数量: {qty}")
    if payload.event_context:
        ec = payload.event_context
        parts.append(f"トリガイベント: {ec.get('name_ja', ec.get('event_id', ''))}")
    return "\n".join(parts)


async def generate_beginner_summary(payload: NotifyPayload) -> str:
    """notify-summarizer サブエージェントで初心者向けサマリーを生成する。"""
    try:
        from AgentUtil import call_agent

        prompt = _build_summary_prompt(payload)
        agent_file = AGENTS_DIR / "notify-summarizer.md"
        if not agent_file.exists():
            return ""
        result = await call_agent(prompt, file_path=str(agent_file))
        return (result.text or "").strip() if result else ""
    except Exception as e:
        print(f"  [通知] サマリー生成失敗（スキップ）: {e}")
        return ""


def build_embed(payload: NotifyPayload) -> dict:
    """Discord Embed dict を構築する。"""
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    md = payload.monitor_data
    label = payload.label
    color = LABEL_COLOR.get(label, 0x808080)
    emoji = _LABEL_EMOJI.get(label, "")
    name = payload.display_name or payload.ticker
    title = f"{emoji} [ {label.value} ]　{name}"
    timestamp = datetime.now(JST).isoformat()
    fields = []

    if label == NotifyLabel.ERROR:
        fields.append({"name": "エラー詳細", "value": payload.error_detail or "不明"})
        return {"title": title, "color": color, "timestamp": timestamp, "fields": fields}

    if label == NotifyLabel.COMPLETE:
        tickers = md.get("tickers", [])
        ng_tickers = md.get("ng_tickers", [])
        count = len(tickers)
        fields.append({"name": "チェック数", "value": f"{count} 銘柄", "inline": True})
        if tickers:
            fields.append({"name": "銘柄", "value": ", ".join(tickers)})
        if ng_tickers:
            fields.append({"name": "NG銘柄（再プラン済み）", "value": ", ".join(ng_tickers)})
        description = (
            "全銘柄のチェックとプラン更新が完了しました。"
            if ng_tickers
            else "全銘柄のプランが現在の市場状況に対して有効です。"
        )
        return {"title": title, "description": description, "color": color, "timestamp": timestamp, "fields": fields}

    current_price = md.get("current_price", "?")
    plan_price = md.get("plan_price", "?")
    pct = md.get("price_change_pct")
    pct_str = f"{pct:+.2f}%" if pct is not None else "?"

    if payload.new_plan:
        plan = payload.new_plan
        decision_raw = str(plan.get("decision_final", "?"))
        decision_ja = _DECISION_JA.get(decision_raw.strip("*").strip(), decision_raw)
        confidence_raw = str(plan.get("confidence", "?"))
        confidence_ja = _CONFIDENCE_JA.get(confidence_raw.lower(), confidence_raw)

        action_summary = payload.beginner_summary or ""

        result_lines = [f"> 判定 : {decision_ja}", f"> 確信度 : {confidence_ja}"]
        alloc = plan.get("allocation_jpy")
        if alloc is not None:
            result_lines.append(f"> 配分額 : ¥{alloc:,.0f}")
        qty = plan.get("quantity")
        if qty is not None:
            result_lines.append(f"> 数量 : {qty}")
        fields.append({"name": "議論結果", "value": "\n".join(result_lines)})

        fields.append({"name": "株価", "value": (
            f"> 現在価格  : {current_price}\n"
            f"> プラン時  : {plan_price}\n"
            f"> 変動率    : {pct_str}"
        )})

        summary = md.get("summary", "")
        if summary:
            fields.append({"name": "議論サマリ", "value": f"> {summary[:1020]}"})

        ng_reason = md.get("ng_reason", "")
        if ng_reason:
            fields.append({"name": "監視時NG理由", "value": f"> {ng_reason[:1020]}"})

        risk_flags = md.get("risk_flags") or []
        if risk_flags:
            flags_ja = [f for f in risk_flags if f in _VALID_RISK_FLAGS]
            fields.append({"name": "リスクフラグ", "value": "> " + ", ".join(flags_ja)})

        if payload.plan_comparison:
            fields.append({"name": "前回プランとの比較", "value": f"> {payload.plan_comparison[:1020]}"})

        embed = {
            "title": title,
            "description": action_summary or "プラン続行がNGと判断し再議論しました",
            "color": color,
            "timestamp": timestamp,
            "fields": fields,
        }

    else:
        result_raw = md.get("result", "?")
        result_val = _RESULT_JA.get(result_raw, result_raw)
        fields.append({"name": "株価", "value": (
            f"> 結果      : {result_val}\n"
            f"> 現在価格  : {current_price}\n"
            f"> プラン時  : {plan_price}\n"
            f"> 変動率    : {pct_str}"
        )})

        summary = md.get("summary", "")
        if summary:
            fields.append({"name": "議論サマリ", "value": f"> {summary[:1020]}"})

        ng_reason = md.get("ng_reason", "")
        if ng_reason:
            fields.append({"name": "監視時NG理由", "value": f"> {ng_reason[:1020]}"})

        risk_flags = md.get("risk_flags") or []
        if risk_flags:
            flags_ja = [f for f in risk_flags if f in _VALID_RISK_FLAGS]
            fields.append({"name": "リスクフラグ", "value": "> " + ", ".join(flags_ja)})

        embed = {"title": title, "color": color, "timestamp": timestamp, "fields": fields}
        if payload.beginner_summary:
            embed["description"] = payload.beginner_summary

    if payload.event_context:
        ec = payload.event_context
        event_name = ec.get("name_ja", ec.get("event_id", "?"))
        watch_kind_raw = ec.get("watch_kind", "")
        watch_kind_ja = _WATCH_KIND_JA.get(watch_kind_raw, watch_kind_raw)
        event_str = event_name + (f"（{watch_kind_ja}）" if watch_kind_ja else "")
        fields.append({"name": "トリガイベント", "value": event_str})

    return embed


def send_webhook(embed: dict, content: str = "") -> bool:
    """Discord Webhook にメッセージを送信する。"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("  [通知] DISCORD_WEBHOOK_URL 未設定 — スキップ")
        return False

    payload: dict = {}
    if content:
        payload["content"] = content
    payload["embeds"] = [embed]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "InvestmentMonitor/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if status in (200, 204):
                print(f"  [通知] Discord 送信成功 (HTTP {status})")
                return True
            else:
                print(f"  [通知] Discord 送信: HTTP {status}")
                return False
    except Exception as e:
        print(f"  [通知] Discord 送信失敗: {e}")
        return False


def send_start_notification(market: str | None) -> bool:
    """Monitor 開始時に Discord に通知する。"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=9)))
    time_str = now.strftime("%Y-%m-%d %H:%M JST")
    market_ja = MARKET_JA.get(market, "全銘柄") if market else "全銘柄"
    embed = {
        "description": f"🕐  **{time_str}**",
        "color": LABEL_COLOR[NotifyLabel.START],
    }
    content = (
        "## ============================\n"
        f"# {market_ja} Monitor 開始\n"
        "## ============================"
    )
    return send_webhook(embed, content=content)


async def notify(payload: NotifyPayload) -> bool:
    """サマリー生成 → Embed 構築 → Webhook 送信の統合関数。"""
    if payload.label not in (NotifyLabel.ERROR, NotifyLabel.COMPLETE):
        summary = await generate_beginner_summary(payload)
        payload.beginner_summary = summary

    embed = build_embed(payload)
    return send_webhook(embed)
