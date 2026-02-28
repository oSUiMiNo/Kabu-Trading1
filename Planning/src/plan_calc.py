"""
決定論的計算ロジック

youken.md セクション5 の固定ルールを Python で実装する。
LLM は使わない。全て確定的な計算のみ。

投資パラメータは Supabase portfolio_config テーブルから読み込む。
DB値が取得できない場合はデフォルト値にフォールバックする。
"""
import math
from dataclasses import dataclass, field
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
# コードに残す定数（市場ルール・数学的閾値）
# ═══════════════════════════════════════════════════════

# 5.5 票差 → confidence（数学的閾値）
CONFIDENCE_THRESHOLDS: list[tuple[float, Confidence]] = [
    (5 / 6, Confidence.HIGH),
    (2 / 3, Confidence.MED),
    (0.0, Confidence.LOW),
]

# 5.7 ロットサイズ（取引所ルール）
LOT_SIZE: dict[Market, int] = {
    Market.JP: 100,
    Market.US: 1,
}

# 7.2 monitoring intensity（confidence→ラベル）
MONITORING_INTENSITY: dict[Confidence, str] = {
    Confidence.HIGH: "STRONG",
    Confidence.MED: "NORMAL",
    Confidence.LOW: "LIGHT",
}


# ═══════════════════════════════════════════════════════
# PlanConfig: DB由来の投資パラメータ
# ═══════════════════════════════════════════════════════

_HORIZON_KEY = {"short": Horizon.SHORT, "mid": Horizon.MID, "long": Horizon.LONG}
_CONFIDENCE_KEY = {"high": Confidence.HIGH, "med": Confidence.MED, "low": Confidence.LOW}

@dataclass
class PlanConfig:
    """portfolio_config テーブルから読み込む投資パラメータ。"""

    price_tolerance_pct: dict[Horizon, float] = field(default_factory=lambda: {
        Horizon.SHORT: 3.0, Horizon.MID: 5.0, Horizon.LONG: 7.0,
    })
    price_block_pct: float = 10.0

    max_log_age_days: dict[Horizon, int] = field(default_factory=lambda: {
        Horizon.SHORT: 2, Horizon.MID: 7, Horizon.LONG: 30,
    })

    stop_loss_pct: dict[Horizon, float] = field(default_factory=lambda: {
        Horizon.SHORT: -4.0, Horizon.MID: -8.0, Horizon.LONG: -15.0,
    })

    max_allocation_pct: dict[Confidence, float] = field(default_factory=lambda: {
        Confidence.HIGH: 15.0, Confidence.MED: 10.0, Confidence.LOW: 5.0,
    })



DEFAULT_CONFIG = PlanConfig()


def _parse_horizon_jsonb(raw: dict | None, default: dict[Horizon, float | int]) -> dict[Horizon, float | int]:
    """DB の JSONB {"short": x, "mid": y, "long": z} を dict[Horizon, ...] に変換。"""
    if not raw or not isinstance(raw, dict):
        return dict(default)
    result = {}
    for key, horizon in _HORIZON_KEY.items():
        val = raw.get(key)
        result[horizon] = type(list(default.values())[0])(val) if val is not None else default[horizon]
    return result



def _parse_confidence_jsonb(raw: dict | None, default: dict[Confidence, float]) -> dict[Confidence, float]:
    """DB の JSONB {"high": x, "med": y, "low": z} を dict[Confidence, ...] に変換。"""
    if not raw or not isinstance(raw, dict):
        return dict(default)
    result = {}
    for key, conf in _CONFIDENCE_KEY.items():
        val = raw.get(key)
        result[conf] = float(val) if val is not None else default[conf]
    return result


def load_plan_config(db_config: dict | None) -> PlanConfig:
    """portfolio_config テーブルの行（dict）から PlanConfig を生成する。

    DB値が null / 欠損の場合はデフォルト値にフォールバックする。
    """
    if not db_config:
        return PlanConfig()

    defaults = DEFAULT_CONFIG

    def _num(key: str, fallback: float) -> float:
        val = db_config.get(key)
        return float(val) if val is not None else fallback

    return PlanConfig(
        price_tolerance_pct=_parse_horizon_jsonb(
            db_config.get("price_tolerance_pct"), defaults.price_tolerance_pct,
        ),
        price_block_pct=_num("price_block_pct", defaults.price_block_pct),
        max_log_age_days=_parse_horizon_jsonb(
            db_config.get("max_log_age_days"), defaults.max_log_age_days,
        ),
        stop_loss_pct=_parse_horizon_jsonb(
            db_config.get("stop_loss_pct"), defaults.stop_loss_pct,
        ),
        max_allocation_pct=_parse_confidence_jsonb(
            db_config.get("max_allocation_pct"), defaults.max_allocation_pct,
        ),
    )


# ═══════════════════════════════════════════════════════
# 計算関数
# ═══════════════════════════════════════════════════════

@dataclass
class FreshnessResult:
    """鮮度チェック結果"""
    log_age_days: int
    max_allowed_days: int
    status: str  # "OK" | "STALE_REEVALUATE"


def check_freshness(
    log_date: datetime, now: datetime, horizon: Horizon,
    config: PlanConfig | None = None,
) -> FreshnessResult:
    """ログ鮮度チェック（youken 5.2）"""
    cfg = config or DEFAULT_CONFIG
    age = (now - log_date).days
    max_days = cfg.max_log_age_days[horizon]
    status = "OK" if age <= max_days else "STALE_REEVALUATE"
    return FreshnessResult(log_age_days=age, max_allowed_days=max_days, status=status)


@dataclass
class DeviationResult:
    """価格ズレ判定結果"""
    anchor_price: float
    current_price: float
    price_deviation_pct: float
    price_tolerance_pct: float
    price_block_pct: float
    status: str  # "OK" | "BLOCK_REEVALUATE"


def check_price_deviation(
    current_price: float, anchor_price: float, horizon: Horizon,
    config: PlanConfig | None = None,
) -> DeviationResult:
    """価格ズレ判定（youken 5.1）"""
    cfg = config or DEFAULT_CONFIG
    if anchor_price <= 0:
        deviation = 0.0
    else:
        deviation = abs(current_price - anchor_price) / anchor_price * 100
    ok_pct = cfg.price_tolerance_pct[horizon]
    status = "BLOCK_REEVALUATE" if deviation > cfg.price_block_pct else "OK"
    return DeviationResult(
        anchor_price=anchor_price,
        current_price=current_price,
        price_deviation_pct=round(deviation, 2),
        price_tolerance_pct=ok_pct,
        price_block_pct=cfg.price_block_pct,
        status=status,
    )


def calc_confidence(vote_for: int, vote_against: int) -> tuple[float, Confidence]:
    """票差 → confidence（youken 5.5）"""
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
    config: PlanConfig | None = None,
) -> AllocationResult:
    """配分・株数計算（youken 5.4, 5.6, 5.7）"""
    cfg = config or DEFAULT_CONFIG

    alloc_pct = cfg.max_allocation_pct[confidence]

    alloc_jpy = int(budget_total_jpy * alloc_pct / 100)

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
        allocation_pct=round(alloc_pct, 2),
        allocation_jpy=alloc_jpy,
        market=market,
        lot_size=lot,
        quantity=shares,
        status=status,
    )
