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


class EventState(Enum):
    NO_EVENT = "NO_EVENT"
    PRE_EARNINGS = "PRE_EARNINGS"
    PRE_MACRO = "PRE_MACRO"
    EVENT_DAY = "EVENT_DAY"


@dataclass
class EventRiskResult:
    event_cap: float = 1.0
    allow_new_entry: bool = True
    blocked_reason: str | None = None
    event_name: str | None = None
    days_to_event: int | None = None
    event_tier: EventTier = EventTier.NONE
    event_pressure: float | None = None
    override_reason: str | None = None
    event_state: EventState = EventState.NO_EVENT
    allow_hold_through_event: bool = True


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
    post_event_cooldown_days: int = 1
    stress_max_new_positions: int = 1
    crisis_max_new_positions: int = 0


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
    max_risk_bps: int = 50
    override_reason: str | None = None
    event_state: EventState = EventState.NO_EVENT
    allow_hold_through_event: bool = True
    post_event_cooldown_days: int = 1
    commentary_tags: list[str] = field(default_factory=list)
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
        post_event_cooldown_days=_g("post_event_cooldown_days", 1),
        stress_max_new_positions=_g("stress_max_new_positions", 1),
        crisis_max_new_positions=_g("crisis_max_new_positions", 0),
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
    horizon: str | None = None,
) -> EventRiskResult:
    """EventRisk を評価する。

    horizon が MID/LONG の場合、「停止」→「縮小」に緩和する Override を適用する。
    """
    cfg = config or DEFAULT_RISK_OVERLAY_CONFIG
    _is_long_horizon = horizon in ("MID", "LONG")

    if not event_risk or not isinstance(event_risk, dict):
        return EventRiskResult()

    event_name = event_risk.get("nearest_event")
    days_to_event = event_risk.get("days_to_event")
    implied_move = event_risk.get("implied_move_pct")

    if event_name is None or days_to_event is None:
        return EventRiskResult()

    event_cap = 1.0
    allow_new_entry = True
    blocked_reason = None
    override_reason = None

    matched = _match_event_master(event_name, event_masters)
    importance = (matched.get("importance") or "").lower() if matched else ""
    if importance == "critical":
        event_tier = EventTier.TIER1
    elif importance == "high":
        event_tier = EventTier.TIER2
    else:
        event_tier = EventTier.NONE

    is_earnings = _is_earnings_event(event_name)

    if is_earnings:
        if days_to_event <= cfg.earnings_freeze_days:
            if _is_long_horizon:
                event_cap = min(event_cap, 0.5)
                override_reason = f"中長期 Override: 決算freeze → cap=0.5（horizon={horizon}）"
            else:
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
            if _is_long_horizon:
                event_cap = min(event_cap, 0.3)
                if override_reason is None:
                    override_reason = f"中長期 Override: pressure block → cap=0.3（horizon={horizon}）"
            else:
                allow_new_entry = False
                if blocked_reason is None:
                    blocked_reason = f"event_pressure={event_pressure:.2f} > {cfg.ep_band_3}（損切り幅超過）"
        elif event_pressure > cfg.ep_band_2:
            event_cap = min(event_cap, 0.5)
        elif event_pressure > cfg.ep_band_1:
            event_cap = min(event_cap, 0.75)

    if days_to_event == 0:
        event_state = EventState.EVENT_DAY
    elif is_earnings and days_to_event <= cfg.earnings_reduce_days:
        event_state = EventState.PRE_EARNINGS
    elif event_tier in (EventTier.TIER1, EventTier.TIER2) and days_to_event <= 3:
        event_state = EventState.PRE_MACRO
    else:
        event_state = EventState.NO_EVENT

    allow_hold_through_event = True
    if not _is_long_horizon and is_earnings and days_to_event <= cfg.earnings_freeze_days:
        allow_hold_through_event = False

    return EventRiskResult(
        event_cap=event_cap,
        allow_new_entry=allow_new_entry,
        blocked_reason=blocked_reason,
        event_name=event_name,
        days_to_event=days_to_event,
        event_tier=event_tier,
        event_pressure=event_pressure,
        override_reason=override_reason,
        event_state=event_state,
        allow_hold_through_event=allow_hold_through_event,
    )


