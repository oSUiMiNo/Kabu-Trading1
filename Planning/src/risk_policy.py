"""
Risk Overlay — Policy Engine

MarketRegime（市場環境）と EventRisk（イベントリスク）を評価し、
ポジションサイズの制約を決定論的に算出するモジュール。

LLM は使用しない。全てルールベースの計算。
confidence は改ざんせず、最終サイズのみを制約する。

Usage:
    cfg = load_risk_overlay_config(db_config)
    overlay = evaluate_risk_overlay(important_indicators, stop_loss_pct, event_masters, cfg)
    # overlay.combined_cap を allocation_jpy に乗算
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RegimeState(Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    STRESS = "STRESS"
    CRISIS = "CRISIS"


class EventTier(Enum):
    TIER1 = "TIER1"
    TIER2 = "TIER2"
    NONE = "NONE"


@dataclass
class RiskOverlayConfig:
    shadow_mode: bool = True
    vix_stress: float = 25.0
    vix_crisis: float = 35.0
    breadth_stress: float = -10.0
    breadth_crisis: float = -20.0
    normal_max_risk_bps: int = 50
    stress_portfolio_cap: float = 0.60
    stress_max_risk_bps: int = 35
    crisis_portfolio_cap: float = 0.25
    crisis_max_risk_bps: int = 15
    earnings_freeze_days: int = 3
    earnings_reduce_days: int = 7
    earnings_reduce_cap: float = 0.7
    tier1_portfolio_cap: float = 0.7
    tier2_portfolio_cap: float = 0.85
    ep_band_1: float = 0.7
    ep_band_2: float = 1.0
    ep_band_3: float = 1.3


DEFAULT_RISK_OVERLAY_CONFIG = RiskOverlayConfig()


@dataclass
class RiskOverlay:
    regime_state: RegimeState
    regime_cap: float
    event_cap: float
    combined_cap: float
    allow_new_entry: bool
    force_scale_in: bool
    shadow_mode: bool
    blocked_reason: str | None
    event_name: str | None
    days_to_event: int | None
    event_tier: EventTier
    event_pressure: float | None
    signals: dict = field(default_factory=dict)


def load_risk_overlay_config(db_config: dict | None) -> RiskOverlayConfig:
    if not db_config:
        return DEFAULT_RISK_OVERLAY_CONFIG
    raw = db_config.get("risk_overlay")
    if not raw or not isinstance(raw, dict):
        return DEFAULT_RISK_OVERLAY_CONFIG

    def _g(key, default):
        v = raw.get(key)
        if v is None:
            return default
        try:
            return type(default)(v)
        except (ValueError, TypeError):
            return default

    return RiskOverlayConfig(
        shadow_mode=_g("shadow_mode", True),
        vix_stress=_g("vix_stress", 25.0),
        vix_crisis=_g("vix_crisis", 35.0),
        breadth_stress=_g("breadth_stress", -10.0),
        breadth_crisis=_g("breadth_crisis", -20.0),
        normal_max_risk_bps=_g("normal_max_risk_bps", 50),
        stress_portfolio_cap=_g("stress_portfolio_cap", 0.60),
        stress_max_risk_bps=_g("stress_max_risk_bps", 35),
        crisis_portfolio_cap=_g("crisis_portfolio_cap", 0.25),
        crisis_max_risk_bps=_g("crisis_max_risk_bps", 15),
        earnings_freeze_days=_g("earnings_freeze_days", 3),
        earnings_reduce_days=_g("earnings_reduce_days", 7),
        earnings_reduce_cap=_g("earnings_reduce_cap", 0.7),
        tier1_portfolio_cap=_g("tier1_portfolio_cap", 0.7),
        tier2_portfolio_cap=_g("tier2_portfolio_cap", 0.85),
        ep_band_1=_g("ep_band_1", 0.7),
        ep_band_2=_g("ep_band_2", 1.0),
        ep_band_3=_g("ep_band_3", 1.3),
    )


def evaluate_market_regime(
    market: dict | None,
    breadth_pct: float | None,
    config: RiskOverlayConfig | None = None,
) -> tuple[RegimeState, float, bool]:
    """MarketRegime を評価し (state, regime_cap, force_scale_in) を返す。

    複数条件スコアリング方式。VIX 単独では STRESS にならない。
    """
    cfg = config or DEFAULT_RISK_OVERLAY_CONFIG

    if not market or not isinstance(market, dict):
        return RegimeState.NORMAL, 1.0, False

    vix = market.get("vix")
    if vix is None:
        return RegimeState.NORMAL, 1.0, False

    score = 0
    if vix >= cfg.vix_crisis:
        score += 2
    elif vix >= cfg.vix_stress:
        score += 1

    if breadth_pct is not None:
        if breadth_pct <= cfg.breadth_crisis:
            score += 2
        elif breadth_pct <= cfg.breadth_stress:
            score += 1

    if score >= 4:
        return RegimeState.CRISIS, cfg.crisis_portfolio_cap, True
    if score >= 2:
        return RegimeState.STRESS, cfg.stress_portfolio_cap, True
    if score == 0 and vix < 15.0 and (breadth_pct is None or breadth_pct > 5.0):
        return RegimeState.CALM, 1.0, False
    return RegimeState.NORMAL, 1.0, False


def _match_event_master(
    event_name: str | None,
    event_masters: list[dict],
) -> dict | None:
    if not event_name or not event_masters:
        return None
    name_lower = event_name.lower()
    for em in event_masters:
        em_name = (em.get("name") or "").lower()
        if em_name and (em_name in name_lower or name_lower in em_name):
            return em
    return None


def _is_earnings_event(event_name: str | None) -> bool:
    if not event_name:
        return False
    lower = event_name.lower()
    return any(kw in lower for kw in ("earnings", "決算", "quarterly results"))


def evaluate_event_risk(
    event_risk: dict | None,
    stop_loss_pct: float,
    event_masters: list[dict],
    config: RiskOverlayConfig | None = None,
) -> tuple[float, bool, str | None, str | None, int | None, EventTier, float | None]:
    """EventRisk を評価する。

    Returns:
        (event_cap, allow_new_entry, blocked_reason,
         event_name, days_to_event, event_tier, event_pressure)
    """
    cfg = config or DEFAULT_RISK_OVERLAY_CONFIG
    none_result = (1.0, True, None, None, None, EventTier.NONE, None)

    if not event_risk or not isinstance(event_risk, dict):
        return none_result

    event_name = event_risk.get("nearest_event")
    days_to_event = event_risk.get("days_to_event")
    implied_move = event_risk.get("implied_move_pct")

    if event_name is None or days_to_event is None:
        return none_result

    event_cap = 1.0
    allow_new_entry = True
    blocked_reason = None

    matched = _match_event_master(event_name, event_masters)
    importance = (matched.get("importance") or "").lower() if matched else ""
    if importance == "critical":
        event_tier = EventTier.TIER1
    elif importance == "high":
        event_tier = EventTier.TIER2
    else:
        event_tier = EventTier.NONE

    if _is_earnings_event(event_name):
        if days_to_event <= cfg.earnings_freeze_days:
            allow_new_entry = False
            blocked_reason = f"決算まで{days_to_event}営業日（freeze閾値: {cfg.earnings_freeze_days}日）"
        elif days_to_event <= cfg.earnings_reduce_days:
            event_cap = min(event_cap, cfg.earnings_reduce_cap)

    if event_tier == EventTier.TIER1 and days_to_event <= 1:
        event_cap = min(event_cap, cfg.tier1_portfolio_cap)
    elif event_tier == EventTier.TIER2 and days_to_event <= 1:
        event_cap = min(event_cap, cfg.tier2_portfolio_cap)

    event_pressure = None
    if implied_move is not None and stop_loss_pct != 0:
        event_pressure = round(implied_move / abs(stop_loss_pct), 4)
        if event_pressure > cfg.ep_band_3:
            allow_new_entry = False
            if blocked_reason is None:
                blocked_reason = f"event_pressure={event_pressure:.2f} > {cfg.ep_band_3}（損切り幅超過）"
        elif event_pressure > cfg.ep_band_2:
            event_cap = min(event_cap, 0.5)
        elif event_pressure > cfg.ep_band_1:
            event_cap = min(event_cap, 0.75)

    return (event_cap, allow_new_entry, blocked_reason,
            event_name, days_to_event, event_tier, event_pressure)


def evaluate_risk_overlay(
    important_indicators: dict | None,
    stop_loss_pct: float,
    event_masters: list[dict],
    config: RiskOverlayConfig | None = None,
) -> RiskOverlay:
    """MarketRegime + EventRisk を統合評価する。"""
    cfg = config or DEFAULT_RISK_OVERLAY_CONFIG

    ii = important_indicators if isinstance(important_indicators, dict) else {}
    market_data = ii.get("market")
    rs = ii.get("relative_strength") or {}
    breadth_pct = rs.get("vs_index_3m_pct")
    event_risk = ii.get("event_risk")

    regime_state, regime_cap, force_scale_in = evaluate_market_regime(
        market_data, breadth_pct, cfg,
    )

    (event_cap, allow_new_entry, blocked_reason,
     event_name, days_to_event, event_tier, event_pressure) = evaluate_event_risk(
        event_risk, stop_loss_pct, event_masters, cfg,
    )

    if regime_state == RegimeState.CRISIS:
        allow_new_entry = False
        if blocked_reason is None:
            blocked_reason = "CRISIS: 新規エントリー停止"

    combined_cap = regime_cap * event_cap

    signals = {}
    if market_data and isinstance(market_data, dict):
        signals["vix"] = market_data.get("vix")
        signals["us_10y_yield"] = market_data.get("us_10y_yield")
    signals["breadth_pct"] = breadth_pct
    if event_risk and isinstance(event_risk, dict):
        signals["nearest_event"] = event_risk.get("nearest_event")
        signals["days_to_event"] = event_risk.get("days_to_event")
        signals["implied_move_pct"] = event_risk.get("implied_move_pct")

    return RiskOverlay(
        regime_state=regime_state,
        regime_cap=regime_cap,
        event_cap=event_cap,
        combined_cap=round(combined_cap, 4),
        allow_new_entry=allow_new_entry,
        force_scale_in=force_scale_in,
        shadow_mode=cfg.shadow_mode,
        blocked_reason=blocked_reason,
        event_name=event_name,
        days_to_event=days_to_event,
        event_tier=event_tier,
        event_pressure=event_pressure,
        signals=signals,
    )
