"""Risk Overlay テスト用共通フィクスチャ"""
import pytest
from risk_policy import RiskOverlayConfig, EventTier
from plan_calc import AllocationResult, Market


@pytest.fixture
def default_config():
    return RiskOverlayConfig()


@pytest.fixture
def calm_market():
    return {"vix": 12.0, "us_10y_yield": 4.2}


@pytest.fixture
def normal_market():
    return {"vix": 20.0, "us_10y_yield": 4.5}


@pytest.fixture
def stress_market():
    return {"vix": 28.0, "us_10y_yield": 4.8}


@pytest.fixture
def crisis_market():
    return {"vix": 40.0, "us_10y_yield": 5.0}


@pytest.fixture
def good_breadth():
    return 8.0


@pytest.fixture
def stress_breadth():
    return -12.0


@pytest.fixture
def crisis_breadth():
    return -25.0


@pytest.fixture
def earnings_event_near():
    return {
        "nearest_event": "AAPL Earnings",
        "days_to_event": 2,
        "implied_move_pct": 5.0,
    }


@pytest.fixture
def fomc_event():
    return {
        "nearest_event": "FOMC Meeting",
        "days_to_event": 1,
        "implied_move_pct": 3.0,
    }


@pytest.fixture
def event_masters():
    return [
        {"name": "FOMC Meeting", "importance": "critical"},
        {"name": "CPI Release", "importance": "critical"},
        {"name": "GDP Report", "importance": "high"},
        {"name": "Retail Sales", "importance": "high"},
    ]


@pytest.fixture
def sample_allocation_jp():
    return AllocationResult(
        budget_total_jpy=1_000_000,
        allocation_pct=10.0,
        allocation_jpy=100_000,
        market=Market.JP,
        lot_size=100,
        quantity=100,
        status="OK",
    )


@pytest.fixture
def sample_allocation_us():
    return AllocationResult(
        budget_total_jpy=1_000_000,
        allocation_pct=10.0,
        allocation_jpy=100_000,
        market=Market.US,
        lot_size=1,
        quantity=5,
        status="OK",
    )
