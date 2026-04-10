"""
ActionLog パイプライン（Phase 6）— 1銘柄処理

archive から action_log への投入と holdings 同期を行う。
Watch ブロックの後に実行される。

Usage:
    python pipeline_main.py --ticker NVDA --archive-id 20260404120000_NVDA
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_archivelog_by_id,
    list_watchlist,
    list_all_action_logs,
    get_holding,
)
from auto_populate import populate_action_log
from data_service import _sync_holdings_from_logs, _utc_to_jst_date


def process_one_ticker(ticker: str, archive_id: str) -> bool:
    print(f"  [{ticker}] ActionLog 処理開始 (archive={archive_id})")

    archivelog = safe_db(get_archivelog_by_id, archive_id)
    if not archivelog:
        print(f"  [{ticker}] archive が見つかりません。スキップ。")
        return False

    newplan_full = archivelog.get("newplan_full")
    monitor_data = archivelog.get("monitor") or {}
    mode = archivelog.get("mode", "")

    wl = safe_db(list_watchlist) or []
    wl_entry = next((w for w in wl if w["ticker"] == ticker), {})
    wl_market = wl_entry.get("market", "JP")

    tech = archivelog.get("technical") or {}
    fb_rate = tech.get("usd_jpy_rate") if isinstance(tech, dict) else None

    try:
        if newplan_full:
            result = populate_action_log(
                ticker=ticker,
                archive_id=archive_id,
                newplan_full=newplan_full,
                action_date=_utc_to_jst_date(archivelog.get("created_at")),
                fallback_market=wl_market,
                fallback_usd_jpy_rate=fb_rate,
            )
        elif mode == "review" and isinstance(monitor_data, dict) and monitor_data.get("result") == "OK":
            result = populate_action_log(
                ticker=ticker,
                archive_id=archive_id,
                monitor_data=monitor_data,
                fallback_market=wl_market,
            )
        else:
            print(f"  [{ticker}] 対象外の archive。スキップ。")
            return True

        if result:
            print(f"  [{ticker}] action_log 投入完了 (id={result.get('id')})")
        else:
            print(f"  [{ticker}] action_log スキップ（投入済み）")
    except Exception as e:
        print(f"  [{ticker}] action_log 投入失敗: {e}")

    try:
        all_logs = safe_db(list_all_action_logs, ticker) or []
        _sync_holdings_from_logs(ticker, all_logs)
        h = safe_db(get_holding, ticker) or {}
        print(f"  [{ticker}] holdings 同期: {h.get('shares', 0)}株, avg_cost={h.get('avg_cost', 0)}")
    except Exception as e:
        print(f"  [{ticker}] holdings 同期スキップ: {e}")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ActionLog パイプライン（Phase 6）: 1銘柄処理")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--archive-id", required=True)
    args = parser.parse_args()
    ok = process_one_ticker(args.ticker.upper(), args.archive_id)
    sys.exit(0 if ok else 1)
