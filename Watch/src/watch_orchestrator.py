"""
watch_orchestrator.py — Watch ブロック本体

処理フロー:
  1. list_watchlist_with_motivation(market) で MotivationID != 0 の銘柄取得
  2. 各銘柄ループ:
     - get_latest_archivelog(ticker) で archive 読み取り
     - archive["monitor"].current_price → stockprice
     - archive["final_judge"] + archive["plan"] → watch-summarizer → discussionresult
     - update_watchlist(ticker, stockprice=..., discussionresult=..., MotivationID=0)
     - classify_label(monitor_data) → notify(NotifyPayload(...))
  3. 全完了後 notify(NotifyPayload(label=COMPLETE, ...))

Usage:
    python watch_orchestrator.py
    python watch_orchestrator.py --market US
"""
import argparse
import json
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    list_watchlist_with_motivation,
    get_latest_archivelog,
    update_watchlist,
)
from notification_types import NotifyPayload, NotifyLabel, classify_label
from discord_notifier import notify

from AgentUtil import call_agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"


def _extract_current_price(archivelog: dict) -> float | None:
    monitor = archivelog.get("monitor") or {}
    if isinstance(monitor, str):
        try:
            monitor = json.loads(monitor)
        except (ValueError, TypeError):
            return None
    return monitor.get("current_price")


def _build_summarizer_prompt(ticker: str, archivelog: dict) -> str:
    final_judge = archivelog.get("final_judge") or {}
    if isinstance(final_judge, str):
        try:
            final_judge = json.loads(final_judge)
        except (ValueError, TypeError):
            final_judge = {}

    plan = archivelog.get("plan") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (ValueError, TypeError):
            plan = {}

    decision = final_judge.get("decision_final", "不明")
    confidence = final_judge.get("confidence", "不明")
    plan_desc = plan.get("decision_final", "")

    return (
        f"銘柄：{ticker}\n"
        f"最終判定：{decision}\n"
        f"確信度：{confidence}\n"
        f"プラン判定：{plan_desc}\n"
        f"final_judge詳細：{json.dumps(final_judge, ensure_ascii=False)[:500]}"
    )


async def process_ticker(ticker: str, archivelog: dict) -> dict | None:
    """1銘柄の Watch 処理（サマリー生成 + watchlist 更新）を行い monitor_data を返す。"""
    monitor_data = archivelog.get("monitor") or {}
    if isinstance(monitor_data, str):
        try:
            monitor_data = json.loads(monitor_data)
        except (ValueError, TypeError):
            monitor_data = {}

    stockprice = _extract_current_price(archivelog)

    prompt = _build_summarizer_prompt(ticker, archivelog)
    agent_file = AGENTS_DIR / "watch-summarizer.md"
    discussionresult = ""
    if agent_file.exists():
        try:
            result = await call_agent(prompt, file_path=str(agent_file))
            discussionresult = (result.text or "").strip() if result else ""
        except Exception as e:
            print(f"  [{ticker}] サマリー生成失敗（スキップ）: {e}")

    safe_db(
        update_watchlist,
        ticker,
        stockprice=stockprice,
        discussionresult=discussionresult,
        **{"MotivationID": 0},
    )
    print(f"  [{ticker}] watchlist 更新完了 (price={stockprice})")

    return monitor_data


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "JP"])
    args = parser.parse_args()

    watchlist = safe_db(list_watchlist_with_motivation, market=args.market)
    if not watchlist:
        print("  Watch: MotivationID!=0 の銘柄なし — スキップ")
        return

    print(f"  Watch: {len(watchlist)} 銘柄を処理")

    processed_tickers = []
    ng_tickers = []

    for row in watchlist:
        ticker = row["ticker"]
        archivelog = safe_db(get_latest_archivelog, ticker)
        if not archivelog:
            print(f"  [{ticker}] archive なし — スキップ")
            continue

        monitor_data = await process_ticker(ticker, archivelog)
        processed_tickers.append(ticker)

        if not monitor_data:
            continue

        label = classify_label(monitor_data)
        if label:
            new_plan = None
            plan = archivelog.get("plan") or {}
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except (ValueError, TypeError):
                    plan = {}
            if plan:
                new_plan = {
                    "decision_final": plan.get("decision_final"),
                    "confidence": plan.get("confidence"),
                    "allocation_jpy": plan.get("allocation_jpy"),
                    "quantity": plan.get("quantity"),
                    "yaml_full": plan.get("yaml_full", ""),
                }

            payload = NotifyPayload(
                label=label,
                ticker=ticker,
                monitor_data=monitor_data,
                new_plan=new_plan,
            )
            await notify(payload)

            if monitor_data.get("result") == "NG":
                ng_tickers.append(ticker)

    complete_payload = NotifyPayload(
        label=NotifyLabel.COMPLETE,
        ticker="",
        monitor_data={
            "tickers": processed_tickers,
            "ng_tickers": ng_tickers,
        },
    )
    await notify(complete_payload)
    print(f"  Watch: 完了 ({len(processed_tickers)} 銘柄, NG: {ng_tickers})")


if __name__ == "__main__":
    anyio.run(main)
