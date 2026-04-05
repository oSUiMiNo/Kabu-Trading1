"""plan_calc.py apply_risk_overlay のユニットテスト"""
import pytest
from plan_calc import apply_risk_overlay, RiskAdjustedResult, Market, AllocationResult
from risk_policy import RiskOverlay, RegimeState, EventTier, PortfolioConstraints


def _make_overlay(
    regime_cap=1.0,
    event_cap=1.0,
    allow_new_entry=True,
    blocked_reason=None,
    regime_state=RegimeState.NORMAL,
    shadow_mode=True,
):
    combined = round(regime_cap * event_cap, 4)
    return RiskOverlay(
        regime_state=regime_state,
        regime_cap=regime_cap,
        event_cap=event_cap,
        combined_cap=combined,
        allow_new_entry=allow_new_entry,
        force_scale_in=False,
        shadow_mode=shadow_mode,
        blocked_reason=blocked_reason,
        event_name=None,
        days_to_event=None,
        event_tier=EventTier.NONE,
        event_pressure=None,
    )


class TestApplyRiskOverlay:

    def test_none_overlay_passthrough(self, sample_allocation_jp):
        result = apply_risk_overlay(sample_allocation_jp, None, 1000.0, Market.JP)
        assert result.final_size_jpy == sample_allocation_jp.allocation_jpy
        assert result.final_quantity == sample_allocation_jp.quantity
        assert result.blocked is False
        assert result.combined_cap == 1.0

    def test_blocked_new_entry(self, sample_allocation_jp):
        overlay = _make_overlay(
            allow_new_entry=False,
            blocked_reason="CRISIS: 新規エントリー停止",
        )
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP, is_new_entry=True,
        )
        assert result.blocked is True
        assert result.final_size_jpy == 0
        assert result.final_quantity == 0
        assert result.blocked_reason == "CRISIS: 新規エントリー停止"

    def test_existing_position_not_blocked(self, sample_allocation_jp):
        overlay = _make_overlay(allow_new_entry=False)
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP, is_new_entry=False,
        )
        assert result.blocked is False
        assert result.final_size_jpy == sample_allocation_jp.allocation_jpy

    def test_cap_applied(self, sample_allocation_jp):
        overlay = _make_overlay(regime_cap=0.6, event_cap=0.7)
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP,
        )
        expected_jpy = int(100_000 * 0.6 * 0.7)
        assert result.final_size_jpy == expected_jpy
        assert result.base_size_jpy == 100_000
        assert result.combined_cap == round(0.6 * 0.7, 4)

    def test_quantity_respects_lot_jp(self, sample_allocation_jp):
        overlay = _make_overlay(regime_cap=0.6)
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP,
        )
        # 60,000 / 1000 = 60 → floor(60/100)*100 = 0
        assert result.final_quantity % 100 == 0

    def test_us_market_with_rate(self, sample_allocation_us):
        overlay = _make_overlay(regime_cap=1.0, event_cap=1.0)
        result = apply_risk_overlay(
            sample_allocation_us, overlay, 150.0, Market.US, usd_jpy_rate=150.0,
        )
        # 100,000 JPY / 150 rate = ~666 USD / 150 price = ~4.4 → floor = 4
        assert result.final_quantity >= 0
        assert result.blocked is False

    def test_us_market_no_rate(self, sample_allocation_us):
        overlay = _make_overlay()
        result = apply_risk_overlay(
            sample_allocation_us, overlay, 150.0, Market.US, usd_jpy_rate=None,
        )
        assert result.final_quantity == 0

    def test_zero_price(self, sample_allocation_jp):
        overlay = _make_overlay()
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 0.0, Market.JP,
        )
        assert result.final_quantity == 0


