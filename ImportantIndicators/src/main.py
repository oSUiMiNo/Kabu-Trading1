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


# ═══════════════════════════════════════════════════════
# 連続失敗検知 + 1日1回通知
# ═══════════════════════════════════════════════════════

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

# フィールドパスからエラー種別を抽出（例："earnings.eps_actual" → "earnings.eps"）
_ERROR_TYPE_MAP = {
    "market.vix": "market.vix",
    "market.us_10y_yield": "market.yield",
    "market.ffr": "market.ffr",
    "market.boj_rate": "market.boj",
    "earnings.eps_actual": "earnings.eps",
    "earnings.eps_estimate": "earnings.eps",
    "earnings.eps_surprise_pct": "earnings.eps",
    "earnings.latest_quarter": "earnings.eps",
    "relative_strength.vs_index_3m_pct": "relative_strength",
    "relative_strength.benchmark": "relative_strength",
    "volume.volume_ratio_5d": "volume",
    "volume.dollar_volume": "volume",
    "event_risk.nearest_event": "event_risk.event",
    "event_risk.days_to_event": "event_risk.event",
}

# 今回の実行で通知済みのエラー種別を記録（1日1回制限用）
_notified_today: dict[str, str] = {}  # {error_type: date_str}


def _get_error_type(field_path: str) -> str:
    return _ERROR_TYPE_MAP.get(field_path, field_path)


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
            .limit(2)
            .execute()
        )
        # 今回書き込んだものを除く直近を取得（limit=2 で今回分をスキップ）
        records = resp.data or []
        prev = None
        for r in records:
            prev = r.get("important_indicators", {})
            break

        if prev is None:
            return False

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


def _check_market_consecutive_failure(field_key: str) -> bool:
    """市場全体データの連続失敗チェック。前回の任意の archive と比較。"""
    try:
        resp = (
            get_client()
            .from_("archive")
            .select("important_indicators")
            .not_.is_("important_indicators", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False
        prev_market = (resp.data[0].get("important_indicators") or {}).get("market", {})
        return prev_market.get(field_key) is None
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


def _send_failure_notification(error_type: str, tickers: list[str], failed_fields: list[str]):
    """連続失敗の Discord 通知を送信（エラー種別で1日1回制限）。"""
    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    # 1日1回チェック
    if _notified_today.get(error_type) == today_str:
        return
    _notified_today[error_type] = today_str

    try:
        from discord_notifier import notify
        from notification_types import NotifyLabel, NotifyPayload

        is_market = error_type.startswith("market.")
        if is_market:
            title = "市場全体データ"
        else:
            title = f"{', '.join(tickers)}" if tickers else "不明"

        detail_lines = [
            f"エラー種別：{error_type}",
            f"失敗項目：{', '.join(failed_fields)}",
        ]
        if tickers and not is_market:
            detail_lines.insert(0, f"該当銘柄：{', '.join(tickers)}")

        payload = NotifyPayload(
            label=NotifyLabel.ERROR,
            ticker=title,
            monitor_data={},
            error_detail="重要指標の連続取得失敗\n" + "\n".join(detail_lines),
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notify(payload))
        except RuntimeError:
            asyncio.run(notify(payload))
    except Exception as e:
        print(f"  [通知警告] Discord 通知失敗: {e}")


# ═══════════════════════════════════════════════════════
# 銘柄処理
# ═══════════════════════════════════════════════════════

# エラー種別ごとに該当銘柄と失敗フィールドを蓄積（run() の最後にまとめて通知）
_failure_collector: dict[str, dict] = {}  # {error_type: {"tickers": set, "fields": set}}


def _collect_failure(error_type: str, ticker: str, field: str):
    """エラーを種別ごとに蓄積する。"""
    if error_type not in _failure_collector:
        _failure_collector[error_type] = {"tickers": set(), "fields": set()}
    _failure_collector[error_type]["tickers"].add(ticker)
    _failure_collector[error_type]["fields"].add(field)


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

    # 連続失敗検知（個別銘柄データのみ。市場全体データは run() でチェック済み）
    null_fields = _detect_null_fields(indicators)
    for field in null_fields:
        if field.startswith("market."):
            continue
        if field in _SKIP_NOTIFICATION_FIELDS:
            continue
        if _check_consecutive_failure(ticker, field):
            error_type = _get_error_type(field)
            _collect_failure(error_type, ticker, field)

    return True


def run(
    target_ticker: str | None = None,
    archive_id: str | None = None,
):
    """重要指標ブロックのメインエントリーポイント。"""
    _failure_collector.clear()

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
    for key in market_nulls:
        if _check_market_consecutive_failure(key):
            error_type = _get_error_type(f"market.{key}")
            _collect_failure(error_type, "", f"market.{key}")

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

    # Step 4: 蓄積した連続失敗をエラー種別ごとにまとめて通知（1日1回制限付き）
    if _failure_collector:
        print(f"\n--- 連続失敗検知 ---")
        for error_type, info in _failure_collector.items():
            ticker_list = sorted(info["tickers"] - {""})
            field_list = sorted(info["fields"])
            print(f"  [{error_type}] 銘柄: {ticker_list or '(市場全体)'}, 項目: {field_list}")
            _send_failure_notification(error_type, ticker_list, field_list)


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
