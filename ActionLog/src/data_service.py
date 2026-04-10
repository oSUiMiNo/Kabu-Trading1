"""
UI 向けデータ操作レイヤー

UI（main.py）から呼ばれる統合レイヤー。
CRUD 関数、計算ロジック、変換ロジックを組み合わせて
UI が必要とするデータ操作を提供する。
"""
from __future__ import annotations

import calendar
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_client,
    list_action_logs,
    list_all_action_logs,
    update_action_log,
    list_watchlist,
    list_action_log_archive_ids,
    get_holding,
    upsert_holding,
)
from calc_engine import recalculate_from, calc_pnl, calc_total_shares
from auto_populate import populate_action_log

_JST = timezone(timedelta(hours=9))


def _utc_to_jst_date(created_at: str | None) -> str | None:
    """UTC の created_at 文字列を JST の日付（YYYY-MM-DD）に変換する。"""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at)
        return dt.astimezone(_JST).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return created_at[:10] if len(created_at) >= 10 else None


def get_monthly_data(ticker: str, year: int, month: int) -> list[dict]:
    """指定銘柄の月別データを取得する（UI テーブル表示用）。"""
    from_date = f"{year:04d}-{month:02d}-01"
    last_day = calendar.monthrange(year, month)[1]
    to_date = f"{year:04d}-{month:02d}-{last_day:02d}"
    return safe_db(list_action_logs, ticker, from_date, to_date) or []


def handle_edit(log_id: int, ticker: str, field: str, new_value) -> list[dict]:
    """ユーザーのセル編集を処理する。

    1. 該当行を更新（user_overrides にフィールドを記録）
    2. 必要なら連鎖再計算
    3. 変更された全行を DB に保存
    4. 更新後の全行を返す
    """
    all_rows = safe_db(list_all_action_logs, ticker) or []
    if not all_rows:
        return []

    edit_index = None
    for i, row in enumerate(all_rows):
        if row["id"] == log_id:
            edit_index = i
            break

    if edit_index is None:
        return all_rows

    row = all_rows[edit_index]

    overrides = row.get("user_overrides") or {}
    overrides[field] = True

    if field in ("money_in", "total_assets"):
        new_value = float(new_value or 0)

    row[field] = new_value
    row["user_overrides"] = overrides

    updated_rows = all_rows
    if field == "money_in":
        updated_rows = recalculate_from(all_rows, edit_index)
        for i in range(edit_index, len(updated_rows)):
            r = updated_rows[i]
            safe_db(
                update_action_log,
                r["id"],
                money_in=r["money_in"] if i == edit_index else r.get("money_in"),
                cumulative_invested=r["cumulative_invested"],
                pnl=r["pnl"],
                user_overrides=overrides if i == edit_index else r.get("user_overrides"),
            )

    elif field == "total_assets":
        row["pnl"] = calc_pnl(row)
        safe_db(
            update_action_log,
            log_id,
            total_assets=new_value,
            pnl=row["pnl"],
            user_overrides=overrides,
        )
        all_rows[edit_index] = row

    else:
        safe_db(
            update_action_log,
            log_id,
            **{field: new_value},
            user_overrides=overrides,
        )
        all_rows[edit_index] = row

    _sync_holdings_from_logs(ticker, updated_rows)
    return updated_rows


_SELL_DECISIONS = {"SELL", "REDUCE"}


def _sync_holdings_from_logs(ticker: str, rows: list[dict]):
    """action_log の全行から holdings の shares と avg_cost を再計算して同期する。"""
    total_shares = 0
    total_cost = 0.0
    for r in rows:
        qty = int(r.get("quantity") or 0)
        if not qty:
            continue
        decision = (r.get("decision") or "").upper().strip("（） ()*")
        price = float(r.get("price") or 0)
        if decision in _SELL_DECISIONS:
            if total_shares > 0:
                avg_at_sell = total_cost / total_shares
                total_cost -= avg_at_sell * qty
            total_shares = max(0, total_shares - qty)
        else:
            total_cost += price * qty
            total_shares += qty

    if total_shares > 0:
        avg_cost = round(total_cost / total_shares, 4)
        safe_db(upsert_holding, ticker, shares=total_shares, avg_cost=avg_cost)
    else:
        holding = safe_db(get_holding, ticker)
        if holding:
            safe_db(upsert_holding, ticker, shares=0, avg_cost=0)


def populate_existing_archives(ticker: str) -> int:
    """既存の archive から未投入分を一括で action_log に取り込む。

    戻り値は投入件数。
    """
    existing_ids = set(safe_db(list_action_log_archive_ids, ticker) or [])

    wl = (
        get_client()
        .from_("watchlist")
        .select("market")
        .eq("ticker", ticker.upper())
        .limit(1)
        .execute()
    )
    watchlist_market = (wl.data[0].get("market") or "JP") if wl.data else "JP"

    fallback_usd_jpy = None
    if watchlist_market == "US":
        rate_q = (
            get_client()
            .from_("archive")
            .select("technical")
            .not_.is_("technical", "null")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        for r in (rate_q.data or []):
            t = r.get("technical") or {}
            if isinstance(t, dict) and t.get("usd_jpy_rate"):
                fallback_usd_jpy = float(t["usd_jpy_rate"])
                break
        if not fallback_usd_jpy:
            fallback_usd_jpy = 150.0
            print(f"  [WARNING] {ticker}: USD/JPY レートが見つかりません。デフォルト 150.0 を使用")

    resp = (
        get_client()
        .from_("archive")
        .select("id, ticker, created_at, newplan_full, monitor, technical")
        .eq("ticker", ticker.upper())
        .not_.is_("monitor", "null")
        .order("created_at")
        .execute()
    )
    archives = resp.data or []

    count = 0
    for arc in archives:
        if arc["id"] in existing_ids:
            continue

        mon = arc.get("monitor") or {}
        if isinstance(mon, dict) and mon.get("result") == "ERROR":
            continue

        action_date = _utc_to_jst_date(arc.get("created_at"))

        tech = arc.get("technical") or {}
        arc_rate = tech.get("usd_jpy_rate") if isinstance(tech, dict) else None
        result = populate_action_log(
            ticker=ticker,
            archive_id=arc["id"],
            newplan_full=arc.get("newplan_full"),
            monitor_data=mon,
            action_date=action_date,
            fallback_market=watchlist_market,
            fallback_usd_jpy_rate=arc_rate or fallback_usd_jpy,
        )
        if result:
            count += 1
    return count


def auto_populate_all() -> int:
    """watchlist の全 active 銘柄について未取り込みの archive を自動取り込みする。"""
    watchlist = safe_db(list_watchlist, active_only=True) or []
    total = 0
    for w in watchlist:
        t = w.get("ticker")
        if t:
            total += populate_existing_archives(t)
    return total


def get_ticker_list() -> list[str]:
    """action_log に存在する銘柄一覧 + watchlist の active 銘柄を取得する。"""
    resp = (
        get_client()
        .from_("action_log")
        .select("ticker")
        .execute()
    )
    tickers = {r["ticker"] for r in (resp.data or [])}

    watchlist = safe_db(list_watchlist, active_only=True) or []
    for w in watchlist:
        tickers.add(w["ticker"])

    return sorted(tickers)


def get_available_months(ticker: str) -> list[str]:
    """指定銘柄のデータがある月一覧を取得する（"2026-03" 形式、昇順）。"""
    all_rows = safe_db(list_all_action_logs, ticker) or []
    months = set()
    for row in all_rows:
        date_str = row.get("action_date", "")
        if date_str and len(str(date_str)) >= 7:
            months.add(str(date_str)[:7])
    return sorted(months)
