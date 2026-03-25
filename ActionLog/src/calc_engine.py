"""
自動計算エンジン

ユーザーがセルを編集したとき、関連する行の値を連鎖的に再計算する。
DB には依存せず、行データのリスト（list[dict]）を受け取って計算結果を返す。
"""
from __future__ import annotations

import copy

_SELL_DECISIONS = {"SELL", "REDUCE"}


def recalculate_all(rows: list[dict]) -> list[dict]:
    """全行の cumulative_invested と pnl を先頭から順に再計算する。

    rows は action_date 昇順であること。元のリストは変更せずコピーを返す。
    """
    result = copy.deepcopy(rows)
    cumulative = 0.0
    for row in result:
        cumulative += float(row.get("money_in") or 0)
        row["cumulative_invested"] = cumulative
        row["pnl"] = calc_pnl(row)
    return result


def recalculate_from(rows: list[dict], start_index: int) -> list[dict]:
    """start_index 以降の行の cumulative_invested と pnl を再計算する。

    start_index より前の行は変更しない。
    """
    result = copy.deepcopy(rows)
    if not result or start_index >= len(result):
        return result

    if start_index > 0:
        cumulative = float(result[start_index - 1].get("cumulative_invested") or 0)
    else:
        cumulative = 0.0

    for row in result[start_index:]:
        cumulative += float(row.get("money_in") or 0)
        row["cumulative_invested"] = cumulative
        row["pnl"] = calc_pnl(row)
    return result


def calc_pnl(row: dict) -> float:
    """1行の pnl を計算: total_assets - cumulative_invested。

    total_assets が None の場合は 0 として扱う。
    """
    total_assets = float(row.get("total_assets") or 0)
    cumulative = float(row.get("cumulative_invested") or 0)
    return total_assets - cumulative


def calc_total_shares(rows: list[dict]) -> int:
    """全行の quantity を合算して保有株数累計を返す。

    BUY/ADD は正、SELL/REDUCE は負として扱う。
    """
    total = 0
    for row in rows:
        qty = int(row.get("quantity") or 0)
        if row.get("decision") in _SELL_DECISIONS:
            total -= qty
        else:
            total += qty
    return total


def calc_total_assets(price: float, rows: list[dict]) -> float:
    """price x 保有株数累計 を計算。"""
    return price * calc_total_shares(rows)
