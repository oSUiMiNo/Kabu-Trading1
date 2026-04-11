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

    default_take_profit_pct: float = 20.0
    min_rr_ratio: float = 1.0


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
        default_take_profit_pct=_num("default_take_profit_pct", defaults.default_take_profit_pct),
        min_rr_ratio=_num("min_rr_ratio", defaults.min_rr_ratio),
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


@dataclass
class PositionSizeResult:
    """ポジションサイジング結果"""
    max_loss_jpy: int
    stop_loss_pct: float
    position_size_jpy: int
    position_size_limited: bool  # True = ポジションサイジングで投入額が制限された


def calc_position_size(
    budget_total_jpy: int,
    risk_limit_pct: float,
    stop_loss_pct: float,
    existing_investment_jpy: float = 0,
) -> PositionSizeResult:
    """ポジションサイジング：許容損失額から投入上限を逆算する。

    max_loss_jpy = budget × risk_limit_pct / 100
    position_size_jpy = max_loss_jpy / abs(stop_loss_pct / 100) - existing_investment_jpy

    existing_investment_jpy: 既保有分の投入額（JPY）。新規分の上限からこの分を差し引く。
    stop_loss_pct が 0 の場合は position_size_jpy を無制限（budget と同額）にする。
    """
    max_loss_jpy = int(budget_total_jpy * risk_limit_pct / 100)

    if stop_loss_pct == 0:
        position_size_jpy = budget_total_jpy
    else:
        total_position_limit = int(max_loss_jpy / abs(stop_loss_pct / 100))
        position_size_jpy = max(0, total_position_limit - int(existing_investment_jpy))

    return PositionSizeResult(
        max_loss_jpy=max_loss_jpy,
        stop_loss_pct=stop_loss_pct,
        position_size_jpy=position_size_jpy,
        position_size_limited=False,
    )


@dataclass
class RRResult:
    """リスクリワード比結果"""
    stop_loss_pct: float
    take_profit_pct: float
    rr_ratio: float
    min_rr_ratio: float
    status: str  # "OK" | "RR_TOO_LOW"


def calc_rr_ratio(
    stop_loss_pct: float,
    take_profit_pct: float,
    min_rr_ratio: float = 1.0,
) -> RRResult:
    """リスクリワード比を計算し OK/RR_TOO_LOW を判定する。

    rr_ratio = take_profit_pct / abs(stop_loss_pct)
    """
    if stop_loss_pct == 0:
        rr_ratio = float("inf")
    else:
        rr_ratio = take_profit_pct / abs(stop_loss_pct)

    status = "OK" if rr_ratio >= min_rr_ratio else "RR_TOO_LOW"

    return RRResult(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        rr_ratio=round(rr_ratio, 2) if rr_ratio != float("inf") else 999.0,
        min_rr_ratio=min_rr_ratio,
        status=status,
    )


def calc_allocation(
    budget_total_jpy: int,
    confidence: Confidence,
    current_price: float,
    market: Market,
    risk_limit_jpy: int | None = None,
    config: PlanConfig | None = None,
    usd_jpy_rate: float | None = None,
    position_size_jpy: int | None = None,
    existing_investment_jpy: float = 0,
) -> AllocationResult:
    """配分・株数計算（youken 5.4, 5.6, 5.7）

    position_size_jpy が指定された場合、confidence ベースの配分額と比較して
    小さい方を投入額として採用する（ポジションサイジング制限）。
    existing_investment_jpy: 既保有分の投入額（JPY）。残り予算から差し引く。
    """
    cfg = config or DEFAULT_CONFIG

    alloc_pct = cfg.max_allocation_pct[confidence]

    available_budget = max(0, budget_total_jpy - int(existing_investment_jpy))
    alloc_jpy = int(available_budget * alloc_pct / 100)

    # ポジションサイジング制限（許容損失から逆算した投入上限で制限）
    # risk_limit_jpy は損失額の上限であり投入額の上限ではないため、
    # 配分額の制限には position_size_jpy を使用する
    if position_size_jpy is not None:
        alloc_jpy = min(alloc_jpy, position_size_jpy)

    lot = LOT_SIZE[market]

    if current_price <= 0:
        shares = 0
    else:
        if market == Market.US:
            if usd_jpy_rate is None or usd_jpy_rate <= 0:
                raise ValueError("US銘柄の株数計算には usd_jpy_rate が必要です")
            alloc_local = alloc_jpy / usd_jpy_rate
        else:
            alloc_local = alloc_jpy
        raw_shares = alloc_local / current_price
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


