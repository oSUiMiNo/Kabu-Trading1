"""
Technical オーケストレーター

指定された1銘柄のテクニカル指標を取得し、archive テーブルに記録する。
複数銘柄の並列実行は technical_batch.py（PJTルート）が担う。

Usage:
    python main.py --ticker AAPL                        # 既存 archive に書き込み
    python main.py --ticker AAPL --create-archive       # 新規 archive を作成
    python main.py --ticker 4755 --market JP            # 市場指定
"""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_latest_archivelog_with_newplan,
    create_archivelog,
    update_archivelog,
    get_client,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

JST = timezone(timedelta(hours=9))

MAX_RETRIES = 3


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def to_yfinance_symbol(ticker: str, market: str | None = None) -> str:
    if market == "JP" or (market is None and ticker.isdigit()):
        return f"{ticker}.T"
    return ticker


def fetch_technical(symbol: str, config: dict) -> dict:
    from technical_indicator_fetcher import fetch_and_run_with_yfinance, FetcherOptions

    timeframes = config.get("timeframes", ["1d"])
    period = config.get("period", "1y")

    options = FetcherOptions(
        indicator_profile=config.get("indicator_profile", "core"),
        pattern_profile=config.get("pattern_profile", "major_only"),
        output_format="json",
    )

    result = {"fetched_at": datetime.now(JST).isoformat(), "latest_price": None, "timeframes": {}}

    for tf in timeframes:
        data = fetch_and_run_with_yfinance(
            symbol=symbol,
            timeframe=tf,
            period=period,
            interval=tf,
            options=options,
            return_dict=True,
        )
        result["timeframes"][tf] = data
        if result["latest_price"] is None:
            result["latest_price"] = data.get("data_summary", {}).get("latest_close")

    return result


async def process_one_ticker(
    ticker: str,
    config: dict,
    market: str | None = None,
    create_archive: bool = True,
) -> dict | None:
    now = datetime.now(JST)
    print(f"  [{ticker}] テクニカル取得開始")

    archive_id = None

    if create_archive:
        latest = safe_db(get_latest_archivelog_with_newplan, ticker)
        mode = latest.get("mode", "buy") if latest else "buy"
        span = latest.get("span", "mid") if latest else "mid"

        new_record = safe_db(create_archivelog, ticker, mode, span)
        if not new_record:
            print(f"  [{ticker}] archive 作成失敗")
            return None
        archive_id = new_record["id"]
    else:
        resp = (
            get_client()
            .from_("archive")
            .select("id")
            .eq("ticker", ticker.upper())
            .is_("technical", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            print(f"  [{ticker}] 対象 archive なし（technical IS NULL が見つかりません）")
            return None
        archive_id = resp.data[0]["id"]

    yf_symbol = to_yfinance_symbol(ticker, market)
    technical_data = None
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            print(f"  [{ticker}] リトライ {attempt}/{MAX_RETRIES}")
        try:
            technical_data = await asyncio.to_thread(fetch_technical, yf_symbol, config)
            break
        except Exception as e:
            last_error = str(e)
            print(f"  [{ticker}] 取得失敗: {last_error}")

    if technical_data is None:
        print(f"  [{ticker}] リトライ上限到達 — エラーとして記録")
        error_data = {
            "fetched_at": now.isoformat(),
            "error": f"リトライ {MAX_RETRIES} 回失敗: {last_error}",
        }
        safe_db(update_archivelog, archive_id, technical=error_data)
        await _notify_error(ticker, last_error)
        return error_data

    safe_db(update_archivelog, archive_id, technical=technical_data)
    print(f"  [{ticker}] テクニカル取得完了")
    return technical_data


async def _notify_error(ticker: str, error_detail: str):
    try:
        from notification_types import NotifyLabel, NotifyPayload
        from discord_notifier import notify

        payload = NotifyPayload(
            label=NotifyLabel.ERROR,
            ticker=ticker,
            monitor_data={},
            error_detail=f"Technical 取得失敗: {error_detail}",
        )
        await notify(payload)
    except Exception as e:
        print(f"  [{ticker}] Discord 通知失敗: {e}")


async def run_single(ticker: str, market: str | None = None, create_archive: bool = False):
    """1銘柄のテクニカル指標を取得して archive に記録する。"""
    config = load_config()
    ticker = ticker.upper()
    print(f"  [{ticker}] Technical 開始")
    result = await process_one_ticker(ticker, config, market=market, create_archive=create_archive)
    if result and "error" not in result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    _ticker = None
    _market = None
    _create_archive = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            _ticker = args[i + 1]
            i += 2
        elif args[i] == "--market" and i + 1 < len(args):
            _market = args[i + 1]
            i += 2
        elif args[i] == "--create-archive":
            _create_archive = True
            i += 1
        else:
            _ticker = args[i]
            i += 1

    if not _ticker:
        print("エラー: --ticker は必須です。バッチ実行は technical_batch.py を使用してください。")
        sys.exit(1)

    asyncio.run(run_single(_ticker, _market, _create_archive))
