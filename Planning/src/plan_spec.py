"""
PlanSpec データ構造 + YAML 生成

youken.md セクション7 のPlanSpec フォーマットに準拠した
データ構造と YAML 出力機能を提供する。
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections import OrderedDict

import yaml


@dataclass
class PlanSpec:
    """youken.md セクション7 の PlanSpec 構造体"""
    ticker: str
    plan_id: str

    # decision ブロック
    decision_final: str        # BUY | SELL | ADD | REDUCE | HOLD
    vote_for: int
    vote_against: int
    horizon: str               # SHORT | MID | LONG
    p: float
    confidence: str            # HIGH | MED | LOW
    decision_basis: list[dict] = field(default_factory=list)
    # 各 dict: {"lane": "set1", "source_desc": "...", "source_url": "...", "why_it_matters": "..."}

    # freshness ブロック
    log_age_days: int = 0
    max_allowed_days: int = 0
    freshness_status: str = "OK"

    # data_checks ブロック
    anchor_price: float = 0.0
    current_price: float = 0.0
    price_deviation_pct: float = 0.0
    price_tolerance_pct: float = 0.0
    price_block_pct: float = 10.0
    data_checks_status: str = "OK"

    # risk_defaults ブロック
    stop_loss_pct: float = 0.0

    # allocation_policy ブロック
    max_pct: float = 10.0

    # portfolio_plan ブロック
    budget_total_jpy: int = 0
    allocation_pct: float = 0.0
    allocation_jpy: int = 0
    usd_jpy_rate: float | None = None
    market: str = "JP"
    lot_size: int = 100
    lot_policy: str = "FLOOR_TO_LOT"
    quantity: int = 0
    portfolio_status: str = "OK"

    # execution_plan ブロック
    order_style: str = "ONE_SHOT"
    entry_rule: str = "NEXT_BUSINESS_DAY_OPEN"
    execution_notes: list[str] = field(default_factory=lambda: [
        "自動発注はしない。通知とプラン提示のみ。"
    ])

    # risk_management ブロック
    max_loss_jpy: int = 0
    position_size_jpy: int = 0
    position_size_limited: bool = False
    take_profit_pct: float = 20.0
    rr_ratio: float = 0.0
    min_rr_ratio: float = 1.0
    rr_status: str = "OK"

    # holdings ブロック
    existing_shares: int = 0
    existing_avg_cost: float = 0.0
    existing_investment_jpy: float = 0.0

    # risk_overlay ブロック
    risk_overlay_regime: str = "NORMAL"
    risk_overlay_regime_cap: float = 1.0
    risk_overlay_event_cap: float = 1.0
    risk_overlay_combined_cap: float = 1.0
    risk_overlay_allow_new_entry: bool = True
    risk_overlay_force_scale_in: bool = False
    risk_overlay_shadow_mode: bool = True
    risk_overlay_blocked_reason: str | None = None
    risk_overlay_base_size_jpy: int = 0
    risk_overlay_final_size_jpy: int = 0
    risk_overlay_event_name: str | None = None
    risk_overlay_days_to_event: int | None = None
    risk_overlay_event_tier: str = "NONE"
    risk_overlay_event_pressure: float | None = None
    risk_overlay_max_risk_bps: int = 50
    risk_overlay_bps_limit_jpy: int | None = None
    risk_overlay_event_state: str = "NO_EVENT"
    risk_overlay_allow_hold_through: bool = True
    risk_overlay_post_event_cooldown: int = 1
    risk_overlay_override_reason: str | None = None
    risk_overlay_portfolio_gross_jpy: int = 0
    risk_overlay_portfolio_remaining_jpy: int | None = None
    risk_overlay_active_positions: int = 0
    risk_overlay_max_new_positions: int | None = None
    risk_overlay_new_position_allowed: bool = True
    risk_overlay_commentary_tags: list[str] = field(default_factory=list)


def generate_plan_id(ticker: str, seq: int) -> str:
    """plan_id を生成: YYYYMMDD-TICKER-SEQ"""
    return f"{datetime.now().strftime('%Y%m%d')}-{ticker.upper()}-{seq:03d}"


def get_next_plan_num(ticker: str, output_dir: Path) -> int:
    """既存の plan ファイルから次の番号を返す"""
    pattern = f"{ticker.upper()}_plan_*.yaml"
    existing = list(output_dir.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_plan_(\d+)\.yaml$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def _ordered_dict(*pairs) -> OrderedDict:
    """キー順序を維持する OrderedDict を簡潔に生成"""
    return OrderedDict(pairs)


def build_yaml(spec: PlanSpec) -> str:
    """
    PlanSpec を youken.md セクション7 のフォーマットに従った YAML 文字列に変換する。

    フィールド順序を youken.md と一致させるため、OrderedDict で構造を手組みする。
    """
    # decision_basis を dict リストに変換
    basis_list = []
    for b in spec.decision_basis:
        entry = _ordered_dict(
            ("lane", b.get("lane", "")),
            ("source_desc", b.get("source_desc", "")),
            ("source_url", b.get("source_url", "")),
            ("why_it_matters", b.get("why_it_matters", b.get("text", ""))),
        )
        basis_list.append(entry)

    structure = _ordered_dict(
        ("ticker", spec.ticker),
        ("plan_id", spec.plan_id),
        ("decision", _ordered_dict(
            ("final", spec.decision_final),
            ("vote", _ordered_dict(
                ("for", spec.vote_for),
                ("against", spec.vote_against),
            )),
            ("horizon", spec.horizon),
            ("p", spec.p),
            ("confidence", spec.confidence),
            ("decision_basis", basis_list),
        )),
        ("freshness", _ordered_dict(
            ("log_age_days", spec.log_age_days),
            ("max_allowed_days", spec.max_allowed_days),
            ("status", spec.freshness_status),
        )),
        ("data_checks", _ordered_dict(
            ("anchor_price", spec.anchor_price),
            ("current_price", spec.current_price),
            ("price_deviation_pct", spec.price_deviation_pct),
            ("price_tolerance_pct", spec.price_tolerance_pct),
            ("price_block_pct", spec.price_block_pct),
            ("status", spec.data_checks_status),
        )),
        ("risk_management", _ordered_dict(
            ("max_loss_jpy", spec.max_loss_jpy),
            ("position_size_jpy", spec.position_size_jpy),
            ("position_size_limited", spec.position_size_limited),
            ("stop_loss_pct", spec.stop_loss_pct),
            ("take_profit_pct", spec.take_profit_pct),
            ("rr_ratio", spec.rr_ratio),
            ("min_rr_ratio", spec.min_rr_ratio),
            ("rr_status", spec.rr_status),
        )),
        ("allocation_policy", _ordered_dict(
            ("max_pct", spec.max_pct),
        )),
        ("risk_overlay", _ordered_dict(
            ("regime", spec.risk_overlay_regime),
            ("regime_cap", spec.risk_overlay_regime_cap),
            ("event_cap", spec.risk_overlay_event_cap),
            ("combined_cap", spec.risk_overlay_combined_cap),
            ("allow_new_entry", spec.risk_overlay_allow_new_entry),
            ("force_scale_in", spec.risk_overlay_force_scale_in),
            ("shadow_mode", spec.risk_overlay_shadow_mode),
            *([("blocked_reason", spec.risk_overlay_blocked_reason)] if spec.risk_overlay_blocked_reason else []),
            ("base_size_jpy", spec.risk_overlay_base_size_jpy),
            ("final_size_jpy", spec.risk_overlay_final_size_jpy),
            ("event", _ordered_dict(
                *([("name", spec.risk_overlay_event_name)] if spec.risk_overlay_event_name else []),
                *([("days_to_event", spec.risk_overlay_days_to_event)] if spec.risk_overlay_days_to_event is not None else []),
                ("tier", spec.risk_overlay_event_tier),
                *([("event_pressure", spec.risk_overlay_event_pressure)] if spec.risk_overlay_event_pressure is not None else []),
            )),
            ("max_risk_bps", spec.risk_overlay_max_risk_bps),
            *([("bps_limit_jpy", spec.risk_overlay_bps_limit_jpy)] if spec.risk_overlay_bps_limit_jpy is not None else []),
            ("event_state", spec.risk_overlay_event_state),
            ("allow_hold_through_event", spec.risk_overlay_allow_hold_through),
            ("post_event_cooldown_days", spec.risk_overlay_post_event_cooldown),
            *([("override_reason", spec.risk_overlay_override_reason)] if spec.risk_overlay_override_reason else []),
            ("portfolio", _ordered_dict(
                ("gross_jpy", spec.risk_overlay_portfolio_gross_jpy),
                *([("remaining_jpy", spec.risk_overlay_portfolio_remaining_jpy)] if spec.risk_overlay_portfolio_remaining_jpy is not None else []),
                ("active_positions", spec.risk_overlay_active_positions),
                *([("max_new_positions", spec.risk_overlay_max_new_positions)] if spec.risk_overlay_max_new_positions is not None else []),
                ("new_position_allowed", spec.risk_overlay_new_position_allowed),
            )),
            *([("commentary_tags", spec.risk_overlay_commentary_tags)] if spec.risk_overlay_commentary_tags else []),
        )),
        ("portfolio_plan", _ordered_dict(
            ("budget_total_jpy", spec.budget_total_jpy),
            ("allocation_pct", spec.allocation_pct),
            ("allocation_jpy", spec.allocation_jpy),
            *([("usd_jpy_rate", spec.usd_jpy_rate)] if spec.usd_jpy_rate is not None else []),
            ("instrument_lot", _ordered_dict(
                ("market", spec.market),
                ("lot_size", spec.lot_size),
                ("lot_policy", spec.lot_policy),
            )),
            ("quantity", spec.quantity),
            ("status", spec.portfolio_status),
            ("holdings", _ordered_dict(
                ("existing_shares", spec.existing_shares),
                ("existing_avg_cost", spec.existing_avg_cost),
                ("existing_investment_jpy", spec.existing_investment_jpy),
            )),
        )),
        ("execution_plan", _ordered_dict(
            ("order_style", spec.order_style),
            ("entry_rule", spec.entry_rule),
            ("notes", spec.execution_notes),
        )),
    )

    # PyYAML の Dumper に OrderedDict の出力を登録
    class OrderedDumper(yaml.SafeDumper):
        pass

    def _dict_representer(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

    OrderedDumper.add_representer(OrderedDict, _dict_representer)

    return yaml.dump(
        structure,
        Dumper=OrderedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def save_plan_spec(spec: PlanSpec, output_dir: Path) -> Path:
    """PlanSpec を YAML ファイルとして保存し、パスを返す"""
    output_dir.mkdir(parents=True, exist_ok=True)
    seq = get_next_plan_num(spec.ticker, output_dir)
    filename = f"{spec.ticker.upper()}_plan_{seq}.yaml"
    output_path = output_dir / filename
    yaml_str = build_yaml(spec)
    output_path.write_text(yaml_str, encoding="utf-8")
    return output_path
