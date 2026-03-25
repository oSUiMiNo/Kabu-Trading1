"""
UI 向けデータ操作レイヤー

UI（main.py）から呼ばれる統合レイヤー。
CRUD 関数、計算ロジック、変換ロジックを組み合わせて
UI が必要とするデータ操作を提供する。
"""
from __future__ import annotations

import calendar
import sys
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
)
from calc_engine import recalculate_from, calc_pnl
from auto_populate import populate_from_archive


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
        return updated_rows

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
        return all_rows

    else:
        safe_db(
            update_action_log,
            log_id,
            **{field: new_value},
            user_overrides=overrides,
        )
        all_rows[edit_index] = row
        return all_rows


def populate_existing_archives(ticker: str) -> int:
    """既存の archive から未投入分を一括で action_log に取り込む。

    戻り値は投入件数。
    """
    existing_ids = set(safe_db(list_action_log_archive_ids, ticker) or [])

    resp = (
        get_client()
        .from_("archive")
        .select("id, ticker, created_at, newplan_full")
        .eq("ticker", ticker.upper())
        .not_.is_("newplan_full", "null")
        .order("created_at")
        .execute()
    )
    archives = resp.data or []

    count = 0
    for arc in archives:
        if arc["id"] in existing_ids:
            continue
        action_date = arc["created_at"][:10] if arc.get("created_at") else None
        result = populate_from_archive(
            ticker=ticker,
            archive_id=arc["id"],
            newplan_full=arc["newplan_full"],
            beginner_summary="",
            action_date=action_date,
        )
        if result:
            count += 1
    return count


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
