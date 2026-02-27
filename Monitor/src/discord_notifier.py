"""
Discord 通知モジュール

Webhook で Embed メッセージを送信する。stdlib のみ使用（新規依存なし）。
サマリー生成は notify-summarizer サブエージェントを呼び出す。
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from notification_types import NotifyLabel, NotifyPayload, LABEL_COLOR

AGENTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "commands"

_RESULT_JA = {"OK": "OK", "NG": "NG", "ERROR": "エラー"}

_DECISION_JA = {
    "BUY": "買い",
    "NOT_BUY_WAIT": "買わない（様子見）",
    "SELL": "売り",
    "NOT_SELL_HOLD": "売らない（保有継続）",
}

_RISK_FLAG_JA = {
    "price_deviation_exceeded": "価格乖離超過（下落）",
    "basis_invalidated": "根拠崩壊",
    "new_regulatory_risk": "規制リスク",
    "earnings_miss": "決算未達",
    "sector_downturn": "セクター悪化",
    "macro_shock": "マクロショック",
    "management_change": "経営陣変更",
    "price_surge_exceeded": "価格乖離超過（上昇）",
    "target_reached": "目標価格到達",
    "earnings_beat": "決算好調",
    "concern_resolved": "懸念材料の解消",
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


def _build_summary_prompt(payload: NotifyPayload) -> str:
    """notify-summarizer に渡すプロンプトを組み立てる。"""
    md = payload.monitor_data
    parts = [
        f"銘柄: {payload.ticker}",
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
    md = payload.monitor_data
    label = payload.label
    color = LABEL_COLOR.get(label, 0x808080)
    title = f"【{label.value}】{payload.ticker}"

    fields = []

    if label == NotifyLabel.ERROR:
        fields.append({"name": "エラー詳細", "value": payload.error_detail or "不明", "inline": False})
        return {
            "title": title,
            "color": color,
            "fields": fields,
        }

    if label == NotifyLabel.COMPLETE:
        tickers = md.get("tickers", [])
        count = len(tickers)
        fields.append({"name": "チェック数", "value": f"{count} 銘柄", "inline": True})
        if tickers:
            fields.append({"name": "銘柄", "value": ", ".join(tickers), "inline": False})
        return {
            "title": title,
            "description": "全銘柄のプランが現在の市場状況に対して有効です。",
            "color": color,
            "fields": fields,
        }

    result_raw = md.get("result", "?")
    result_val = _RESULT_JA.get(result_raw, result_raw)
    current_price = md.get("current_price", "?")
    plan_price = md.get("plan_price", "?")
    pct = md.get("price_change_pct")
    pct_str = f"{pct:+.2f}%" if pct is not None else "?"

    fields.append({"name": "結果", "value": result_val, "inline": True})
    fields.append({"name": "現在価格", "value": str(current_price), "inline": True})
    fields.append({"name": "プラン時価格", "value": str(plan_price), "inline": True})
    fields.append({"name": "変動率", "value": pct_str, "inline": True})

    summary = md.get("summary", "")
    if summary:
        fields.append({"name": "サマリー", "value": summary[:1024], "inline": False})

    ng_reason = md.get("ng_reason", "")
    if ng_reason:
        fields.append({"name": "NG理由", "value": ng_reason[:1024], "inline": False})

    risk_flags = md.get("risk_flags") or []
    if risk_flags:
        flags_ja = [_RISK_FLAG_JA.get(f, f) for f in risk_flags]
        fields.append({"name": "リスクフラグ", "value": ", ".join(flags_ja), "inline": False})

    if payload.new_plan:
        plan = payload.new_plan
        decision_raw = str(plan.get("decision_final", "?"))
        decision_ja = _DECISION_JA.get(decision_raw.strip("*").strip(), decision_raw)
        fields.append({"name": "── 新プラン ──", "value": "\u200b", "inline": False})
        fields.append({"name": "判定", "value": decision_ja, "inline": True})
        fields.append({"name": "確信度", "value": str(plan.get("confidence", "?")), "inline": True})

        alloc = plan.get("allocation_jpy")
        if alloc is not None:
            fields.append({"name": "配分額", "value": f"¥{alloc:,.0f}", "inline": True})

        qty = plan.get("quantity")
        if qty is not None:
            fields.append({"name": "数量", "value": str(qty), "inline": True})

    if payload.event_context:
        ec = payload.event_context
        event_name = ec.get("name_ja", ec.get("event_id", "?"))
        watch_kind_raw = ec.get("watch_kind", "")
        watch_kind_ja = _WATCH_KIND_JA.get(watch_kind_raw, watch_kind_raw)
        event_str = f"{event_name}"
        if watch_kind_ja:
            event_str += f"（{watch_kind_ja}）"
        fields.append({"name": "トリガイベント", "value": event_str, "inline": False})

    embed = {
        "title": title,
        "color": color,
        "fields": fields,
    }

    if payload.beginner_summary:
        embed["description"] = payload.beginner_summary

    return embed


def send_webhook(embed: dict) -> bool:
    """Discord Webhook にメッセージを送信する。"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("  [通知] DISCORD_WEBHOOK_URL 未設定 — スキップ")
        return False

    body = json.dumps({"embeds": [embed]}).encode("utf-8")
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


async def notify(payload: NotifyPayload) -> bool:
    """サマリー生成 → Embed 構築 → Webhook 送信の統合関数。"""
    if payload.label not in (NotifyLabel.ERROR, NotifyLabel.COMPLETE):
        summary = await generate_beginner_summary(payload)
        payload.beginner_summary = summary

    embed = build_embed(payload)
    return send_webhook(embed)
