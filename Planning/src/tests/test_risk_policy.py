"""risk_policy.py のユニットテスト"""
import pytest
from risk_policy import (
    RegimeState,
    EventTier,
    EventState,
    EventRiskResult,
    PortfolioConstraints,
    evaluate_portfolio_constraints,
    RiskOverlayConfig,
    RiskOverlay,
    evaluate_market_regime,
    evaluate_event_risk,
    evaluate_risk_overlay,
    load_risk_overlay_config,
)


# ═══════════════════════════════════════════════════════
# evaluate_market_regime
# ═══════════════════════════════════════════════════════

class TestEvaluateMarketRegime:

    def test_calm(self, calm_market, good_breadth):
        state, cap, fsi = evaluate_market_regime(calm_market, good_breadth)
        assert state == RegimeState.CALM
        assert cap == 1.0
        assert fsi is False

    def test_normal_default(self, normal_market, good_breadth):
        state, cap, fsi = evaluate_market_regime(normal_market, good_breadth)
        assert state == RegimeState.NORMAL
        assert cap == 1.0
        assert fsi is False

    def test_stress(self, stress_market, stress_breadth):
        state, cap, fsi = evaluate_market_regime(stress_market, stress_breadth)
        assert state == RegimeState.STRESS
        assert cap == 0.60
        assert fsi is True

    def test_crisis(self, crisis_market, crisis_breadth):
        state, cap, fsi = evaluate_market_regime(crisis_market, crisis_breadth)
        assert state == RegimeState.CRISIS
        assert cap == 0.25
        assert fsi is True

    def test_vix_alone_not_stress(self):
        """VIX が高くても breadth が良好なら STRESS にならない（スコア1止まり）"""
        market = {"vix": 30.0}
        state, cap, _ = evaluate_market_regime(market, 5.0)
        assert state == RegimeState.NORMAL

    def test_breadth_alone_not_stress(self, calm_market, stress_breadth):
        """VIX が低くても breadth だけ悪化 → スコア1止まりで NORMAL"""
        state, cap, _ = evaluate_market_regime(calm_market, stress_breadth)
        assert state == RegimeState.NORMAL

    def test_none_market_returns_normal(self):
        state, cap, fsi = evaluate_market_regime(None, None)
        assert state == RegimeState.NORMAL
        assert cap == 1.0

    def test_missing_vix_returns_normal(self):
        state, cap, fsi = evaluate_market_regime({"us_10y_yield": 4.5}, None)
        assert state == RegimeState.NORMAL

    def test_breadth_none_with_low_vix_is_calm(self, calm_market):
        state, cap, _ = evaluate_market_regime(calm_market, None)
        assert state == RegimeState.CALM


# ═══════════════════════════════════════════════════════
# evaluate_event_risk
# ═══════════════════════════════════════════════════════

class TestEvaluateEventRisk:

    def test_no_event(self):
        r = evaluate_event_risk(None, -4.0, [])
        assert r.event_cap == 1.0
        assert r.allow_new_entry is True
        assert r.blocked_reason is None

    def test_earnings_freeze(self, earnings_event_near, event_masters):
        r = evaluate_event_risk(earnings_event_near, -4.0, event_masters)
        assert r.allow_new_entry is False
        assert "freeze" in r.blocked_reason
        assert r.days_to_event == 2

    def test_earnings_reduce(self, event_masters):
        event = {
            "nearest_event": "AAPL Earnings",
            "days_to_event": 5,
            "implied_move_pct": 2.0,
        }
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert r.allow_new_entry is True
        assert r.event_cap == 0.7

    def test_tier1_event_cap(self, fomc_event, event_masters):
        r = evaluate_event_risk(fomc_event, -4.0, event_masters)
        assert r.event_tier == EventTier.TIER1
        assert r.event_cap <= 0.7

    def test_tier2_event_cap(self, event_masters):
        event = {"nearest_event": "GDP Report", "days_to_event": 1, "implied_move_pct": 2.0}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert r.event_tier == EventTier.TIER2
        assert r.event_cap <= 0.85

    def test_event_pressure_band_low(self, event_masters):
        """event_pressure < 0.7 → cap=1.0"""
        event = {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 2.0}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert r.event_cap == 1.0
        assert r.allow_new_entry is True

    def test_event_pressure_band_1(self, event_masters):
        """0.7 <= event_pressure < 1.0 → cap=0.75"""
        event = {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 3.2}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert 0.7 <= r.event_pressure < 1.0
        assert r.event_cap == 0.75
        assert r.allow_new_entry is True

    def test_event_pressure_band_2(self, event_masters):
        """1.0 <= event_pressure < 1.3 → cap=0.5"""
        event = {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 4.4}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert 1.0 <= r.event_pressure < 1.3
        assert r.event_cap == 0.5
        assert r.allow_new_entry is True

    def test_event_pressure_band_3_blocks(self, event_masters):
        """event_pressure > 1.3 → 新規エントリー停止"""
        event = {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 6.0}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert r.event_pressure > 1.3
        assert r.allow_new_entry is False
        assert "損切り幅超過" in r.blocked_reason

    def test_missing_event_name(self, event_masters):
        event = {"days_to_event": 5}
        r = evaluate_event_risk(event, -4.0, event_masters)
        assert r.event_cap == 1.0
        assert r.allow_new_entry is True


