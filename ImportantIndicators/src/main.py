"""
重要指標オーケストレーター

市場全体データ + 個別銘柄データを取得し、archive.important_indicators に書き込む。

Usage:
    python main.py                                    # 全銘柄一括（watchlist から取得）
    python main.py --ticker NVDA                      # 単一銘柄
    python main.py --ticker NVDA --archive-id 123     # archive 指定
"""

import asyncio
import json
import sys
import time
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_client,
    list_watchlist,
    update_archivelog,
    get_archivelog_by_id,
)

from market_data import fetch_market_data
from ticker_data import fetch_ticker_data
from event_risk import fetch_event_risk

JST = timezone(timedelta(hours=9))


def _detect_market(ticker: str) -> str:
    if ticker.endswith(".T") or ticker.replace(".T", "").isdigit():
        return "JP"
    return "US"


def _to_yfinance_symbol(ticker: str) -> str:
    if ticker.isdigit():
        return f"{ticker}.T"
    return ticker


def _find_archive_for_ticker(ticker: str) -> dict | None:
    """今日の archive で important_indicators が未記入のレコードを探す。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    try:
        resp = (
            get_client()
            .from_("archive")
            .select("id, ticker")
            .eq("ticker", ticker)
            .gte("created_at", f"{today}T00:00:00+09:00")
            .is_("important_indicators", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        print(f"  [DB警告] archive 検索失敗: {e}")
        return None


def _check_consecutive_failure(ticker: str, field_path: str) -> bool:
    """前回の同じ銘柄の archive で同じフィールドが null かチェック。"""
    try:
        resp = (
            get_client()
            .from_("archive")
            .select("id, important_indicators")
            .eq("ticker", ticker)
            .not_.is_("important_indicators", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False

        prev = resp.data[0].get("important_indicators", {})
        if prev is None:
            return False

        # field_path を辿って前回の値を確認（例："earnings.eps_actual"）
        parts = field_path.split(".")
        val = prev
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return False
        return val is None
    except Exception:
        return False


def _detect_null_fields(data: dict, prefix: str = "") -> list[str]:
    """JSON 構造から null のフィールドパスを抽出。"""
    nulls = []
    for k, v in data.items():
        path = f"{prefix}.{k}" if prefix else k
        if v is None:
            nulls.append(path)
        elif isinstance(v, dict):
            nulls.extend(_detect_null_fields(v, path))
    return nulls


# 通知しないフィールド（仕様上 null が正常なもの）
_SKIP_NOTIFICATION_FIELDS = {
    "event_risk.implied_move_pct",
    "event_risk.implied_move_source",
    "earnings.revenue_actual",
    "earnings.revenue_estimate",
    "earnings.revenue_surprise_pct",
    "relative_strength.vs_sector_3m_pct",
    "relative_strength.sector",
    "relative_strength.sector_etf",
}


def _send_failure_notification(ticker: str, failed_fields: list[str], is_market: bool = False):
    """連続失敗の Discord 通知を送信。"""
    try:
        from discord_notifier import notify
        from notification_types import NotifyLabel, NotifyPayload

        if is_market:
            title_ticker = "市場全体データ"
            label = NotifyLabel.ERROR
        else:
            title_ticker = ticker
            label = NotifyLabel.ERROR

        payload = NotifyPayload(
            label=label,
            ticker=title_ticker,
            monitor_data={},
            error_detail=f"重要指標の連続取得失敗\n失敗項目：{', '.join(failed_fields)}",
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify(payload))
        except RuntimeError:
            asyncio.run(notify(payload))
    except Exception as e:
        print(f"  [通知警告] Discord 通知失敗: {e}")


def process_one_ticker(
    ticker: str,
    archive_id: str | None,
    market_data: dict,
) -> bool:
    """1銘柄分の重要指標を取得して archive に書き込む。"""
    symbol = _to_yfinance_symbol(ticker)
    market = _detect_market(symbol)
    print(f"\n  [{ticker}] 重要指標取得開始...")

    # archive レコードを特定
    if archive_id:
        record = safe_db(get_archivelog_by_id, archive_id)
        if not record:
            print(f"  [{ticker}] archive ID {archive_id} が見つかりません")
            return False
    else:
        record = _find_archive_for_ticker(ticker)
        if not record:
            print(f"  [{ticker}] 対象の archive レコードが見つかりません（スキップ）")
            return False
        archive_id = record["id"]

    # 個別銘柄データ取得
    ticker_result = fetch_ticker_data(symbol)

    time.sleep(random.uniform(1, 2))

    event_result = fetch_event_risk(symbol, market)

    # 市場全体データ + 個別データを統合
    indicators = {
        "fetched_at": datetime.now(JST).isoformat(),
        "market": market_data,
        "event_risk": event_result,
        "earnings": ticker_result["earnings"],
        "relative_strength": ticker_result["relative_strength"],
        "volume": ticker_result["volume"],
    }

    # archive に書き込み
    result = safe_db(update_archivelog, archive_id, important_indicators=indicators)
    if result:
        print(f"  [{ticker}] archive {archive_id} に書き込み完了")
    else:
        print(f"  [{ticker}] archive 書き込み失敗")
        return False

    # 連続失敗検知（個別銘柄データのみ。市場全体データは別途チェック）
    null_fields = _detect_null_fields(indicators)
    for field in null_fields:
        if field.startswith("market."):
            continue
        if field in _SKIP_NOTIFICATION_FIELDS:
            continue
        if _check_consecutive_failure(ticker, field):
            _send_failure_notification(ticker, [field])

    return True


def run(
    target_ticker: str | None = None,
    archive_id: str | None = None,
):
    """重要指標ブロックのメインエントリーポイント。"""
    print(f"\n{'='*60}")
    print(f"=== 重要指標取得 ===")
    print(f"{'='*60}")

    # Step 1: 市場全体データを1回取得
    print("\n--- 市場全体データ取得 ---")
    market_data = fetch_market_data()
    print(f"  VIX: {market_data.get('vix')}")
    print(f"  米10年債: {market_data.get('us_10y_yield')}%")
    print(f"  FRB金利: {market_data.get('ffr')}")
    print(f"  日銀金利: {market_data.get('boj_rate')}")

    # 市場全体データの連続失敗検知
    market_nulls = [k for k, v in market_data.items() if v is None]
    if market_nulls:
        # 前回の任意の archive から market データをチェック
        try:
            prev_resp = (
                get_client()
                .from_("archive")
                .select("important_indicators")
                .not_.is_("important_indicators", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if prev_resp.data:
                prev_market = (prev_resp.data[0].get("important_indicators") or {}).get("market", {})
                consecutive_fails = [k for k in market_nulls if prev_market.get(k) is None]
                if consecutive_fails:
                    failed_paths = [f"market.{k}" for k in consecutive_fails]
                    _send_failure_notification("", failed_paths, is_market=True)
        except Exception:
            pass

    # Step 2: 対象銘柄を決定
    if target_ticker:
        tickers = [target_ticker]
    else:
        wl = safe_db(list_watchlist, active_only=True) or []
        tickers = [w["ticker"] for w in wl]

    if not tickers:
        print("\n  対象銘柄がありません。")
        return

    print(f"\n--- 個別銘柄データ取得（{len(tickers)} 銘柄）---")

    # Step 3: 各銘柄の個別データを取得
    ok_count = 0
    ng_count = 0
    for ticker in tickers:
        success = process_one_ticker(ticker, archive_id, market_data)
        if success:
            ok_count += 1
        else:
            ng_count += 1
        time.sleep(random.uniform(1, 3))

    print(f"\n--- 結果 ---")
    print(f"  成功: {ok_count} / 失敗: {ng_count} / 合計: {len(tickers)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    _ticker = None
    _archive_id = None

    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            _ticker = args[i + 1]
            i += 2
        elif args[i] == "--archive-id" and i + 1 < len(args):
            _archive_id = args[i + 1]
            i += 2
        else:
            _ticker = args[i]
            i += 1

    run(_ticker, _archive_id)
