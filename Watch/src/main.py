"""
Watch オーケストレーター

指定された1銘柄の archive を読み取り、watchlist を更新し、Discord 通知を送信する。
複数銘柄の並列実行は watch_batch.py（PJTルート）が担う。

Usage:
    python main.py --ticker NVDA
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    update_watchlist,
    update_archivelog,
    get_archivelog_by_id,
    get_latest_archivelog_with_newplan,
    get_previous_archivelog_with_newplan,
    list_watchlist,
)
from notification_types import NotifyPayload, classify_label
from discord_notifier import notify

from AgentUtil import call_agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"


def _parse_yaml_block(text: str) -> dict | None:
    m = re.search(r"```ya?ml\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None


def _build_new_plan_dict(newplan_full: str, verdict: str) -> dict:
    parsed = yaml.safe_load(newplan_full) if newplan_full else {}
    if not isinstance(parsed, dict):
        parsed = {}
    decision = parsed.get("decision", {})
    portfolio = parsed.get("portfolio_plan", {})
    return {
        "decision_final": verdict,
        "confidence": decision.get("confidence", "?"),
        "allocation_jpy": portfolio.get("allocation_jpy"),
        "quantity": portfolio.get("quantity"),
        "yaml_full": newplan_full,
    }


def _build_summarizer_prompt(
    ticker: str,
    archivelog: dict,
    prev_archivelog: dict | None,
) -> str:
    monitor = archivelog.get("monitor") or {}
    final_judge = archivelog.get("final_judge") or {}
    newplan_full = archivelog.get("newplan_full") or ""
    verdict = archivelog.get("verdict") or ""

    parts = [
        f"# {ticker} の watchlist サマリーを生成してください\n",
        f"## 最終判定\n{verdict}\n",
    ]

    if isinstance(final_judge, dict):
        fj_text = final_judge.get("final_judge_md") or json.dumps(final_judge, ensure_ascii=False, indent=2)
    else:
        fj_text = str(final_judge)
    parts.append(f"## 議論の最終判定詳細\n{fj_text[:2000]}\n")

    if monitor:
        parts.append(f"## 監視結果\n```json\n{json.dumps(monitor, ensure_ascii=False, indent=2)}\n```\n")

    if newplan_full:
        parts.append(f"## 新プラン\n```yaml\n{newplan_full[:2000]}\n```\n")

    if prev_archivelog:
        prev_plan = prev_archivelog.get("newplan_full") or ""
        if prev_plan:
            parts.append(f"## 旧プラン（比較用）\n```yaml\n{prev_plan[:2000]}\n```\n")
    else:
        parts.append("## 旧プラン\nなし（初回プラン）\n")

    return "\n".join(parts)


async def process_one_ticker(ticker: str, archive_id: str | None = None) -> bool:
    print(f"\n{'='*50}")
    print(f"[Watch] {ticker} 処理開始")
    print(f"{'='*50}")

    if archive_id:
        archivelog = safe_db(get_archivelog_by_id, archive_id)
    else:
        archivelog = safe_db(get_latest_archivelog_with_newplan, ticker)
    if not archivelog:
        print(f"  [{ticker}] newplan_full 付きアーカイブログが見つかりません。スキップ。")
        return False

    archivelog_id = archivelog["id"]
    newplan_full = archivelog.get("newplan_full") or ""
    verdict = archivelog.get("verdict") or ""
    monitor_data = archivelog.get("monitor") or {}

    print(f"  archive id={archivelog_id}, verdict={verdict}")

    prev_archivelog = safe_db(get_previous_archivelog_with_newplan, ticker, archivelog_id)

    print(f"  サマリー生成中...")
    prompt = _build_summarizer_prompt(ticker, archivelog, prev_archivelog)
    agent_file = AGENTS_DIR / "watch-summarizer.md"
    result = await call_agent(prompt, file_path=str(agent_file), show_cost=True)

    discussion_summary = ""
    new_plan_summary = ""
    plan_comparison = ""

    if result and result.text:
        parsed = _parse_yaml_block(result.text)
        if parsed:
            ws = parsed.get("watch_summary", parsed)
            discussion_summary = ws.get("discussion_summary", "")
            new_plan_summary = ws.get("new_plan_summary", "")
            plan_comparison = ws.get("plan_comparison", "")
            print(f"  サマリー生成完了")
        else:
            print(f"  警告: YAML パース失敗。サマリーなしで続行。")
    else:
        print(f"  警告: エージェント応答なし。サマリーなしで続行。")

    update_fields = {
        "MotivationID": archivelog.get("MotivationID"),
        "motivation_summary": monitor_data.get("ng_reason") or monitor_data.get("summary", ""),
        "discussion_result": verdict,
        "discussion_summary": discussion_summary,
        "new_plan_summary": new_plan_summary,
        "risk_flags": monitor_data.get("risk_flags", []),
        "plan_comparison": plan_comparison,
        "stock_price": monitor_data.get("current_price"),
        "latest_archive_id": archivelog_id,
    }
    safe_db(update_watchlist, ticker, **update_fields)
    print(f"  [{ticker}] watchlist 更新完了")

    wl = safe_db(list_watchlist)

    # ── Discord 通知（保有状況に応じてラベル判定） ──
    if monitor_data:
        mode = archivelog.get("mode", "")
        label = classify_label(monitor_data, mode=mode)
        if label:
            event_context = None
            event_raw = os.environ.get("EVENT_CONTEXT", "")
            if event_raw:
                try:
                    event_context = json.loads(event_raw)
                except (ValueError, TypeError):
                    pass
            new_plan = _build_new_plan_dict(newplan_full, verdict)
            dn = next((w.get("display_name") or ticker for w in wl if w["ticker"] == ticker), ticker)
            payload = NotifyPayload(
                label=label,
                ticker=ticker,
                monitor_data=monitor_data,
                new_plan=new_plan,
                event_context=event_context,
                display_name=dn,
                plan_comparison=plan_comparison,
            )
            await notify(payload)
            print(f"  [{ticker}] Discord 通知送信完了 (label={label.value})")
        else:
            print(f"  [{ticker}] 通知不要（mode={mode}, result={monitor_data.get('result', '?')}）")
    else:
        print(f"  [{ticker}] monitor データなし。通知スキップ。")

    safe_db(update_archivelog, archivelog["id"], active=False)
    print(f"  [{ticker}] 処理完了（active=False）")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watch ブロック：watchlist 更新 + Discord 通知")
    parser.add_argument("--ticker", required=True, help="処理対象の銘柄")
    parser.add_argument("--archive-id", default=None, help="対象の archive ID")
    args = parser.parse_args()
    ok = anyio.run(lambda: process_one_ticker(args.ticker.upper(), archive_id=args.archive_id))
    sys.exit(0 if ok else 1)