# ═══════════════════════════════════════════════════════
# evaluate_risk_overlay（統合テスト）
# ═══════════════════════════════════════════════════════

class TestEvaluateRiskOverlay:

    def test_normal_no_event(self, normal_market, good_breadth):
        ii = {
            "market": normal_market,
            "relative_strength": {"vs_index_3m_pct": good_breadth},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.regime_state == RegimeState.NORMAL
        assert overlay.combined_cap == 1.0
        assert overlay.allow_new_entry is True

    def test_combined_cap_multiplication(self, stress_market, stress_breadth, event_masters):
        ii = {
            "market": stress_market,
            "relative_strength": {"vs_index_3m_pct": stress_breadth},
            "event_risk": {"nearest_event": "FOMC Meeting", "days_to_event": 1, "implied_move_pct": 3.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert overlay.regime_state == RegimeState.STRESS
        assert overlay.regime_cap == 0.60
        expected_combined = round(overlay.regime_cap * overlay.event_cap, 4)
        assert overlay.combined_cap == expected_combined

    def test_crisis_blocks_new_entry(self, crisis_market, crisis_breadth):
        ii = {
            "market": crisis_market,
            "relative_strength": {"vs_index_3m_pct": crisis_breadth},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.regime_state == RegimeState.CRISIS
        assert overlay.allow_new_entry is False
        assert "CRISIS" in overlay.blocked_reason

    def test_none_indicators_returns_normal(self):
        overlay = evaluate_risk_overlay(None, -4.0, [])
        assert overlay.regime_state == RegimeState.NORMAL
        assert overlay.combined_cap == 1.0

    def test_signals_populated(self, normal_market, good_breadth, fomc_event, event_masters):
        ii = {
            "market": normal_market,
            "relative_strength": {"vs_index_3m_pct": good_breadth},
            "event_risk": fomc_event,
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert overlay.signals["vix"] == 20.0
        assert overlay.signals["breadth_pct"] == good_breadth

    def test_horizon_override_mid_earnings_freeze(self, event_masters):
        """MID horizon → 決算 freeze が cap=0.5 に緩和される"""
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 2, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="MID")
        assert overlay.allow_new_entry is True  # freeze解除
        assert overlay.event_cap <= 0.5
        assert overlay.override_reason is not None
        assert "中長期" in overlay.override_reason

    def test_horizon_override_short_no_change(self, event_masters):
        """SHORT horizon → 通常通り freeze"""
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 2, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="SHORT")
        assert overlay.allow_new_entry is False
        assert overlay.override_reason is None

    def test_horizon_override_long_pressure_block(self, event_masters):
        """LONG horizon → event_pressure block が cap=0.3 に緩和"""
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 6.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="LONG")
        assert overlay.allow_new_entry is True  # block解除
        assert overlay.event_cap <= 0.3
        assert "中長期" in overlay.override_reason

    def test_event_state_pre_earnings(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 5, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert overlay.event_state == EventState.PRE_EARNINGS

    def test_event_state_pre_macro(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "FOMC Meeting", "days_to_event": 1, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert overlay.event_state == EventState.PRE_MACRO

    def test_event_state_event_day(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "FOMC Meeting", "days_to_event": 0, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert overlay.event_state == EventState.EVENT_DAY

    def test_event_state_no_event(self):
        ii = {"market": {"vix": 15.0}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.event_state == EventState.NO_EVENT

    def test_allow_hold_through_short_earnings_freeze(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 2, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="SHORT")
        assert overlay.allow_hold_through_event is False

    def test_allow_hold_through_mid_earnings_freeze(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 2, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="MID")
        assert overlay.allow_hold_through_event is True

    def test_commentary_tags_stress(self, stress_market, stress_breadth):
        ii = {"market": stress_market, "relative_strength": {"vs_index_3m_pct": stress_breadth}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert "high_systematic_risk" in overlay.commentary_tags
        assert "scale_in_forced" in overlay.commentary_tags

    def test_commentary_tags_earnings(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "AAPL Earnings", "days_to_event": 2, "implied_move_pct": 2.0},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters, horizon="SHORT")
        assert "binary_event_near" in overlay.commentary_tags
        assert "entry_blocked" in overlay.commentary_tags

    def test_commentary_tags_normal_empty(self, normal_market, good_breadth):
        ii = {"market": normal_market, "relative_strength": {"vs_index_3m_pct": good_breadth}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.commentary_tags == []

    def test_commentary_tags_pressure_elevated(self, event_masters):
        ii = {
            "market": {"vix": 15.0},
            "event_risk": {"nearest_event": "some event", "days_to_event": 10, "implied_move_pct": 4.4},
        }
        overlay = evaluate_risk_overlay(ii, -4.0, event_masters)
        assert "event_pressure_elevated" in overlay.commentary_tags

    def test_max_risk_bps_normal(self, normal_market, good_breadth):
        ii = {"market": normal_market, "relative_strength": {"vs_index_3m_pct": good_breadth}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.max_risk_bps == 50

    def test_max_risk_bps_stress(self, stress_market, stress_breadth):
        ii = {"market": stress_market, "relative_strength": {"vs_index_3m_pct": stress_breadth}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.max_risk_bps == 35

    def test_max_risk_bps_crisis(self, crisis_market, crisis_breadth):
        ii = {"market": crisis_market, "relative_strength": {"vs_index_3m_pct": crisis_breadth}}
        overlay = evaluate_risk_overlay(ii, -4.0, [])
        assert overlay.max_risk_bps == 15


# ═══════════════════════════════════════════════════════
# load_risk_overlay_config
# ═══════════════════════════════════════════════════════

class TestLoadRiskOverlayConfig:

    def test_none_returns_default(self):
        cfg = load_risk_overlay_config(None)
        assert cfg.shadow_mode is True
        assert cfg.vix_stress == 25.0

    def test_empty_dict_returns_default(self):
        cfg = load_risk_overlay_config({})
        assert cfg.vix_crisis == 35.0

    def test_custom_values(self):
        db = {"risk_overlay": {"vix_stress": 20.0, "shadow_mode": False}}
        cfg = load_risk_overlay_config(db)
        assert cfg.vix_stress == 20.0
        assert cfg.shadow_mode is False
        assert cfg.vix_crisis == 35.0  # default

    def test_invalid_type_falls_back(self):
        db = {"risk_overlay": {"vix_stress": "not_a_number"}}
        cfg = load_risk_overlay_config(db)
        assert cfg.vix_stress == 25.0  # default because float("not_a_number") fails


# ═══════════════════════════════════════════════════════
# evaluate_portfolio_constraints
# ═══════════════════════════════════════════════════════

class TestEvaluatePortfolioConstraints:

    def test_empty_holdings(self):
        pc = evaluate_portfolio_constraints([], 1_000_000, RegimeState.NORMAL)
        assert pc.portfolio_gross_jpy == 0
        assert pc.portfolio_remaining_jpy == 1_000_000
        assert pc.active_position_count == 0
        assert pc.new_position_allowed is True
        assert pc.max_new_positions is None

    def test_stress_remaining_cap(self):
        holdings = [
            {"shares": 100, "current_price": 2000.0, "market": "JP"},  # 200,000
        ]
        pc = evaluate_portfolio_constraints(holdings, 1_000_000, RegimeState.STRESS)
        assert pc.portfolio_gross_jpy == 200_000
        # STRESS cap = 0.60 → max = 600,000 → remaining = 400,000
        assert pc.portfolio_remaining_jpy == 400_000
        assert pc.portfolio_gross_cap == 0.60
        assert pc.active_position_count == 1

    def test_stress_max_new_positions_blocked(self):
        holdings = [
            {"shares": 100, "current_price": 2000.0, "market": "JP"},
        ]
        pc = evaluate_portfolio_constraints(holdings, 1_000_000, RegimeState.STRESS)
        # STRESS: max_new_positions = 1, active = 1 → blocked
        assert pc.max_new_positions == 1
        assert pc.new_position_allowed is False

    def test_crisis_all_blocked(self):
        pc = evaluate_portfolio_constraints([], 1_000_000, RegimeState.CRISIS)
        assert pc.max_new_positions == 0
        assert pc.new_position_allowed is False  # 0 >= 0 → blocked

    def test_us_holdings_with_rate(self):
        holdings = [
            {"shares": 10, "current_price": 150.0, "market": "US"},
        ]
        pc = evaluate_portfolio_constraints(holdings, 1_000_000, RegimeState.NORMAL, usd_jpy_rate=150.0)
        # 10 * 150 * 150 = 225,000
        assert pc.portfolio_gross_jpy == 225_000

    def test_zero_shares_ignored(self):
        holdings = [
            {"shares": 0, "current_price": 2000.0, "market": "JP"},
        ]
        pc = evaluate_portfolio_constraints(holdings, 1_000_000, RegimeState.NORMAL)
        assert pc.active_position_count == 0
        assert pc.portfolio_gross_jpy == 0
