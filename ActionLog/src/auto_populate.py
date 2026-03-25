"""
archive → action_log 変換モジュール

archive.newplan_full（YAML）を解析し、action_log テーブルに投入するデータに変換する。
Watch 連携（Step 8）と既存 archive 一括取り込みの両方で使う。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    create_action_log,
    list_all_action_logs,
    get_latest_action_log,
    list_action_log_archive_ids,
)
from calc_engine import calc_total_shares

_JST = timezone(timedelta(hours=9))

_SELL_DECISIONS = {"SELL", "REDUCE"}
_HOLD_DECISIONS = {"HOLD", "NO_BUY", "NOT_BUY_WAIT"}

_ACTION_TEXT_MAP = {
    "SELL": "売った",
    "REDUCE": "一部売った",
    "HOLD": "様子見",
    "NO_BUY": "保留",
    "NOT_BUY_WAIT": "保留",
}


def parse_newplan_full(yaml_str: str) -> dict:
    """newplan_full の YAML 文字列をパースして必要なフィールドを抽出する。"""
    try:
        data = yaml.safe_load(yaml_str) if yaml_str else {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    decision_block = data.get("decision", {})
    portfolio = data.get("portfolio_plan", {})
    checks = data.get("data_checks", {})

    return {
        "decision": decision_block.get("final", ""),
        "allocation_jpy": portfolio.get("allocation_jpy"),
        "quantity": portfolio.get("quantity", 0),
        "current_price": checks.get("current_price"),
        "confidence": decision_block.get("confidence", ""),
        "horizon": decision_block.get("horizon", ""),
    }


def build_action_text(decision: str, allocation_jpy: float | None) -> str:
    """decision と配分額からアクションログ短文を生成する。"""
    decision_upper = (decision or "").upper().strip("*").strip()

    if decision_upper in _ACTION_TEXT_MAP:
        return _ACTION_TEXT_MAP[decision_upper]

    if allocation_jpy and allocation_jpy > 0:
        amount = f"{int(allocation_jpy):,}"
        if decision_upper == "BUY":
            return f"{amount}円買った"
        if decision_upper == "ADD":
            return f"{amount}円買い増した"

    return decision_upper or "不明"


def _calc_money_in(decision: str, allocation_jpy: float | None) -> float:
    """decision に基づいて money_in の符号を決定する。"""
    decision_upper = (decision or "").upper().strip("*").strip()
    amount = float(allocation_jpy or 0)

    if decision_upper in _SELL_DECISIONS:
        return -amount
    if decision_upper in _HOLD_DECISIONS:
        return 0.0
    return amount


def build_action_log_row(
    ticker: str,
    archive_id: str,
    newplan_full: str,
    beginner_summary: str = "",
    action_date: str | None = None,
) -> dict:
    """archive の情報から action_log に INSERT するための行データを構築する。

    cumulative_invested / total_assets / pnl は含まない（呼び出し側でセット）。
    """
    parsed = parse_newplan_full(newplan_full)
    decision = parsed["decision"]

    return {
        "ticker": ticker.upper(),
        "archive_id": archive_id,
        "action_date": action_date or datetime.now(_JST).strftime("%Y-%m-%d"),
        "action_text": build_action_text(decision, parsed["allocation_jpy"]),
        "story": beginner_summary,
        "decision": decision,
        "quantity": parsed["quantity"] or 0,
        "price": parsed["current_price"],
        "money_in": _calc_money_in(decision, parsed["allocation_jpy"]),
        "is_auto": True,
    }


def populate_from_archive(
    ticker: str,
    archive_id: str,
    newplan_full: str,
    beginner_summary: str = "",
    action_date: str | None = None,
) -> dict | None:
    """archive → action_log への変換＋INSERT を一括で行う。

    既存投入済みなら None を返す（二重投入防止）。
    cumulative_invested / total_assets / pnl も計算してセットする。
    """
    existing_ids = safe_db(list_action_log_archive_ids, ticker) or []
    if archive_id in existing_ids:
        return None

    row = build_action_log_row(
        ticker, archive_id, newplan_full, beginner_summary, action_date
    )

    all_logs = safe_db(list_all_action_logs, ticker) or []
    total_shares = calc_total_shares(all_logs)

    latest = safe_db(get_latest_action_log, ticker)
    prev_cumulative = float(latest["cumulative_invested"]) if latest else 0.0

    row["cumulative_invested"] = prev_cumulative + row["money_in"]

    new_quantity = row["quantity"]
    decision_upper = (row["decision"] or "").upper().strip("*").strip()
    if decision_upper in _SELL_DECISIONS:
        shares_after = total_shares - new_quantity
    else:
        shares_after = total_shares + new_quantity

    price = float(row["price"] or 0)
    row["total_assets"] = price * shares_after
    row["pnl"] = row["total_assets"] - row["cumulative_invested"]

    result = safe_db(
        create_action_log,
        row.pop("ticker"),
        row.pop("action_date"),
        archive_id=row.pop("archive_id"),
        **row,
    )
    return result
