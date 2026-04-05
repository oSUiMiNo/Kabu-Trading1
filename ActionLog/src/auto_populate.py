"""
archive → action_log 変換モジュール

archive.newplan_full（YAML）または monitor 結果を解析し、
action_log テーブルに投入するデータに変換する。
"""
from __future__ import annotations

import json
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


def _calc_money_in(
    decision: str,
    quantity: int,
    price: float,
    usd_jpy: float,
    is_us: bool,
) -> float:
    """実際に株に使った金額を計算する。株数 × 株価 × 為替。"""
    decision_upper = _normalize_decision(decision)

    if decision_upper not in _KNOWN_DECISIONS:
        return 0.0
    if decision_upper in _HOLD_DECISIONS:
        return 0.0

    actual_cost = price * quantity * (usd_jpy if is_us else 1.0)

    if decision_upper in _SELL_DECISIONS:
        return -actual_cost
    return actual_cost


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

    qty = (parsed["quantity"] or 0) if _normalize_decision(decision) in ("BUY", "ADD", "SELL", "REDUCE") else 0
    price = float(parsed["current_price"] or 0)
    usd_jpy = float(parsed.get("usd_jpy_rate") or 0)
    market = parsed.get("market", "JP")
    is_us = market == "US" and usd_jpy > 0

    return {
        "ticker": ticker.upper(),
        "archive_id": archive_id,
        "action_date": action_date or datetime.now(_JST).strftime("%Y-%m-%d"),
        "action_text": build_action_text(decision, parsed["allocation_jpy"]),
        "story": beginner_summary,
        "decision": decision,
        "quantity": qty if _normalize_decision(decision) in ("BUY", "ADD", "SELL", "REDUCE") else 0,
        "price": parsed["current_price"],
        "money_in": int(_calc_money_in(decision, qty, price, usd_jpy, is_us)),
        "is_auto": True,
        "_parsed_market": market,
        "_parsed_usd_jpy": usd_jpy,
        "_parsed_is_us": is_us,
    }


_MONITOR_RESULT_TEXT = {
    "OK": "OK",
    "NG": "要注意",
    "ERROR": "チェック失敗",
}


def _insert_with_calc(
    ticker: str,
    archive_id: str,
    row: dict,
    market: str,
    usd_jpy: float,
) -> dict | None:
    """累積計算 + INSERT 処理。"""
    existing_ids = safe_db(list_action_log_archive_ids, ticker) or []
    if archive_id in existing_ids:
        return None

    is_us = market == "US" and usd_jpy > 0

    holding = safe_db(get_holding, ticker) or {}
    holding_shares = int(holding.get("shares") or 0)
    holding_avg_cost = float(holding.get("avg_cost") or 0)

    latest = safe_db(get_latest_action_log, ticker)
    if latest:
        prev_cumulative = float(latest["cumulative_invested"])
    else:
        if is_us:
            prev_cumulative = int(holding_avg_cost * holding_shares * usd_jpy)
        else:
            prev_cumulative = int(holding_avg_cost * holding_shares)

    all_logs = safe_db(list_all_action_logs, ticker) or []
    log_shares = calc_total_shares(all_logs)

    new_quantity = row.get("quantity", 0)
    decision_upper = _normalize_decision(row.get("decision", ""))
    if decision_upper in _SELL_DECISIONS:
        log_shares_after = log_shares - new_quantity
    else:
        log_shares_after = log_shares + new_quantity
    total_shares = log_shares_after

    price = float(row.get("price") or 0)
    if is_us:
        total_assets = int(price * total_shares * usd_jpy)
    else:
        total_assets = int(price * total_shares)

    money_in = row.get("money_in", 0)
    cumulative_invested = int(prev_cumulative + money_in)

    result = safe_db(
        create_action_log,
        ticker,
        row.get("action_date") or datetime.now(_JST).strftime("%Y-%m-%d"),
        archive_id=archive_id,
        action_text=row.get("action_text", ""),
        story=row.get("story", ""),
        decision=row.get("decision", ""),
        quantity=new_quantity,
        price=row.get("price"),
        money_in=money_in,
        cumulative_invested=cumulative_invested,
        total_assets=total_assets,
        pnl=int(total_assets - cumulative_invested),
        is_auto=row.get("is_auto", True),
    )
    return result


def populate_action_log(
    ticker: str,
    archive_id: str,
    newplan_full: str | None = None,
    monitor_data: dict | None = None,
    beginner_summary: str = "",
    action_date: str | None = None,
    fallback_market: str = "JP",
    fallback_usd_jpy_rate: float | None = None,
) -> dict | None:
    """archive または monitor 結果から action_log に1行投入する統合関数。

    newplan_full がある場合は YAML をパースして売買記録を作成。
    なければ monitor_data から経過記録（OK/NG）を作成。
    """
    if newplan_full:
        row = build_action_log_row(
            ticker, archive_id, newplan_full, beginner_summary, action_date
        )
        market = row.pop("_parsed_market", fallback_market)
        parsed_usd_jpy = row.pop("_parsed_usd_jpy", 0)
        row.pop("_parsed_is_us", None)
        usd_jpy = parsed_usd_jpy or float(fallback_usd_jpy_rate or 0)
        is_us = market == "US" and usd_jpy > 0
        if not parsed_usd_jpy and is_us:
            row["money_in"] = int(_calc_money_in(
                row["decision"], row["quantity"], float(row["price"] or 0), usd_jpy, True,
            ))
    else:
        md = monitor_data or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (json.JSONDecodeError, TypeError):
                md = {}
        if not isinstance(md, dict):
            md = {}
        result_str = md.get("result", "OK")
        row = {
            "action_date": action_date or datetime.now(_JST).strftime("%Y-%m-%d"),
            "action_text": _MONITOR_RESULT_TEXT.get(result_str, f"監視結果: {result_str}"),
            "story": md.get("summary", ""),
            "decision": result_str,
            "quantity": 0,
            "price": md.get("current_price") or 0,
            "money_in": 0,
            "is_auto": True,
        }
        market = fallback_market
        usd_jpy = float(fallback_usd_jpy_rate or 0)

    return _insert_with_calc(ticker, archive_id, row, market, usd_jpy)
