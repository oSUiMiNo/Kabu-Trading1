"""
決定論的計算ロジック

youken.md セクション5 の固定ルールを Python で実装する。
LLM は使わない。全て確定的な計算のみ。
"""
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


# ═══════════════════════════════════════════════════════
# Enum 定義
# ═══════════════════════════════════════════════════════

class Horizon(Enum):
    SHORT = "SHORT"
    MID = "MID"
    LONG = "LONG"


class Confidence(Enum):
    HIGH = "HIGH"
    MED = "MED"
    LOW = "LOW"


class Market(Enum):
    JP = "JP"
    US = "US"


# ═══════════════════════════════════════════════════════
# 定数テーブル（youken.md セクション5 からの直接転写）
# ═══════════════════════════════════════════════════════

# 5.1 価格ズレ許容幅（アンカー価格比）
DEVIATION_OK_PCT: dict[Horizon, float] = {
    Horizon.SHORT: 3.0,
    Horizon.MID: 5.0,
    Horizon.LONG: 7.0,
}
DEVIATION_BLOCK_PCT: float = 10.0

# 5.2 ログの最大許容経過時間（鮮度）
MAX_LOG_AGE_DAYS: dict[Horizon, int] = {
    Horizon.SHORT: 2,
    Horizon.MID: 7,
    Horizon.LONG: 30,
}

# 5.3 損切り幅（期間別デフォルト）
STOP_LOSS_PCT: dict[Horizon, float] = {
    Horizon.SHORT: -4.0,
    Horizon.MID: -8.0,
    Horizon.LONG: -15.0,
}
STOP_LOSS_CAP_PCT: float = -20.0

# 5.4 予算配分の基本ルール
MAX_ALLOCATION_PCT: float = 10.0
MAX_ALLOCATION_HIGH_PCT: float = 15.0  # confidence=HIGH のみ
MIN_ALLOCATION_PCT: float = 2.0
CASH_MIN_PCT: float = 25.0

# 5.5 票差 → confidence
# p = vote_for / (vote_for + vote_against)
CONFIDENCE_THRESHOLDS: list[tuple[float, Confidence]] = [
    (5 / 6, Confidence.HIGH),  # p >= 5/6 ≈ 0.8333（例: 6-0, 5-1）
    (2 / 3, Confidence.MED),   # 2/3 <= p < 5/6（例: 4-2）
    (0.0, Confidence.LOW),     # p < 2/3（例: 3-2）
]

# 5.6 confidence → 配分倍率
CONFIDENCE_MULTIPLIER: dict[Confidence, float] = {
    Confidence.HIGH: 1.0,
    Confidence.MED: 0.6,
    Confidence.LOW: 0.3,
}

# 5.7 ロットサイズ
LOT_SIZE: dict[Market, int] = {
    Market.JP: 100,
    Market.US: 1,
}

# 7.2 monitoring intensity
MONITORING_INTENSITY: dict[Confidence, str] = {
    Confidence.HIGH: "STRONG",
    Confidence.MED: "NORMAL",
    Confidence.LOW: "LIGHT",
}


# ═══════════════════════════════════════════════════════
# 計算関数
# ═══════════════════════════════════════════════════════

@dataclass
class FreshnessResult:
    """鮮度チェック結果"""
    log_age_days: int
    max_allowed_days: int
    status: str  # "OK" | "STALE_REEVALUATE"


def check_freshness(log_date: datetime, now: datetime, horizon: Horizon) -> FreshnessResult:
    """
    ログ鮮度チェック（youken 5.2）

    log_date から now までの経過日数が MAX_LOG_AGE_DAYS を超えていれば STALE_REEVALUATE。
    """
    age = (now - log_date).days
    max_days = MAX_LOG_AGE_DAYS[horizon]
    status = "OK" if age <= max_days else "STALE_REEVALUATE"
    return FreshnessResult(log_age_days=age, max_allowed_days=max_days, status=status)


@dataclass
class DeviationResult:
    """価格ズレ判定結果"""
    anchor_price: float
    current_price: float
    price_deviation_pct: float
    deviation_ok_pct: float
    deviation_block_pct: float
    status: str  # "OK" | "BLOCK_REEVALUATE"


