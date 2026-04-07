"""
IndicatorFilter evaluator 層のユニットテスト

DB モック不要。dataclass に直接テストデータを詰めて渡す。
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from indicator_filter import (
    TickerInputs,
    FilterInputs,
    ScheduleContext,
    FilterResult,
    DEFAULT_CONFIG,
    evaluate_filter,
    _check_market_gate,
    _check_ticker_hard,
    _check_ticker_soft,
)


# ── ヘルパー ──────────────────────────────────────────

def _make_tech(
    price: float,
    sma_20: float | None = None,
    sma_200: float | None = None,
    rsi_14: float | None = None,
) -> dict:
    raw = {}
    if sma_20 is not None:
        raw["sma_20"] = sma_20
    if sma_200 is not None:
        raw["sma_200"] = sma_200
    if rsi_14 is not None:
        raw["rsi_14"] = rsi_14
    return {
        "latest_price": price,
        "timeframes": {
            "1d": {
                "indicators": {"raw": raw},
            },
        },
    }


def _make_ii(
    vix: float | None = None,
    us_10y_yield: float | None = None,
    ffr: float | None = None,
    boj_rate: float | None = None,
    days_to_event: int | None = None,
    nearest_event: str | None = None,
    eps_surprise_pct: float | None = None,
    latest_quarter: str | None = None,
    volume_ratio_5d: float | None = None,
) -> dict:
    ii: dict = {
        "market": {},
        "event_risk": {},
        "earnings": {},
        "volume": {},
    }
    if vix is not None:
        ii["market"]["vix"] = vix
    if us_10y_yield is not None:
        ii["market"]["us_10y_yield"] = us_10y_yield
    if ffr is not None:
        ii["market"]["ffr"] = ffr
    if boj_rate is not None:
        ii["market"]["boj_rate"] = boj_rate
    if days_to_event is not None:
        ii["event_risk"]["days_to_event"] = days_to_event
    if nearest_event is not None:
        ii["event_risk"]["nearest_event"] = nearest_event
    if eps_surprise_pct is not None:
        ii["earnings"]["eps_surprise_pct"] = eps_surprise_pct
    if latest_quarter is not None:
        ii["earnings"]["latest_quarter"] = latest_quarter
    if volume_ratio_5d is not None:
        ii["volume"]["volume_ratio_5d"] = volume_ratio_5d
    return ii


# ══════════════════════════════════════════════════════
# market gate テスト
# ══════════════════════════════════════════════════════

class TestMarketGate:
    def test_vix_absolute_triggers(self):
        reasons = _check_market_gate(
            {"vix": 35}, None, False, DEFAULT_CONFIG,
        )
        assert any("VIX=35" in r for r in reasons)

    def test_vix_below_threshold_no_trigger(self):
        reasons = _check_market_gate(
            {"vix": 25}, None, False, DEFAULT_CONFIG,
        )
        assert not reasons

    def test_vix_delta_triggers(self):
        reasons = _check_market_gate(
            {"vix": 25}, {"vix": 20}, False, DEFAULT_CONFIG,
        )
        assert any("VIX delta" in r for r in reasons)

    def test_vix_delta_small_no_trigger(self):
        reasons = _check_market_gate(
            {"vix": 22}, {"vix": 20}, False, DEFAULT_CONFIG,
        )
        assert not reasons

    def test_yield_delta_triggers(self):
        reasons = _check_market_gate(
            {"us_10y_yield": 4.45}, {"us_10y_yield": 4.25}, False, DEFAULT_CONFIG,
        )
        assert any("10Y yield" in r for r in reasons)

    def test_yield_delta_small_no_trigger(self):
        reasons = _check_market_gate(
            {"us_10y_yield": 4.30}, {"us_10y_yield": 4.25}, False, DEFAULT_CONFIG,
        )
        assert not reasons

    def test_ffr_change_triggers(self):
        reasons = _check_market_gate(
            {"ffr": 5.50}, {"ffr": 5.33}, False, DEFAULT_CONFIG,
        )
        assert any("FFR changed" in r for r in reasons)

    def test_ffr_same_no_trigger(self):
        reasons = _check_market_gate(
            {"ffr": 5.33}, {"ffr": 5.33}, False, DEFAULT_CONFIG,
        )
        ffr_reasons = [r for r in reasons if "FFR" in r]
        assert not ffr_reasons

    def test_boj_change_triggers(self):
        reasons = _check_market_gate(
            {"boj_rate": 0.50}, {"boj_rate": 0.25}, False, DEFAULT_CONFIG,
        )
        assert any("BOJ" in r for r in reasons)

    def test_central_bank_event_triggers(self):
        reasons = _check_market_gate(
            {"vix": 15}, None, True, DEFAULT_CONFIG,
        )
        assert any("FOMC/BOJ" in r for r in reasons)

    def test_no_current_market_no_trigger(self):
        reasons = _check_market_gate(None, None, False, DEFAULT_CONFIG)
        assert not reasons

    def test_no_prev_market_only_absolute(self):
        reasons = _check_market_gate(
            {"vix": 35}, None, False, DEFAULT_CONFIG,
        )
        assert len(reasons) == 1
        assert "VIX=35" in reasons[0]


# ══════════════════════════════════════════════════════
# ticker hard テスト
# ══════════════════════════════════════════════════════

class TestTickerHard:
    def test_price_tolerance_short_triggers(self):
        ti = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(103.5),
            plan_price=100.0,
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("price deviation" in r for r in reasons)

    def test_price_tolerance_short_within(self):
        ti = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(102.0),
            plan_price=100.0,
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        price_reasons = [r for r in reasons if "price deviation" in r]
        assert not price_reasons

    def test_price_tolerance_mid(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(106.0),
            plan_price=100.0,
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("price deviation" in r for r in reasons)

    def test_event_proximity_short_triggers(self):
        ti = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(100),
            current_ii=_make_ii(days_to_event=4, nearest_event="Earnings"),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("event" in r for r in reasons)

    def test_event_proximity_short_outside(self):
        ti = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(100),
            current_ii=_make_ii(days_to_event=6, nearest_event="Earnings"),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        event_reasons = [r for r in reasons if "event" in r]
        assert not event_reasons

    def test_event_proximity_long_stricter(self):
        ti = TickerInputs(
            ticker="NVDA", span="long",
            current_tech=_make_tech(100),
            current_ii=_make_ii(days_to_event=3, nearest_event="Earnings"),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        event_reasons = [r for r in reasons if "event" in r]
        assert not event_reasons

    def test_new_earnings_data_triggers(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100),
            current_ii=_make_ii(latest_quarter="2026-Q1"),
            previous_ii_earnings={"latest_quarter": "2025-Q4"},
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("new earnings" in r for r in reasons)

    def test_same_earnings_quarter_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100),
            current_ii=_make_ii(latest_quarter="2025-Q4"),
            previous_ii_earnings={"latest_quarter": "2025-Q4"},
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        earnings_reasons = [r for r in reasons if "new earnings" in r]
        assert not earnings_reasons

    def test_eps_surprise_bad_triggers(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100),
            current_ii=_make_ii(eps_surprise_pct=-12.0),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("EPS surprise" in r for r in reasons)

    def test_eps_surprise_mild_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100),
            current_ii=_make_ii(eps_surprise_pct=-5.0),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        eps_reasons = [r for r in reasons if "EPS" in r]
        assert not eps_reasons

    def test_sma200_crossdown_mid_triggers(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(195, sma_200=200),
            previous_tech=_make_tech(205, sma_200=200),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        assert any("SMA200 crossdown" in r for r in reasons)

    def test_sma200_both_below_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(190, sma_200=200),
            previous_tech=_make_tech(195, sma_200=200),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        sma_reasons = [r for r in reasons if "SMA200" in r]
        assert not sma_reasons

    def test_sma200_cross_up_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(205, sma_200=200),
            previous_tech=_make_tech(195, sma_200=200),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        sma_reasons = [r for r in reasons if "SMA200" in r]
        assert not sma_reasons

    def test_sma200_crossdown_short_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(195, sma_200=200),
            previous_tech=_make_tech(205, sma_200=200),
        )
        reasons = _check_ticker_hard(ti, DEFAULT_CONFIG)
        sma_reasons = [r for r in reasons if "SMA200" in r]
        assert not sma_reasons


# ══════════════════════════════════════════════════════
# ticker soft テスト
# ══════════════════════════════════════════════════════

class TestTickerSoft:
    def test_rsi_oversold_with_below_sma20(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(95, sma_20=100, rsi_14=25),
        )
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        assert any("RSI" in r for r in reasons)

    def test_rsi_oversold_above_sma20_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(105, sma_20=100, rsi_14=25),
        )
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        assert not reasons

    def test_volume_spike_with_decline(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(95, sma_20=100),
            previous_tech=_make_tech(98),
            current_ii=_make_ii(volume_ratio_5d=2.5),
        )
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        assert any("volume_ratio" in r for r in reasons)

    def test_volume_spike_with_rise_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(97, sma_20=100),
            previous_tech=_make_tech(95),
            current_ii=_make_ii(volume_ratio_5d=2.5),
        )
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        vol_reasons = [r for r in reasons if "volume" in r]
        assert not vol_reasons

    def test_volume_spike_above_sma20_no_trigger(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(105, sma_20=100),
            previous_tech=_make_tech(110),
            current_ii=_make_ii(volume_ratio_5d=2.5),
        )
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        vol_reasons = [r for r in reasons if "volume" in r]
        assert not vol_reasons

    def test_no_current_tech_no_trigger(self):
        ti = TickerInputs(ticker="NVDA", span="mid")
        reasons = _check_ticker_soft(ti, DEFAULT_CONFIG)
        assert not reasons


# ══════════════════════════════════════════════════════
# fail-open テスト
# ══════════════════════════════════════════════════════

class TestFailOpen:
    def _make_inputs(self, **overrides) -> tuple[FilterInputs, ScheduleContext]:
        defaults = dict(
            current_tech=_make_tech(100, sma_20=98),
            current_ii=_make_ii(vix=15),
            plan_price=100.0,
        )
        defaults.update(overrides)

        ti = TickerInputs(ticker="NVDA", span="mid", **defaults)
        inputs = FilterInputs(
            current_market={"vix": 15},
            tickers={"NVDA": ti},
        )
        return inputs, ScheduleContext()

    def test_current_tech_none_failopen(self):
        inputs, ctx = self._make_inputs(current_tech=None)
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" in result.triggered_tickers
        assert any("current data missing" in r for r in result.trigger_details["NVDA"])

    def test_current_ii_none_failopen(self):
        inputs, ctx = self._make_inputs(current_ii=None)
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" in result.triggered_tickers

    def test_plan_price_none_failopen(self):
        inputs, ctx = self._make_inputs(plan_price=None)
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" in result.triggered_tickers
        assert any("plan price missing" in r for r in result.trigger_details["NVDA"])

    def test_prev_missing_delta_disabled_absolute_only(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100, sma_20=98),
            previous_tech=None,
            current_ii=_make_ii(vix=15, days_to_event=10),
            plan_price=100.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 25},
            previous_market=None,
            tickers={"NVDA": ti},
        )
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ScheduleContext())
        assert "NVDA" in result.skipped_tickers


# ══════════════════════════════════════════════════════
# full_run テスト
# ══════════════════════════════════════════════════════

class TestFullRun:
    def _make_normal_inputs(self, span: str) -> FilterInputs:
        ti = TickerInputs(
            ticker="NVDA", span=span,
            current_tech=_make_tech(100, sma_20=98),
            current_ii=_make_ii(vix=15),
            plan_price=100.0,
        )
        return FilterInputs(
            current_market={"vix": 15},
            tickers={"NVDA": ti},
        )

    def test_full_run_matching_span(self):
        inputs = self._make_normal_inputs("short")
        ctx = ScheduleContext(full_run_spans={"short", "mid"})
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" in result.full_run_tickers
        assert "NVDA" not in result.triggered_tickers

    def test_full_run_non_matching_span(self):
        inputs = self._make_normal_inputs("mid")
        ctx = ScheduleContext(full_run_spans={"short"})
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" not in result.full_run_tickers

    def test_full_run_empty_spans(self):
        inputs = self._make_normal_inputs("short")
        ctx = ScheduleContext(full_run_spans=set())
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" not in result.full_run_tickers


# ══════════════════════════════════════════════════════
# evaluate_filter 統合テスト
# ══════════════════════════════════════════════════════

class TestEvaluateFilter:
    def test_market_gate_triggers_all(self):
        ti_a = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100),
            current_ii=_make_ii(vix=35),
            plan_price=100.0,
        )
        ti_b = TickerInputs(
            ticker="TSLA", span="short",
            current_tech=_make_tech(200),
            current_ii=_make_ii(vix=35),
            plan_price=200.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 35},
            tickers={"NVDA": ti_a, "TSLA": ti_b},
        )
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ScheduleContext())
        assert result.market_gate_triggered
        assert set(result.triggered_tickers) == {"NVDA", "TSLA"}
        assert result.status == "ALL_TRIGGERED"

    def test_mixed_trigger_and_skip(self):
        ti_triggered = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(110),
            current_ii=_make_ii(vix=15, eps_surprise_pct=-15),
            plan_price=100.0,
        )
        ti_safe = TickerInputs(
            ticker="TSLA", span="mid",
            current_tech=_make_tech(200, sma_20=198),
            current_ii=_make_ii(vix=15, days_to_event=30),
            plan_price=200.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 15},
            tickers={"NVDA": ti_triggered, "TSLA": ti_safe},
        )
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ScheduleContext())
        assert "NVDA" in result.triggered_tickers
        assert "TSLA" in result.skipped_tickers
        assert result.status == "PARTIAL_TRIGGERED"

    def test_all_skipped(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100, sma_20=98),
            current_ii=_make_ii(vix=15, days_to_event=30),
            plan_price=100.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 15},
            tickers={"NVDA": ti},
        )
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ScheduleContext())
        assert "NVDA" in result.skipped_tickers
        assert result.status == "FILTER_SKIPPED"

    def test_market_gate_skips_individual_checks(self):
        ti = TickerInputs(
            ticker="NVDA", span="mid",
            current_tech=_make_tech(100, sma_20=98),
            current_ii=_make_ii(vix=35),
            plan_price=100.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 35},
            tickers={"NVDA": ti},
        )
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ScheduleContext())
        assert result.trigger_details["NVDA"][0].startswith("VIX=35")

    def test_full_run_plus_triggered(self):
        ti_full = TickerInputs(
            ticker="NVDA", span="short",
            current_tech=_make_tech(100),
            current_ii=_make_ii(vix=15),
            plan_price=100.0,
        )
        ti_triggered = TickerInputs(
            ticker="TSLA", span="mid",
            current_tech=_make_tech(90, sma_20=100, rsi_14=25),
            current_ii=_make_ii(vix=15),
            plan_price=100.0,
        )
        inputs = FilterInputs(
            current_market={"vix": 15},
            tickers={"NVDA": ti_full, "TSLA": ti_triggered},
        )
        ctx = ScheduleContext(full_run_spans={"short"})
        result = evaluate_filter(inputs, DEFAULT_CONFIG, ctx)
        assert "NVDA" in result.full_run_tickers
        assert "TSLA" in result.triggered_tickers
        assert result.status == "ALL_TRIGGERED"
