"""
EventRisk 計算モジュール

EventScheduler の DB からイベント日数を計算し、
米国株のみ yfinance でオプションチェーンから Implied Move を算出する。
"""

import sys
import time
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf
from curl_cffi import requests as cffi_requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import get_client, safe_db


def _create_session():
    return cffi_requests.Session(impersonate="chrome")


def _detect_market(symbol: str) -> str:
    if symbol.endswith(".T") or symbol.replace(".T", "").isdigit():
        return "JP"
    return "US"


def fetch_nearest_event(ticker: str, market: str | None = None) -> dict:
    """
    EventScheduler の DB から、指定市場に関連する直近の将来イベントを検索。
    イベント名と今日からの日数を返す。
    """
    result = {
        "nearest_event": None,
        "days_to_event": None,
    }

    if market is None:
        market = _detect_market(ticker)

    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        resp = (
            get_client()
            .from_("event_date_time")
            .select("event_id, scheduled_at_utc")
            .gte("scheduled_at_utc", now_utc)
            .order("scheduled_at_utc")
            .limit(20)
            .execute()
        )

        if not resp.data:
            return result

        # event_master と照合して市場でフィルタ
        event_ids = list({row["event_id"] for row in resp.data})
        master_resp = (
            get_client()
            .from_("event_master")
            .select("event_id, name, region")
            .in_("event_id", event_ids)
            .execute()
        )
        master_map = {m["event_id"]: m for m in (master_resp.data or [])}

        # 市場に関連するイベントを探す（region 一致 or region が空）
        for row in resp.data:
            eid = row["event_id"]
            master = master_map.get(eid, {})
            region = master.get("region", "")
            if region and region.upper() != market.upper():
                continue

            scheduled = datetime.fromisoformat(row["scheduled_at_utc"])
            days = (scheduled - datetime.now(timezone.utc)).days
            result["nearest_event"] = master.get("name", eid)
            result["days_to_event"] = max(0, days)
            break

    except Exception as e:
        print(f"    [警告] イベント検索失敗: {e}")

    return result


def fetch_implied_move(symbol: str) -> dict:
    """
    米国株のみ：オプションチェーンから Implied Move を計算。
    日本株は null を返す。
    """
    result = {
        "implied_move_pct": None,
        "implied_move_source": None,
    }

    if _detect_market(symbol) != "US":
        return result

    session = _create_session()
    try:
        ticker_obj = yf.Ticker(symbol, session=session)

        # 満期日一覧を取得
        options_dates = ticker_obj.options
        if not options_dates:
            return result

        # 最も近い満期を選択
        nearest_expiry = options_dates[0]

        # オプションチェーンを取得
        chain = ticker_obj.option_chain(nearest_expiry)
        calls = chain.calls
        puts = chain.puts

        if calls.empty or puts.empty:
            return result

        # 現在株価を取得
        hist = ticker_obj.history(period="1d")
        if hist.empty:
            return result
        current_price = float(hist["Close"].iloc[-1])

        # ATM に最も近い strike を特定
        calls_sorted = calls.copy()
        calls_sorted["distance"] = abs(calls_sorted["strike"] - current_price)
        atm_call = calls_sorted.sort_values("distance").iloc[0]

        puts_sorted = puts.copy()
        puts_sorted["distance"] = abs(puts_sorted["strike"] - current_price)
        atm_put = puts_sorted.sort_values("distance").iloc[0]

        # mid price を計算（bid/ask が 0 の場合は lastPrice にフォールバック）
        call_bid, call_ask = float(atm_call["bid"]), float(atm_call["ask"])
        call_mid = (call_bid + call_ask) / 2 if call_bid > 0 and call_ask > 0 else float(atm_call["lastPrice"])

        put_bid, put_ask = float(atm_put["bid"]), float(atm_put["ask"])
        put_mid = (put_bid + put_ask) / 2 if put_bid > 0 and put_ask > 0 else float(atm_put["lastPrice"])

        # Implied Move %
        if current_price > 0 and call_mid > 0 and put_mid > 0:
            implied_move = (call_mid + put_mid) / current_price * 100
            result["implied_move_pct"] = round(implied_move, 2)
            result["implied_move_source"] = "options"

    except Exception as e:
        print(f"    [警告] Implied Move 計算失敗: {e}")

    return result


def fetch_event_risk(ticker: str, market: str | None = None) -> dict:
    """
    EventRisk グループのデータを一括取得。
    """
    event = fetch_nearest_event(ticker, market)
    time.sleep(random.uniform(1, 2))
    implied = fetch_implied_move(ticker)

    return {**event, **implied}


if __name__ == "__main__":
    import json

    symbol = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(f"Fetching event risk for {symbol}...")
    data = fetch_event_risk(symbol)
    print(json.dumps(data, indent=2, ensure_ascii=False))
