"""
archive → action_log 変換モジュール

archive.newplan_full（YAML）を解析し、action_log テーブルに投入するデータに変換する。
Watch 連携（Step 8）と既存 archive 一括取り込みの両方で使う。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import re
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    create_action_log,
    list_all_action_logs,
    get_latest_action_log,
    list_action_log_archive_ids,
    get_holding,
)
from calc_engine import calc_total_shares

_JST = timezone(timedelta(hours=9))

_SELL_DECISIONS = {"SELL", "REDUCE"}
_HOLD_DECISIONS = {"HOLD", "NO_BUY", "NOT_BUY_WAIT"}

def _normalize_decision(raw: str) -> str:
    """decision 値から括弧・記号を除去して正規化する。"""
    return re.sub(r'[（）\(\)\*\s]', '', raw or '').strip().upper()


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

    instrument = portfolio.get("instrument_lot", {})

    return {
        "decision": decision_block.get("final", ""),
        "allocation_jpy": portfolio.get("allocation_jpy"),
        "quantity": portfolio.get("quantity", 0),
        "current_price": checks.get("current_price"),
        "confidence": decision_block.get("confidence", ""),
        "horizon": decision_block.get("horizon", ""),
        "usd_jpy_rate": portfolio.get("usd_jpy_rate"),
        "market": instrument.get("market", "JP"),
    }


def build_action_text(decision: str, allocation_jpy: float | None) -> str:
    """decision と配分額からアクションログ短文を生成する。"""
    decision_upper = _normalize_decision(decision)

    if decision_upper in _ACTION_TEXT_MAP:
        return _ACTION_TEXT_MAP[decision_upper]

    if allocation_jpy and allocation_jpy > 0:
        amount = f"{int(allocation_jpy):,}"
        if decision_upper == "BUY":
            return f"{amount}円買った"
        if decision_upper == "ADD":
            return f"{amount}円買い増した"

    return "不明"

_KNOWN_DECISIONS = {"BUY", "SELL", "ADD", "REDUCE", "HOLD", "NO_BUY", "NOT_BUY_WAIT"}


def _calc_money_in(decision: str, allocation_jpy: float | None) -> float:
    """decision に基づいて money_in の符号を決定する。"""
    decision_upper = _normalize_decision(decision)
    amount = float(allocation_jpy or 0)

    if decision_upper not in _KNOWN_DECISIONS:
        return 0.0
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
        "quantity": (parsed["quantity"] or 0) if _normalize_decision(decision) in ("BUY", "ADD") else 0,
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
    fallback_market: str = "JP",
    fallback_usd_jpy_rate: float | None = None,
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

    holding = safe_db(get_holding, ticker) or {}
    actual_shares = int(holding.get("shares") or 0)

    latest = safe_db(get_latest_action_log, ticker)
    parsed = parse_newplan_full(newplan_full)
    market = parsed.get("market") or fallback_market
    usd_jpy = float(parsed.get("usd_jpy_rate") or 0) or float(fallback_usd_jpy_rate or 0)
    is_us = market == "US" and usd_jpy > 0

    if latest:
        prev_cumulative = float(latest["cumulative_invested"])
    else:
        prev_cumulative = 0.0

    row["cumulative_invested"] = prev_cumulative + row["money_in"]

    price = float(row["price"] or 0)
    if is_us:
        row["total_assets"] = price * actual_shares * usd_jpy
    else:
        row["total_assets"] = price * actual_shares
    row["pnl"] = row["total_assets"] - row["cumulative_invested"]

    result = safe_db(
        create_action_log,
        row.pop("ticker"),
        row.pop("action_date"),
        archive_id=row.pop("archive_id"),
        **row,
    )
    return result


_MONITOR_RESULT_TEXT = {
    "OK": "OK",
    "NG": "要注意",
    "ERROR": "チェック失敗",
}


def populate_from_monitor(
    ticker: str,
    archive_id: str,
    monitor_data: dict,
    action_date: str | None = None,
    market: str = "JP",
    usd_jpy_rate: float | None = None,
) -> dict | None:
    """monitor 結果のみ（newplan_full なし）の archive から action_log 行を作成する。"""
    if isinstance(monitor_data, str):
        try:
            import json
            monitor_data = json.loads(monitor_data)
        except (json.JSONDecodeError, TypeError):
            monitor_data = {}
    if not isinstance(monitor_data, dict):
        monitor_data = {}

    existing_ids = safe_db(list_action_log_archive_ids, ticker) or []
    if archive_id in existing_ids:
        return None

    result_str = monitor_data.get("result", "OK")
    action_text = _MONITOR_RESULT_TEXT.get(result_str, f"監視結果: {result_str}")
    summary = monitor_data.get("summary", "")

    holding = safe_db(get_holding, ticker) or {}
    actual_shares = int(holding.get("shares") or 0)

    latest = safe_db(get_latest_action_log, ticker)
    if latest:
        prev_cumulative = float(latest["cumulative_invested"])
    else:
        prev_cumulative = 0.0

    price = monitor_data.get("current_price") or 0
    usd_jpy = float(usd_jpy_rate or 0)
    if market == "US" and usd_jpy > 0 and price:
        total_assets = float(price) * actual_shares * usd_jpy
    else:
        total_assets = float(price) * actual_shares if price else 0.0

    row_result = safe_db(
        create_action_log,
        ticker,
        action_date or datetime.now(_JST).strftime("%Y-%m-%d"),
        archive_id=archive_id,
        action_text=action_text,
        story=summary,
        decision=result_str,
        quantity=0,
        price=price,
        money_in=0,
        cumulative_invested=prev_cumulative,
        total_assets=total_assets,
        pnl=total_assets - prev_cumulative,
        is_auto=True,
    )
    return row_result