def evaluate_risk_overlay(
    important_indicators: dict | None,
    stop_loss_pct: float,
    event_masters: list[dict],
    config: RiskOverlayConfig | None = None,
    horizon: str | None = None,
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

    er = evaluate_event_risk(
        event_risk, stop_loss_pct, event_masters, cfg, horizon=horizon,
    )

    allow_new_entry = er.allow_new_entry
    blocked_reason = er.blocked_reason

    if regime_state == RegimeState.CRISIS:
        allow_new_entry = False
        if blocked_reason is None:
            blocked_reason = "CRISIS: 新規エントリー停止"
        max_risk_bps = cfg.crisis_max_risk_bps
    elif regime_state == RegimeState.STRESS:
        max_risk_bps = cfg.stress_max_risk_bps
    else:
        max_risk_bps = cfg.normal_max_risk_bps

    combined_cap = regime_cap * er.event_cap

    signals = {}
    if market_data and isinstance(market_data, dict):
        signals["vix"] = market_data.get("vix")
        signals["us_10y_yield"] = market_data.get("us_10y_yield")
    signals["breadth_pct"] = breadth_pct
    signals["nearest_event"] = er.event_name
    signals["days_to_event"] = er.days_to_event
    if event_risk and isinstance(event_risk, dict):
        signals["implied_move_pct"] = event_risk.get("implied_move_pct")

    tags: list[str] = []
    if regime_state in (RegimeState.STRESS, RegimeState.CRISIS):
        tags.append("high_systematic_risk")
    if er.event_state == EventState.PRE_EARNINGS:
        tags.append("binary_event_near")
    if er.event_pressure is not None and er.event_pressure > cfg.ep_band_2:
        tags.append("event_pressure_elevated")
    if force_scale_in:
        tags.append("scale_in_forced")
    if not allow_new_entry:
        tags.append("entry_blocked")

    return RiskOverlay(
        regime_state=regime_state,
        regime_cap=regime_cap,
        event_cap=er.event_cap,
        combined_cap=round(combined_cap, 4),
        allow_new_entry=allow_new_entry,
        force_scale_in=force_scale_in,
        shadow_mode=cfg.shadow_mode,
        blocked_reason=blocked_reason,
        event_name=er.event_name,
        days_to_event=er.days_to_event,
        event_tier=er.event_tier,
        event_pressure=er.event_pressure,
        max_risk_bps=max_risk_bps,
        override_reason=er.override_reason,
        event_state=er.event_state,
        allow_hold_through_event=er.allow_hold_through_event,
        post_event_cooldown_days=cfg.post_event_cooldown_days,
        commentary_tags=tags,
        signals=signals,
    )


# ═══════════════════════════════════════════════════════
# ポートフォリオレベル制約
# ═══════════════════════════════════════════════════════

@dataclass
class PortfolioConstraints:
    portfolio_gross_jpy: int
    portfolio_gross_ratio: float
    portfolio_gross_cap: float
    portfolio_remaining_jpy: int
    active_position_count: int
    max_new_positions: int | None
    new_position_allowed: bool


def evaluate_portfolio_constraints(
    holdings: list[dict],
    budget_total_jpy: int,
    regime_state: RegimeState,
    config: RiskOverlayConfig | None = None,
    usd_jpy_rate: float | None = None,
) -> PortfolioConstraints:
    """現在の保有銘柄から、ポートフォリオレベルの制約を計算する。"""
    cfg = config or DEFAULT_RISK_OVERLAY_CONFIG

    gross_jpy = 0
    active_count = 0
    rate = usd_jpy_rate or 1.0

    for h in (holdings or []):
        shares = h.get("shares", 0) or 0
        if shares <= 0:
            continue
        active_count += 1
        price = h.get("current_price", 0) or 0
        market = (h.get("market") or "JP").upper()
        if market == "US":
            gross_jpy += int(shares * price * rate)
        else:
            gross_jpy += int(shares * price)

    if regime_state == RegimeState.CRISIS:
        portfolio_gross_cap = cfg.crisis_portfolio_cap
        max_new_positions = cfg.crisis_max_new_positions
    elif regime_state == RegimeState.STRESS:
        portfolio_gross_cap = cfg.stress_portfolio_cap
        max_new_positions = cfg.stress_max_new_positions
    else:
        portfolio_gross_cap = 1.0
        max_new_positions = None

    gross_ratio = gross_jpy / budget_total_jpy if budget_total_jpy > 0 else 0.0
    max_gross_jpy = int(budget_total_jpy * portfolio_gross_cap)
    remaining_jpy = max(0, max_gross_jpy - gross_jpy)

    new_position_allowed = True
    if max_new_positions is not None and active_count >= max_new_positions:
        new_position_allowed = False

    return PortfolioConstraints(
        portfolio_gross_jpy=gross_jpy,
        portfolio_gross_ratio=round(gross_ratio, 4),
        portfolio_gross_cap=portfolio_gross_cap,
        portfolio_remaining_jpy=remaining_jpy,
        active_position_count=active_count,
        max_new_positions=max_new_positions,
        new_position_allowed=new_position_allowed,
    )