# ═══════════════════════════════════════════════════════
# Risk Overlay 適用
# ═══════════════════════════════════════════════════════

@dataclass
class RiskAdjustedResult:
    base_size_jpy: int
    regime_cap: float
    event_cap: float
    combined_cap: float
    final_size_jpy: int
    final_quantity: int
    blocked: bool
    blocked_reason: str | None
    max_risk_bps: int = 50
    bps_limit_jpy: int | None = None
    portfolio_remaining_jpy: int | None = None


def apply_risk_overlay(
    allocation: AllocationResult,
    risk_overlay,
    current_price: float,
    market: Market,
    usd_jpy_rate: float | None = None,
    is_new_entry: bool = True,
    budget_total_jpy: int = 0,
    stop_loss_pct: float = 0,
    portfolio_constraints: "PortfolioConstraints | None" = None,
) -> RiskAdjustedResult:
    """Risk Overlay の制約を AllocationResult に適用する。

    risk_overlay が None の場合は制約なしでパススルー。
    """
    base_jpy = allocation.allocation_jpy

    if risk_overlay is None:
        return RiskAdjustedResult(
            base_size_jpy=base_jpy,
            regime_cap=1.0,
            event_cap=1.0,
            combined_cap=1.0,
            final_size_jpy=base_jpy,
            final_quantity=allocation.quantity,
            blocked=False,
            blocked_reason=None,
        )

    max_risk_bps = risk_overlay.max_risk_bps

    # ブロック判定（overlay + ポートフォリオ制約を統合）
    blocked = False
    blocked_reason = None
    if not risk_overlay.allow_new_entry and is_new_entry:
        blocked = True
        blocked_reason = risk_overlay.blocked_reason
    elif portfolio_constraints is not None and is_new_entry and not portfolio_constraints.new_position_allowed:
        blocked = True
        blocked_reason = "ポートフォリオ新規建て上限超過"

    portfolio_remaining_jpy = portfolio_constraints.portfolio_remaining_jpy if portfolio_constraints else None

    if blocked:
        return RiskAdjustedResult(
            base_size_jpy=base_jpy,
            regime_cap=risk_overlay.regime_cap,
            event_cap=risk_overlay.event_cap,
            combined_cap=risk_overlay.combined_cap,
            final_size_jpy=0,
            final_quantity=0,
            blocked=True,
            blocked_reason=blocked_reason,
            max_risk_bps=max_risk_bps,
            portfolio_remaining_jpy=portfolio_remaining_jpy,
        )

    final_jpy = int(base_jpy * risk_overlay.combined_cap)

    bps_limit_jpy = None
    if budget_total_jpy > 0 and stop_loss_pct != 0:
        max_risk_jpy = budget_total_jpy * max_risk_bps / 10_000
        bps_limit_jpy = int(max_risk_jpy / abs(stop_loss_pct / 100))
        final_jpy = min(final_jpy, bps_limit_jpy)

    if portfolio_remaining_jpy is not None:
        final_jpy = min(final_jpy, portfolio_remaining_jpy)

    lot = LOT_SIZE[market]
    if current_price <= 0:
        final_qty = 0
    else:
        if market == Market.US:
            if usd_jpy_rate is None or usd_jpy_rate <= 0:
                final_qty = 0
            else:
                final_qty = math.floor((final_jpy / usd_jpy_rate) / current_price / lot) * lot
        else:
            final_qty = math.floor(final_jpy / current_price / lot) * lot

    return RiskAdjustedResult(
        base_size_jpy=base_jpy,
        regime_cap=risk_overlay.regime_cap,
        event_cap=risk_overlay.event_cap,
        combined_cap=risk_overlay.combined_cap,
        final_size_jpy=final_jpy,
        final_quantity=final_qty,
        blocked=False,
        blocked_reason=None,
        max_risk_bps=max_risk_bps,
        bps_limit_jpy=bps_limit_jpy,
        portfolio_remaining_jpy=portfolio_remaining_jpy,
    )
