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
    decision_final: str        # BUY | NO_BUY | SELL | NO_SELL
    vote_for: int
    vote_against: int
    horizon: str               # SHORT | MID | LONG
    p: float
    confidence: str            # HIGH | MED | LOW
    decision_basis: list[dict] = field(default_factory=list)
    # 各 dict: {"fact_id": "F12", "source_id": "S3", "why_it_matters": "..."}

    # freshness ブロック
    log_age_days: int = 0
    max_allowed_days: int = 0
    freshness_status: str = "OK"

    # data_checks ブロック
    anchor_price: float = 0.0
    current_price: float = 0.0
    price_deviation_pct: float = 0.0
    deviation_ok_pct: float = 0.0
    deviation_block_pct: float = 10.0
    data_checks_status: str = "OK"

    # risk_defaults ブロック
    stop_loss_pct: float = 0.0
    stop_loss_cap_pct: float = -20.0

    # allocation_policy ブロック
    max_pct: float = 10.0
    min_pct: float = 2.0
    cash_min_pct: float = 25.0
    confidence_multiplier: float = 1.0

    # portfolio_plan ブロック
    budget_total_jpy: int = 0
    cash_reserved_jpy: int = 0
    allocation_pct: float = 0.0
    allocation_jpy: int = 0
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

    # monitoring_hint ブロック
    monitoring_intensity: str = "NORMAL"
    monitoring_reason: str = ""


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
            ("fact_id", b.get("fact_id", "")),
            ("source_id", b.get("source_id", "")),
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
            ("deviation_ok_pct", spec.deviation_ok_pct),
            ("deviation_block_pct", spec.deviation_block_pct),
            ("status", spec.data_checks_status),
        )),
        ("risk_defaults", _ordered_dict(
            ("stop_loss_pct", spec.stop_loss_pct),
            ("stop_loss_cap_pct", spec.stop_loss_cap_pct),
        )),
        ("allocation_policy", _ordered_dict(
            ("max_pct", spec.max_pct),
            ("min_pct", spec.min_pct),
            ("cash_min_pct", spec.cash_min_pct),
            ("confidence_multiplier", spec.confidence_multiplier),
        )),
        ("portfolio_plan", _ordered_dict(
            ("budget_total_jpy", spec.budget_total_jpy),
            ("cash_reserved_jpy", spec.cash_reserved_jpy),
            ("allocation_pct", spec.allocation_pct),
            ("allocation_jpy", spec.allocation_jpy),
            ("instrument_lot", _ordered_dict(
                ("market", spec.market),
                ("lot_size", spec.lot_size),
                ("lot_policy", spec.lot_policy),
            )),
            ("quantity", spec.quantity),
            ("status", spec.portfolio_status),
        )),
        ("execution_plan", _ordered_dict(
            ("order_style", spec.order_style),
            ("entry_rule", spec.entry_rule),
            ("notes", spec.execution_notes),
        )),
        ("monitoring_hint", _ordered_dict(
            ("intensity", spec.monitoring_intensity),
            ("reason", spec.monitoring_reason),
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
