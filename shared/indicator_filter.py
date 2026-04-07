"""
IndicatorFilter

テクニカル指標・重要指標から Monitor 実行の要否を決定論的に判定する。
2層構成:
  - loader 層: DB から current/prev/plan データを収集して FilterInputs を構築
  - evaluator 層: 渡されたデータだけで判定する純ロジック（DB アクセスなし）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


# ── dataclass 定義 ────────────────────────────────────

@dataclass
class TickerInputs:
    ticker: str
    span: str                          # "short" / "mid" / "long"
    current_tech: dict | None = None
    previous_tech: dict | None = None
    current_ii: dict | None = None
    previous_ii_earnings: dict | None = None
    plan_price: float | None = None


@dataclass
class FilterInputs:
    current_market: dict | None = None
    previous_market: dict | None = None
    central_bank_within_n_days: bool = False
    tickers: dict[str, TickerInputs] = field(default_factory=dict)


@dataclass
class ScheduleContext:
    full_run_spans: set[str] = field(default_factory=set)


@dataclass
class FilterResult:
    triggered_tickers: list[str] = field(default_factory=list)
    full_run_tickers: list[str] = field(default_factory=list)
    skipped_tickers: list[str] = field(default_factory=list)
    market_gate_triggered: bool = False
    trigger_details: dict[str, list[str]] = field(default_factory=dict)
    status: str = "FILTER_SKIPPED"


# ── デフォルト設定 ────────────────────────────────────

DEFAULT_CONFIG = {
    "filter_vix_threshold": 30,
    "filter_vix_delta_pt": 4.0,
    "filter_yield_delta_bp": 15,
    "filter_central_bank_event_days": 1,
    "filter_eps_surprise_hard_pct": -10,
    "filter_volume_ratio_threshold": 2.0,
    "filter_rsi_oversold": 30,
    "filter_event_threshold_days": {"short": 5, "mid": 3, "long": 2},
    "price_tolerance_pct": {"short": 3, "mid": 5, "long": 7},
}


# ── テクニカルデータ抽出ヘルパー ──────────────────────

def _extract_raw(tech: dict) -> dict:
    """archive.technical から raw indicators を取り出す。"""
    tfs = tech.get("timeframes", {})
    daily = tfs.get("1d", {})
    return daily.get("indicators", {}).get("raw", {})


def _extract_close(tech: dict) -> float | None:
    """archive.technical から latest_price を取り出す。"""
    return tech.get("latest_price")


def _extract_prev_close(tech: dict) -> float | None:
    """前回の archive.technical から close（latest_price）を取り出す。"""
    return tech.get("latest_price")


# ── market gate ───────────────────────────────────────

def _check_market_gate(
    current_market: dict | None,
    previous_market: dict | None,
    central_bank_within_n_days: bool,
    config: dict,
) -> list[str]:
    """市場全体ゲート。トリガー理由のリストを返す（空なら発火なし）。"""
    reasons: list[str] = []
    if current_market is None:
        return reasons

    vix = current_market.get("vix")
    vix_threshold = config.get("filter_vix_threshold", DEFAULT_CONFIG["filter_vix_threshold"])
    vix_delta_pt = config.get("filter_vix_delta_pt", DEFAULT_CONFIG["filter_vix_delta_pt"])

    if vix is not None and vix >= vix_threshold:
        reasons.append(f"VIX={vix} >= {vix_threshold}")

    if vix is not None and previous_market:
        prev_vix = previous_market.get("vix")
        if prev_vix is not None:
            delta = vix - prev_vix
            if delta >= vix_delta_pt:
                reasons.append(f"VIX delta={delta:+.1f} >= +{vix_delta_pt}")

    yield_delta_bp = config.get("filter_yield_delta_bp", DEFAULT_CONFIG["filter_yield_delta_bp"])
    us_10y = current_market.get("us_10y_yield")
    if us_10y is not None and previous_market:
        prev_10y = previous_market.get("us_10y_yield")
        if prev_10y is not None:
            delta_bp = abs(us_10y - prev_10y) * 100
            if delta_bp >= yield_delta_bp:
                reasons.append(f"10Y yield delta={delta_bp:.0f}bp >= {yield_delta_bp}bp")

    if previous_market:
        ffr = current_market.get("ffr")
        prev_ffr = previous_market.get("ffr")
        if ffr is not None and prev_ffr is not None and ffr != prev_ffr:
            reasons.append(f"FFR changed {prev_ffr} -> {ffr}")

        boj = current_market.get("boj_rate")
        prev_boj = previous_market.get("boj_rate")
        if boj is not None and prev_boj is not None and boj != prev_boj:
            reasons.append(f"BOJ rate changed {prev_boj} -> {boj}")

    if central_bank_within_n_days:
        reasons.append("FOMC/BOJ within 1 business day")

    return reasons


# ── ticker hard triggers ──────────────────────────────

def _check_ticker_hard(ti: TickerInputs, config: dict) -> list[str]:
    """個別ハードトリガー。トリガー理由のリストを返す。"""
    reasons: list[str] = []

    tolerance_map = config.get("price_tolerance_pct", DEFAULT_CONFIG["price_tolerance_pct"])
    tolerance = tolerance_map.get(ti.span, 5)

    if ti.current_tech is not None and ti.plan_price is not None and ti.plan_price > 0:
        current_price = _extract_close(ti.current_tech)
        if current_price is not None:
            pct = abs(current_price - ti.plan_price) / ti.plan_price * 100
            if pct > tolerance:
                reasons.append(f"price deviation {pct:.1f}% > {tolerance}%")

    event_days_map = config.get("filter_event_threshold_days", DEFAULT_CONFIG["filter_event_threshold_days"])
    event_threshold = event_days_map.get(ti.span, 3)

    if ti.current_ii:
        event_risk = ti.current_ii.get("event_risk", {})
        days_to_event = event_risk.get("days_to_event")
        if days_to_event is not None and days_to_event <= event_threshold:
            event_name = event_risk.get("nearest_event", "event")
            reasons.append(f"event '{event_name}' in {days_to_event}d <= {event_threshold}d")

    if ti.current_ii and ti.previous_ii_earnings:
        current_quarter = (ti.current_ii.get("earnings") or {}).get("latest_quarter")
        prev_quarter = ti.previous_ii_earnings.get("latest_quarter")
        if current_quarter and prev_quarter and current_quarter != prev_quarter:
            reasons.append(f"new earnings data: {prev_quarter} -> {current_quarter}")

    eps_threshold = config.get("filter_eps_surprise_hard_pct", DEFAULT_CONFIG["filter_eps_surprise_hard_pct"])
    if ti.current_ii:
        eps_surprise = (ti.current_ii.get("earnings") or {}).get("eps_surprise_pct")
        if eps_surprise is not None and eps_surprise <= eps_threshold:
            reasons.append(f"EPS surprise {eps_surprise}% <= {eps_threshold}%")

    if ti.span in ("mid", "long") and ti.current_tech and ti.previous_tech:
        current_raw = _extract_raw(ti.current_tech)
        prev_raw = _extract_raw(ti.previous_tech)
        current_close = _extract_close(ti.current_tech)
        prev_close = _extract_prev_close(ti.previous_tech)
        current_sma200 = current_raw.get("sma_200")
        prev_sma200 = prev_raw.get("sma_200")

        if (current_close is not None and current_sma200 is not None
                and prev_close is not None and prev_sma200 is not None):
            was_above = prev_close > prev_sma200
            now_below = current_close < current_sma200
            if was_above and now_below:
                reasons.append("new SMA200 crossdown")

    return reasons


# ── ticker soft triggers ──────────────────────────────

def _check_ticker_soft(ti: TickerInputs, config: dict) -> list[str]:
    """個別ソフトトリガー（組み合わせ条件）。トリガー理由のリストを返す。"""
    reasons: list[str] = []
    if ti.current_tech is None:
        return reasons

    raw = _extract_raw(ti.current_tech)
    close = _extract_close(ti.current_tech)
    sma_20 = raw.get("sma_20")
    rsi = raw.get("rsi_14")

    rsi_oversold = config.get("filter_rsi_oversold", DEFAULT_CONFIG["filter_rsi_oversold"])
    if rsi is not None and sma_20 is not None and close is not None:
        if rsi < rsi_oversold and close < sma_20:
            reasons.append(f"RSI={rsi:.1f} < {rsi_oversold} AND close < SMA20")

    vol_threshold = config.get("filter_volume_ratio_threshold", DEFAULT_CONFIG["filter_volume_ratio_threshold"])
    if ti.current_ii and sma_20 is not None and close is not None:
        vol_ratio = (ti.current_ii.get("volume") or {}).get("volume_ratio_5d")
        prev_close = _extract_prev_close(ti.previous_tech) if ti.previous_tech else None

        if (vol_ratio is not None and vol_ratio >= vol_threshold
                and close < sma_20
                and prev_close is not None and close < prev_close):
            reasons.append(
                f"volume_ratio={vol_ratio:.1f}x >= {vol_threshold}x "
                f"AND close < SMA20 AND close < prev_close"
            )

    return reasons


# ── evaluator メイン ──────────────────────────────────

def evaluate_filter(
    inputs: FilterInputs,
    config: dict,
    schedule_context: ScheduleContext,
) -> FilterResult:
    """渡されたデータだけで全銘柄のフィルター判定を行う。"""
    result = FilterResult()

    market_reasons = _check_market_gate(
        inputs.current_market,
        inputs.previous_market,
        inputs.central_bank_within_n_days,
        config,
    )
    result.market_gate_triggered = bool(market_reasons)

    for ticker, ti in inputs.tickers.items():
        if ti.span in schedule_context.full_run_spans:
            result.full_run_tickers.append(ticker)
            result.trigger_details[ticker] = [f"full_run ({ti.span})"]
            continue

        if ti.current_tech is None or ti.current_ii is None:
            result.triggered_tickers.append(ticker)
            result.trigger_details[ticker] = ["fail-open: current data missing"]
            continue

        if ti.plan_price is None:
            result.triggered_tickers.append(ticker)
            result.trigger_details[ticker] = ["fail-open: plan price missing"]
            continue

        if result.market_gate_triggered:
            result.triggered_tickers.append(ticker)
            result.trigger_details[ticker] = market_reasons
            continue

        ticker_reasons: list[str] = []
        ticker_reasons.extend(_check_ticker_hard(ti, config))
        if not ticker_reasons:
            ticker_reasons.extend(_check_ticker_soft(ti, config))

        if ticker_reasons:
            result.triggered_tickers.append(ticker)
            result.trigger_details[ticker] = ticker_reasons
        else:
            result.skipped_tickers.append(ticker)

    all_active = result.triggered_tickers + result.full_run_tickers
    if not all_active:
        result.status = "FILTER_SKIPPED"
    elif not result.skipped_tickers:
        result.status = "ALL_TRIGGERED"
    else:
        result.status = "PARTIAL_TRIGGERED"

    return result


# ══════════════════════════════════════════════════════
# loader 層 — DB から判定データを収集
# ══════════════════════════════════════════════════════

def _get_current_ii_market(pipeline_start: str) -> dict | None:
    """今回実行の important_indicators から市場データ（VIX 等）を取得。
    pipeline_start 以降に作成された任意1銘柄の archive から取る。"""
    from supabase_client import get_client
    resp = (
        get_client()
        .from_("archive")
        .select("important_indicators")
        .gte("created_at", pipeline_start)
        .not_.is_("important_indicators", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    ii = resp.data[0].get("important_indicators") or {}
    return ii.get("market")


def _get_previous_ii_market(pipeline_start: str) -> dict | None:
    """前回実行の important_indicators から市場データを取得。
    pipeline_start より前に作成された任意1銘柄の archive から取る。"""
    from supabase_client import get_client
    resp = (
        get_client()
        .from_("archive")
        .select("important_indicators")
        .lt("created_at", pipeline_start)
        .not_.is_("important_indicators", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    ii = resp.data[0].get("important_indicators") or {}
    return ii.get("market")


def _get_current_archive(ticker: str, pipeline_start: str) -> dict | None:
    """今回実行の archive レコードを取得（technical + important_indicators）。"""
    from supabase_client import get_client
    resp = (
        get_client()
        .from_("archive")
        .select("technical, important_indicators")
        .eq("ticker", ticker.upper())
        .gte("created_at", pipeline_start)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _get_previous_archive(ticker: str, pipeline_start: str) -> dict | None:
    """前回実行の archive レコードを取得（technical + important_indicators）。"""
    from supabase_client import get_client
    resp = (
        get_client()
        .from_("archive")
        .select("technical, important_indicators")
        .eq("ticker", ticker.upper())
        .lt("created_at", pipeline_start)
        .not_.is_("technical", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _get_plan_price_and_span(ticker: str) -> tuple[float | None, str]:
    """latest newplan_full から plan 価格と span を取得。"""
    import yaml
    from supabase_client import get_client
    resp = (
        get_client()
        .from_("archive")
        .select("newplan_full, span")
        .eq("ticker", ticker.upper())
        .eq("status", "completed")
        .not_.is_("newplan_full", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None, "mid"

    record = resp.data[0]
    span = record.get("span", "mid")
    newplan_full = record.get("newplan_full", "")
    if not newplan_full:
        return None, span

    try:
        parsed = yaml.safe_load(newplan_full)
        if parsed:
            price = parsed.get("data_checks", {}).get("current_price")
            return price, span
    except Exception:
        pass
    return None, span


def _check_central_bank_within_days(days: int) -> bool:
    """FOMC / BOJ 会合が N 日以内にあるか DB で確認。"""
    from supabase_client import get_client
    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc + timedelta(days=days + 1)).isoformat()

    try:
        master_resp = (
            get_client()
            .from_("event_master")
            .select("event_id")
            .eq("category", "central_bank")
            .execute()
        )
        if not master_resp.data:
            return False
        cb_ids = [r["event_id"] for r in master_resp.data]

        for eid in cb_ids:
            dt_resp = (
                get_client()
                .from_("event_date_time")
                .select("scheduled_at_utc")
                .eq("event_id", eid)
                .gte("scheduled_at_utc", now_utc.isoformat())
                .lte("scheduled_at_utc", cutoff)
                .limit(1)
                .execute()
            )
            if dt_resp.data:
                return True
    except Exception as e:
        print(f"  [IndicatorFilter] central_bank event check failed: {e}")
    return False


def load_filter_inputs(
    tickers: list[str],
    pipeline_start: str,
    config: dict | None = None,
) -> FilterInputs:
    """DB から全銘柄分の判定データを収集して FilterInputs を構築する。"""
    cfg = config or DEFAULT_CONFIG

    current_market = _get_current_ii_market(pipeline_start)
    previous_market = _get_previous_ii_market(pipeline_start)

    cb_days = cfg.get("filter_central_bank_event_days",
                       DEFAULT_CONFIG["filter_central_bank_event_days"])
    cb_near = _check_central_bank_within_days(cb_days)

    ticker_inputs: dict[str, TickerInputs] = {}
    for ticker in tickers:
        plan_price, span = _get_plan_price_and_span(ticker)

        current_archive = _get_current_archive(ticker, pipeline_start)
        current_tech = current_archive.get("technical") if current_archive else None
        current_ii = current_archive.get("important_indicators") if current_archive else None

        previous_archive = _get_previous_archive(ticker, pipeline_start)
        previous_tech = previous_archive.get("technical") if previous_archive else None
        prev_ii = previous_archive.get("important_indicators") if previous_archive else None
        prev_earnings = (prev_ii.get("earnings") if prev_ii else None)

        ticker_inputs[ticker] = TickerInputs(
            ticker=ticker,
            span=span,
            current_tech=current_tech,
            previous_tech=previous_tech,
            current_ii=current_ii,
            previous_ii_earnings=prev_earnings,
            plan_price=plan_price,
        )

    return FilterInputs(
        current_market=current_market,
        previous_market=previous_market,
        central_bank_within_n_days=cb_near,
        tickers=ticker_inputs,
    )


# ══════════════════════════════════════════════════════
# 結合エントリポイント
# ══════════════════════════════════════════════════════

def run_filter(
    tickers: list[str],
    pipeline_start: str,
    config: dict,
    schedule_context: ScheduleContext,
) -> FilterResult:
    """loader → evaluator の結合。main_pipeline.py から呼ぶ。"""
    inputs = load_filter_inputs(tickers, pipeline_start, config)
    return evaluate_filter(inputs, config, schedule_context)
