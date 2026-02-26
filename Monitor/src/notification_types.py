"""
通知ラベル判定ロジック + NotifyPayload データクラス

Monitor の結果をもとに、Discord 通知のラベル（緊急/朗報/警告/確認/ERROR）を分類する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NotifyLabel(Enum):
    URGENT = "緊急"
    GOOD_NEWS = "朗報"
    WARNING = "警告"
    CHECK = "確認"
    ERROR = "エラー"


LABEL_COLOR = {
    NotifyLabel.URGENT: 0xFF0000,
    NotifyLabel.GOOD_NEWS: 0x00CC00,
    NotifyLabel.WARNING: 0xFFA500,
    NotifyLabel.CHECK: 0x3498DB,
    NotifyLabel.ERROR: 0x808080,
}


@dataclass
class NotifyPayload:
    label: NotifyLabel
    ticker: str
    monitor_data: dict
    new_plan: dict | None = None
    beginner_summary: str = ""
    event_context: dict | None = None
    error_detail: str = ""


def classify_label(monitor_data: dict) -> NotifyLabel | None:
    """
    Monitor 結果から通知ラベルを判定する。

    - NG + 変動率 ≤ -10% → 緊急
    - NG + 変動率 ≥ +10% → 朗報
    - NG（上記以外）     → 警告
    - OK + risk_flags あり → 確認
    - OK + flags なし     → None（通知不要）
    - retries_exhausted   → ERROR
    """
    if monitor_data.get("retries_exhausted"):
        return NotifyLabel.ERROR

    result = monitor_data.get("result", "")

    if result == "NG":
        pct = monitor_data.get("price_change_pct")
        if pct is not None and pct <= -10:
            return NotifyLabel.URGENT
        if pct is not None and pct >= 10:
            return NotifyLabel.GOOD_NEWS
        return NotifyLabel.WARNING

    if result == "OK":
        flags = monitor_data.get("risk_flags") or []
        if flags:
            return NotifyLabel.CHECK
        return None

    return None