def check_price_deviation(
    current_price: float, anchor_price: float, horizon: Horizon
) -> DeviationResult:
    """
    価格ズレ判定（youken 5.1）

    abs(current_price - anchor_price) / anchor_price * 100 を price_deviation_pct とする。
    ±10%超 → BLOCK_REEVALUATE（停止→再評価要求）。
    """
    if anchor_price <= 0:
        deviation = 0.0
    else:
        deviation = abs(current_price - anchor_price) / anchor_price * 100
    ok_pct = DEVIATION_OK_PCT[horizon]
    status = "BLOCK_REEVALUATE" if deviation > DEVIATION_BLOCK_PCT else "OK"
    return DeviationResult(
        anchor_price=anchor_price,
        current_price=current_price,
        price_deviation_pct=round(deviation, 2),
        deviation_ok_pct=ok_pct,
        deviation_block_pct=DEVIATION_BLOCK_PCT,
        status=status,
    )


def calc_confidence(vote_for: int, vote_against: int) -> tuple[float, Confidence]:
    """
    票差 → confidence（youken 5.5）

    p = vote_for / (vote_for + vote_against)
    HIGH: p >= 0.83, MED: 0.67 <= p < 0.83, LOW: p < 0.67
    """
    total = vote_for + vote_against
    if total == 0:
        return 0.0, Confidence.LOW
    p = vote_for / total
    for threshold, level in CONFIDENCE_THRESHOLDS:
        if p >= threshold:
            return round(p, 4), level
    return round(p, 4), Confidence.LOW


@dataclass
class AllocationResult:
    """配分・株数計算結果"""
    budget_total_jpy: int
    cash_reserved_jpy: int
    allocation_pct: float
    allocation_jpy: int
    market: Market
    lot_size: int
    quantity: int
    status: str  # "OK" | "NOT_EXECUTABLE_DUE_TO_LOT"


def calc_allocation(
    budget_total_jpy: int,
    confidence: Confidence,
    current_price: float,
    market: Market,
    risk_limit_jpy: int | None = None,
) -> AllocationResult:
    """
    配分・株数計算（youken 5.4, 5.6, 5.7）

    1. cash_reserved = budget * CASH_MIN_PCT / 100
    2. investable = budget - cash_reserved
    3. max_pct を confidence で決定
    4. allocation_pct = max_pct * confidence_multiplier
    5. allocation_jpy = min(investable, budget * allocation_pct / 100)
    6. risk_limit による制限（あれば）
    7. shares = floor(allocation_jpy / current_price)
    8. lot_size でフロア: shares = floor(shares / lot_size) * lot_size
    9. shares == 0 なら NOT_EXECUTABLE_DUE_TO_LOT
    """
    cash_reserved = int(budget_total_jpy * CASH_MIN_PCT / 100)
    investable = budget_total_jpy - cash_reserved

    max_pct = MAX_ALLOCATION_HIGH_PCT if confidence == Confidence.HIGH else MAX_ALLOCATION_PCT
    multiplier = CONFIDENCE_MULTIPLIER[confidence]
    alloc_pct = max_pct * multiplier

    # min_pct 下限チェック
    if alloc_pct < MIN_ALLOCATION_PCT:
        alloc_pct = MIN_ALLOCATION_PCT

    alloc_jpy = int(budget_total_jpy * alloc_pct / 100)
    alloc_jpy = min(alloc_jpy, investable)

    # risk_limit 制限
    if risk_limit_jpy is not None:
        alloc_jpy = min(alloc_jpy, risk_limit_jpy)

    lot = LOT_SIZE[market]

    if current_price <= 0:
        shares = 0
    else:
        raw_shares = alloc_jpy / current_price
        shares = math.floor(raw_shares / lot) * lot

    status = "OK" if shares > 0 else "NOT_EXECUTABLE_DUE_TO_LOT"

    return AllocationResult(
        budget_total_jpy=budget_total_jpy,
        cash_reserved_jpy=cash_reserved,
        allocation_pct=round(alloc_pct, 2),
        allocation_jpy=alloc_jpy,
        market=market,
        lot_size=lot,
        quantity=shares,
        status=status,
    )