class TestBpsConstraint:

    def test_bps_limits_final_size(self):
        alloc = AllocationResult(
            budget_total_jpy=1_000_000,
            allocation_pct=15.0,
            allocation_jpy=150_000,
            market=Market.JP,
            lot_size=100,
            quantity=100,
            status="OK",
        )
        overlay = _make_overlay()  # max_risk_bps=50 (default from RiskOverlay)
        result = apply_risk_overlay(
            alloc, overlay, 1000.0, Market.JP,
            budget_total_jpy=1_000_000, stop_loss_pct=-4.0,
        )
        # max_risk_jpy = 1,000,000 * 50 / 10,000 = 5,000
        # bps_limit_jpy = 5,000 / 0.04 = 125,000
        # final_jpy = min(150,000, 125,000) = 125,000
        assert result.bps_limit_jpy == 125_000
        assert result.final_size_jpy == 125_000
        assert result.max_risk_bps == 50

    def test_bps_stress_regime(self):
        alloc = AllocationResult(
            budget_total_jpy=1_000_000,
            allocation_pct=15.0,
            allocation_jpy=150_000,
            market=Market.JP,
            lot_size=100,
            quantity=100,
            status="OK",
        )
        overlay = _make_overlay(regime_cap=0.6, regime_state=RegimeState.STRESS)
        overlay.max_risk_bps = 35  # STRESS
        result = apply_risk_overlay(
            alloc, overlay, 1000.0, Market.JP,
            budget_total_jpy=1_000_000, stop_loss_pct=-4.0,
        )
        # combined_cap: 150,000 * 0.6 = 90,000
        # bps_limit: 1,000,000 * 35 / 10,000 / 0.04 = 87,500
        # final = min(90,000, 87,500) = 87,500
        assert result.bps_limit_jpy == 87_500
        assert result.final_size_jpy == 87_500

    def test_bps_not_applied_without_budget(self, sample_allocation_jp):
        overlay = _make_overlay()
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP,
            budget_total_jpy=0, stop_loss_pct=-4.0,
        )
        assert result.bps_limit_jpy is None
        assert result.final_size_jpy == 100_000

    def test_bps_not_applied_without_stop_loss(self, sample_allocation_jp):
        overlay = _make_overlay()
        result = apply_risk_overlay(
            sample_allocation_jp, overlay, 1000.0, Market.JP,
            budget_total_jpy=1_000_000, stop_loss_pct=0,
        )
        assert result.bps_limit_jpy is None


class TestPortfolioConstraints:

    def test_remaining_cap_limits_final_size(self):
        alloc = AllocationResult(
            budget_total_jpy=1_000_000,
            allocation_pct=15.0,
            allocation_jpy=150_000,
            market=Market.JP,
            lot_size=100,
            quantity=100,
            status="OK",
        )
        overlay = _make_overlay()
        pc = PortfolioConstraints(
            portfolio_gross_jpy=500_000,
            portfolio_gross_ratio=0.5,
            portfolio_gross_cap=0.6,
            portfolio_remaining_jpy=100_000,  # 600k - 500k = 100k
            active_position_count=1,
            max_new_positions=None,
            new_position_allowed=True,
        )
        result = apply_risk_overlay(
            alloc, overlay, 1000.0, Market.JP,
            portfolio_constraints=pc,
        )
        assert result.final_size_jpy <= 100_000
        assert result.portfolio_remaining_jpy == 100_000

    def test_new_position_blocked(self):
        alloc = AllocationResult(
            budget_total_jpy=1_000_000,
            allocation_pct=10.0,
            allocation_jpy=100_000,
            market=Market.JP,
            lot_size=100,
            quantity=100,
            status="OK",
        )
        overlay = _make_overlay()
        pc = PortfolioConstraints(
            portfolio_gross_jpy=500_000,
            portfolio_gross_ratio=0.5,
            portfolio_gross_cap=0.6,
            portfolio_remaining_jpy=100_000,
            active_position_count=1,
            max_new_positions=1,
            new_position_allowed=False,
        )
        result = apply_risk_overlay(
            alloc, overlay, 1000.0, Market.JP,
            is_new_entry=True, portfolio_constraints=pc,
        )
        assert result.blocked is True
        assert result.final_size_jpy == 0

    def test_existing_position_not_blocked_by_portfolio(self):
        alloc = AllocationResult(
            budget_total_jpy=1_000_000,
            allocation_pct=10.0,
            allocation_jpy=100_000,
            market=Market.JP,
            lot_size=100,
            quantity=100,
            status="OK",
        )
        overlay = _make_overlay()
        pc = PortfolioConstraints(
            portfolio_gross_jpy=500_000,
            portfolio_gross_ratio=0.5,
            portfolio_gross_cap=0.6,
            portfolio_remaining_jpy=100_000,
            active_position_count=1,
            max_new_positions=1,
            new_position_allowed=False,
        )
        result = apply_risk_overlay(
            alloc, overlay, 1000.0, Market.JP,
            is_new_entry=False, portfolio_constraints=pc,
        )
        assert result.blocked is False
