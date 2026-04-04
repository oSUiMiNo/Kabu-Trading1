"""
Plan オーケストレーター

指定された1銘柄の final_judge / lanes を取得し、
決定論的な計算ですべての数値を確定させた上で、
エージェントに commentary フィールドを生成させ、PlanSpec YAML を出力する。
複数銘柄の並列実行は planning_batch.py（PJTルート）が担う。

Usage:
    python main.py <銘柄> <期間> [予算(円)] [リスク上限] [現在価格] [基準価格]

例:
    python main.py 楽天 中期
    python main.py 楽天 中期 5000000 50000
    python main.py NVDA 長期 5000000 5% - 135
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db, get_portfolio_config,
    get_latest_archivelog, get_archivelog_by_id, update_archivelog,
    list_event_masters,
)

from AgentUtil import call_agent, load_debug_config
from log_parser import parse_final_judge_from_db
from plan_calc import (
    Horizon, Market, Confidence, PlanConfig,
    check_freshness, check_price_deviation, calc_confidence, calc_allocation,
    calc_position_size, calc_rr_ratio,
    load_plan_config, apply_risk_overlay,
)
from risk_policy import evaluate_risk_overlay, load_risk_overlay_config
from plan_spec import PlanSpec, generate_plan_id, build_yaml, save_plan_spec

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


# ═══════════════════════════════════════════════════════
# Discord 通知（エラー種別で1日1回制限）
# ═══════════════════════════════════════════════════════

_notified_today: dict[str, str] = {}  # {error_type: date_str}


def _notify_planning_error(ticker: str, error_type: str, detail: str):
    """Planning エラーの Discord 通知。同じエラー種別は1日1回。"""
    from datetime import timezone, timedelta
    today_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    # 1日1回チェック（STOP_LOSS_ZERO は銘柄関係なく1回、それ以外は銘柄ごと）
    if error_type == "STOP_LOSS_ZERO":
        key = error_type
    else:
        key = f"{error_type}:{ticker}"

    if _notified_today.get(key) == today_str:
        return
    _notified_today[key] = today_str

    try:
        from discord_notifier import notify as _discord_notify
        from notification_types import NotifyLabel, NotifyPayload
        import asyncio

        payload = NotifyPayload(
            label=NotifyLabel.ERROR,
            ticker=ticker,
            monitor_data={},
            error_detail=f"Planning エラー: {error_type}\n{detail}",
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_discord_notify(payload))
        except RuntimeError:
            asyncio.run(_discord_notify(payload))
    except Exception as e:
        print(f"  [通知警告] Discord 通知失敗: {e}")


# ═══════════════════════════════════════════════════════
# ユーティリティ
# ═══════════════════════════════════════════════════════

_HORIZON_MAP: dict[str, str] = {
    "短期": "SHORT", "中期": "MID", "長期": "LONG",
    "short": "SHORT", "mid": "MID", "long": "LONG",
}

_HORIZON_JA: dict[str, str] = {
    "SHORT": "短期（数日〜数週間）",
    "MID": "中期（数週間〜数ヶ月）",
    "LONG": "長期（半年以上）",
}


def parse_risk_limit(value: str, budget: int) -> int:
    """
    risk_limit を解析する。

    "50000" → 50000（円）
    "5%" → budget * 5 / 100
    """
    if value.endswith("%"):
        pct = float(value[:-1])
        return int(budget * pct / 100)
    return int(value)


def detect_market(ticker: str) -> Market:
    """
    ティッカーコードから市場を推定する。

    - 4桁数字 → JP
    - 日本語文字を含む → JP
    - それ以外（英字）→ US
    """
    if ticker.isdigit() and len(ticker) == 4:
        return Market.JP
    if any("\u3000" <= c <= "\u9fff" for c in ticker):
        return Market.JP
    return Market.US


# ═══════════════════════════════════════════════════════
# 価格取得（エージェント呼び出し）
# ═══════════════════════════════════════════════════════

def _parse_price_result(agent_output: str) -> float | None:
    """
    price-fetcher エージェントの出力から current_price を抽出する。

    戦略1: YAML ブロックの price_result.current_price
    戦略2: "current_price: 数値" パターンのテキスト抽出（フォールバック）
    """
    # 戦略1: YAML ブロックをパース
    blocks = re.findall(r"```yaml\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
        except yaml.YAMLError:
            continue

        if not isinstance(data, dict):
            continue

        # price_result がトップレベルにネストされている場合と直接の場合の両方に対応
        price_result = data.get("price_result", data)
        if not isinstance(price_result, dict):
            continue

        # confidence チェック
        confidence = str(price_result.get("confidence", "")).upper()
        if confidence == "FAILED":
            print(f"  警告: price-fetcher が FAILED を返しました")
            return None

        price = price_result.get("current_price")
        if price is not None:
            try:
                val = float(price)
                if val > 0:
                    return val
            except (ValueError, TypeError):
                continue

    # 戦略2: テキストからフォールバック抽出
    m = re.search(r"current_price:\s*([\d.]+)", agent_output)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return val
        except ValueError:
            pass

    return None


MAX_AGENT_RETRIES = 3


async def _fetch_current_price(ticker: str, market: Market) -> float | None:
    """
    price-fetcher エージェントを呼び出して現在価格を取得する（最大3回リトライ）。

    Returns:
        取得成功時は float、失敗時は None。
    """
    prompt = (
        f"以下の銘柄の現在の株価を取得してください。\n"
        f"\n"
        f"ティッカー: {ticker}\n"
        f"市場: {market.value}\n"
    )

    agent_file = AGENTS_DIR / "price-fetcher.md"
    dbg = load_debug_config("price_fetch")

    for attempt in range(1, MAX_AGENT_RETRIES + 1):
        if attempt > 1:
            print(f"  価格取得 リトライ {attempt}/{MAX_AGENT_RETRIES}")
        try:
            result = await call_agent(prompt, file_path=str(agent_file), **{**dbg, "show_cost": True})

            if result and result.cost:
                print(f"  コスト: ${result.cost:.4f}")

            if not result or not result.text:
                print(f"  価格取得 警告: 応答なし")
                continue

            price = _parse_price_result(result.text)
            if price is not None:
                return price
            print(f"  価格取得 警告: パース失敗")
        except Exception as e:
            print(f"  価格取得 エラー: {e}")

    return None


# ═══════════════════════════════════════════════════════
# commentary 生成（エージェント呼び出し）
# ═══════════════════════════════════════════════════════

def build_commentary_prompt(
    spec: PlanSpec,
    raw_judge_text: str,
    additional_texts: list[tuple[str, str]] | None = None,
) -> str:
    """
    plan-generator エージェントに渡すプロンプトを組み立てる。

    エージェントの責務:
    - decision_basis の why_it_matters を日本語で記述
    - execution_notes に状況に応じた注記を追加
    - 全ての数値はオーケストレーター算出済み。エージェントは数値を変更しない。
    - additional_texts（議論ログ・判定ログ）を参照して commentary を補強

    Args:
        additional_texts: [(ラベル, テキスト内容), ...] 形式の追加コンテキスト
    """
    yaml_str = build_yaml(spec)

    additional_section = ""
    if additional_texts:
        parts = []
        for label, content in additional_texts:
            parts.append(f"--- {label} ---\n{content}\n")
        additional_section = (
            f"\n"
            f"【参考：Analyzer ログ】\n"
            f"{''.join(parts)}"
        )

    return (
        f"以下の PlanSpec（機械算出済み）に対して、テキスト応答として commentary フィールドを生成してください。\n"
        f"\n"
        f"【PlanSpec（確定済み数値）】\n"
        f"```yaml\n{yaml_str}```\n"
        f"\n"
        f"【最終判定ログ（プロンプト埋め込み分）】\n"
        f"--- ここから ---\n"
        f"{raw_judge_text}\n"
        f"--- ここまで ---\n"
        f"{additional_section}"
        f"\n"
        f"【あなたの作業】\n"
        f"1. 銘柄「{spec.ticker}」に関する最新ニュースや市場状況をWeb検索で確認\n"
        f"2. 追加の Analyzer ログがある場合は参照し、根拠の詳細や議論の文脈を把握する\n"
        f"3. decision_basis の各項目に why_it_matters（結論の決め手になった理由を日本語1文。最新情報があれば言及）を付与\n"
        f"4. execution_notes に追加すべき注記があれば追加（価格ズレ警告、鮮度警告、市況注記など）\n"
        f"5. 結果を YAML 形式で出力。数値フィールドは一切変更しないこと。\n"
        f"\n"
        f"出力は YAML ブロック（```yaml ... ```）のみ。説明文は不要。\n"
    )


def _merge_commentary(spec: PlanSpec, agent_output: str) -> None:
    """
    エージェント出力から commentary フィールドを PlanSpec に反映する。

    エージェントが返す YAML ブロックをパースし、以下のフィールドのみを上書き:
    - decision_basis[].why_it_matters
    - execution_plan.notes
    """
    # ```yaml ... ``` ブロックを抽出
    blocks = re.findall(r"```yaml\n(.*?)```", agent_output, re.DOTALL)
    if not blocks:
        return

    try:
        data = yaml.safe_load(blocks[-1].strip())
    except yaml.YAMLError:
        return

    if not isinstance(data, dict):
        return

    # decision_basis の why_it_matters を上書き
    agent_basis = (data.get("decision", {}) or {}).get("decision_basis", [])
    if isinstance(agent_basis, list):
        for i, ab in enumerate(agent_basis):
            if i < len(spec.decision_basis) and isinstance(ab, dict):
                wim = ab.get("why_it_matters")
                if wim:
                    spec.decision_basis[i]["why_it_matters"] = str(wim)

    # execution_plan.notes を上書き
    agent_exec = data.get("execution_plan", {})
    if isinstance(agent_exec, dict):
        notes = agent_exec.get("notes")
        if isinstance(notes, list) and notes:
            spec.execution_notes = [str(n) for n in notes]


# ═══════════════════════════════════════════════════════
# メインフロー
# ═══════════════════════════════════════════════════════

async def run_plan(
    ticker: str,
    budget_total_jpy: int,
    risk_limit: str,
    horizon: str,
    current_price: float | None = None,
    anchor_price: float | None = None,
    config: PlanConfig | None = None,
    archive_id: str | None = None,
) -> Path | None:
    """
    Plan オーケストレーターのメイン関数。

    フロー:
    1. DB からセッション取得 (final_judge / lanes)
    1.5. 価格取得 (price-fetcher, current_price 未指定時)
    2. 鮮度チェック (plan_calc)
    3. 価格ズレ判定 (plan_calc)
    4. confidence 算出 (plan_calc)
    5. 配分・株数計算 (plan_calc)
    6. PlanSpec 組立 (plan_spec)
    7. エージェント呼び出し (commentary 生成)
    8. commentary 反映 → 最終 PlanSpec YAML 保存
    """
    now = datetime.now()
    t = ticker.upper()
    h = Horizon[_HORIZON_MAP.get(horizon, horizon)]
    market = detect_market(ticker)
    risk_jpy = parse_risk_limit(risk_limit, budget_total_jpy)
    cfg = config or PlanConfig()

    print(f"{'='*60}")
    print(f"=== Plan オーケストレーター: {t} ===")
    print(f"=== 投資期間: {_HORIZON_JA.get(horizon, horizon)} ===")
    print(f"{'='*60}")

    # --- 1. DB からセッション取得 ---
    if archive_id:
        _db_archivelog = safe_db(get_archivelog_by_id, archive_id)
    else:
        _db_archivelog = safe_db(get_latest_archivelog, t)
    if not _db_archivelog:
        print(f"  エラー: {t} のセッションが DB に見つかりません")
        sys.exit(1)

    fj_data = _db_archivelog.get("final_judge") or {}
    if isinstance(fj_data, str):
        fj_data = json.loads(fj_data)
    if not fj_data:
        print(f"  エラー: {t} のセッションに final_judge がありません")
        sys.exit(1)

    created_at = _db_archivelog.get("created_at", "")
    judgment = parse_final_judge_from_db(fj_data, ticker, created_at)
    print(f"  判定: {judgment.decision}（raw: {judgment.decision_raw}）")
    print(f"  投票: for={judgment.vote_for}, against={judgment.vote_against}")
    print(f"  一致度: {judgment.overall_agreement}")

    lanes_raw = _db_archivelog.get("lanes") or {}
    if isinstance(lanes_raw, str):
        lanes_raw = json.loads(lanes_raw)
    additional_texts: list[tuple[str, str]] = []
    for lane_id, lane_data in sorted(lanes_raw.items()):
        if isinstance(lane_data, dict):
            disc_md = lane_data.get("discussion_md", "")
            if disc_md:
                additional_texts.append((f"set{lane_id} 議論", disc_md))
            judge_md = lane_data.get("judge_md", "")
            if judge_md:
                additional_texts.append((f"set{lane_id} 判定", judge_md))
    if additional_texts:
        print(f"  追加テキスト: {len(additional_texts)} 件")
    print()

    # --- 1.5 価格取得（current_price 未指定時）+ 為替レート ---
    #   優先順: archive.technical.latest_price → archive.monitor.current_price → price-fetcher
    technical_data = _db_archivelog.get("technical")
    if isinstance(technical_data, dict):
        usd_jpy_rate = technical_data.get("usd_jpy_rate")
    else:
        usd_jpy_rate = None

    if current_price is None:
        if technical_data and isinstance(technical_data, dict) and technical_data.get("latest_price"):
            current_price = technical_data["latest_price"]
            print(f">>> 価格取得（archive.technical から）: {current_price}")
        else:
            monitor_data = _db_archivelog.get("monitor")
            if monitor_data and isinstance(monitor_data, dict) and monitor_data.get("current_price"):
                current_price = monitor_data["current_price"]
                print(f">>> 価格取得（archive.monitor から）: {current_price}")
            else:
                print(f">>> 価格取得（price-fetcher エージェント）")
                fetched = await _fetch_current_price(ticker, market)
                if fetched is not None:
                    current_price = fetched
                    print(f"  取得成功: {current_price}")
                else:
                    print(f"  エラー: 価格取得に失敗しました。現在価格を手動指定してください。")
                    sys.exit(1)

    if anchor_price is None:
        anchor_price = current_price
        print(f"  基準価格: 省略 → 現在価格と同値 ({anchor_price})")

    # --- 保有状況の取得（archive.technical.holdings_snapshot） ---
    _p_tech = (_db_archivelog.get("technical") or {}) if isinstance(_db_archivelog.get("technical"), dict) else {}
    _p_hs = _p_tech.get("holdings_snapshot") or {}
    existing_shares = _p_hs.get("shares", 0) or 0
    existing_avg_cost = _p_hs.get("avg_cost", 0) or 0
    _rate = usd_jpy_rate if market == Market.US and usd_jpy_rate else 1.0
    existing_investment_jpy = existing_avg_cost * existing_shares * _rate
    if existing_shares:
        print(f"  保有状況: {existing_shares}株（平均{existing_avg_cost}、投入額{existing_investment_jpy:,.0f}円）")
    else:
        print(f"  保有状況: なし")

    print()

    # --- 2. 鮮度チェック ---
    freshness = check_freshness(judgment.log_date, now, h, config=cfg)
    print(f"  鮮度: {freshness.status}（{freshness.log_age_days}日 / 上限{freshness.max_allowed_days}日）")

    # --- 3. 価格ズレ判定 ---
    deviation = check_price_deviation(current_price, anchor_price, h, config=cfg)
    print(f"  価格ズレ: {deviation.price_deviation_pct}%（許容{deviation.price_tolerance_pct}% / ブロック{deviation.price_block_pct}%）→ {deviation.status}")

    # --- 4. confidence ---
    p, confidence = calc_confidence(judgment.vote_for, judgment.vote_against)
    print(f"  confidence: {confidence.value}（p={p}）")

    # --- 4.5 Risk Overlay 評価 ---
    _db_config_for_overlay = safe_db(get_portfolio_config) or {}
    overlay_cfg = load_risk_overlay_config(_db_config_for_overlay)
    ii = _db_archivelog.get("important_indicators")
    event_masters = safe_db(list_event_masters) or []
    stop_loss = cfg.stop_loss_pct[h]
    risk_overlay = evaluate_risk_overlay(ii, stop_loss, event_masters, overlay_cfg)
    print(f"  Risk Overlay: regime={risk_overlay.regime_state.value}(cap={risk_overlay.regime_cap})"
          f" event_cap={risk_overlay.event_cap} combined={risk_overlay.combined_cap}"
          f" new_entry={'OK' if risk_overlay.allow_new_entry else 'BLOCKED'}"
          f" shadow={risk_overlay.shadow_mode}")
    if risk_overlay.blocked_reason:
        print(f"    blocked: {risk_overlay.blocked_reason}")

    # --- 5. ポジションサイジング ---
    pos_size = calc_position_size(budget_total_jpy, float(risk_limit.rstrip('%')) if risk_limit.endswith('%') else risk_jpy / budget_total_jpy * 100, stop_loss, existing_investment_jpy=existing_investment_jpy)
    print(f"  ポジションサイジング: 許容損失{pos_size.max_loss_jpy:,}円 → 投入上限{pos_size.position_size_jpy:,}円")

    # --- 6. 配分計算（ポジションサイジング制限付き） ---
    allocation = calc_allocation(budget_total_jpy, confidence, current_price, market, risk_jpy, config=cfg, usd_jpy_rate=usd_jpy_rate, position_size_jpy=pos_size.position_size_jpy, existing_investment_jpy=existing_investment_jpy)
    pos_size.position_size_limited = allocation.allocation_jpy < pos_size.position_size_jpy and allocation.allocation_jpy < int(budget_total_jpy * cfg.max_allocation_pct[confidence] / 100)
    # position_size_limited: ポジションサイジングで実際に制限された場合のみ True
    if pos_size.position_size_jpy < int(budget_total_jpy * cfg.max_allocation_pct[confidence] / 100):
        pos_size.position_size_limited = True
        print(f"  ※ ポジションサイジングにより投入額を制限")
    print(f"  配分: {allocation.allocation_pct}% = {allocation.allocation_jpy:,}円")
    print(f"  株数: {allocation.quantity}（{allocation.market.value} lot={allocation.lot_size}）→ {allocation.status}")

    # --- 6.5 Risk Overlay 適用 ---
    is_new_entry = existing_shares == 0
    risk_adjusted = apply_risk_overlay(
        allocation, risk_overlay, current_price, market,
        usd_jpy_rate=usd_jpy_rate, is_new_entry=is_new_entry,
    )
    if not risk_overlay.shadow_mode:
        from plan_calc import AllocationResult
        allocation = AllocationResult(
            budget_total_jpy=allocation.budget_total_jpy,
            allocation_pct=allocation.allocation_pct,
            allocation_jpy=risk_adjusted.final_size_jpy,
            market=allocation.market,
            lot_size=allocation.lot_size,
            quantity=risk_adjusted.final_quantity,
            status="BLOCKED_BY_RISK_OVERLAY" if risk_adjusted.blocked else allocation.status,
        )
        if risk_adjusted.blocked:
            print(f"  ★ Risk Overlay BLOCKED: {risk_adjusted.blocked_reason}")
        elif risk_adjusted.combined_cap < 1.0:
            print(f"  ★ Risk Overlay 適用: {risk_adjusted.base_size_jpy:,}円 → {risk_adjusted.final_size_jpy:,}円"
                  f"（×{risk_adjusted.combined_cap}）{risk_adjusted.final_quantity}株")
    else:
        if risk_adjusted.combined_cap < 1.0 or risk_adjusted.blocked:
            print(f"  [Shadow] Risk Overlay: {risk_adjusted.base_size_jpy:,}円 → {risk_adjusted.final_size_jpy:,}円"
                  f"（×{risk_adjusted.combined_cap}）{'BLOCKED' if risk_adjusted.blocked else ''}")

    # --- 7. RR比計算 ---
    take_profit = cfg.default_take_profit_pct
    rr = calc_rr_ratio(stop_loss, take_profit, cfg.min_rr_ratio)
    print(f"  RR比: {rr.rr_ratio}（損切り{stop_loss}% / 利確{take_profit}%）→ {rr.status}")
    print()

    # --- 6. PlanSpec 組立 ---
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    from plan_spec import get_next_plan_num
    seq = get_next_plan_num(ticker, LOGS_DIR)
    plan_id = generate_plan_id(ticker, seq)

    # decision_basis を dict リストに変換
    basis_dicts = [
        {"lane": b.lane, "source_desc": b.source_desc, "source_url": b.source_url, "why_it_matters": b.text}
        for b in judgment.decision_basis
    ]

    spec = PlanSpec(
        ticker=t,
        plan_id=plan_id,
        decision_final=judgment.decision,
        vote_for=judgment.vote_for,
        vote_against=judgment.vote_against,
        horizon=h.value,
        p=p,
        confidence=confidence.value,
        decision_basis=basis_dicts,
        # freshness
        log_age_days=freshness.log_age_days,
        max_allowed_days=freshness.max_allowed_days,
        freshness_status=freshness.status,
        # data_checks
        anchor_price=deviation.anchor_price,
        current_price=deviation.current_price,
        price_deviation_pct=deviation.price_deviation_pct,
        price_tolerance_pct=deviation.price_tolerance_pct,
        price_block_pct=deviation.price_block_pct,
        data_checks_status=deviation.status,
        # risk_management
        max_loss_jpy=pos_size.max_loss_jpy,
        position_size_jpy=pos_size.position_size_jpy,
        position_size_limited=pos_size.position_size_limited,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        rr_ratio=rr.rr_ratio,
        min_rr_ratio=rr.min_rr_ratio,
        rr_status=rr.status,
        # allocation_policy
        max_pct=cfg.max_allocation_pct[confidence],
        # portfolio_plan
        budget_total_jpy=allocation.budget_total_jpy,
        allocation_pct=allocation.allocation_pct,
        allocation_jpy=allocation.allocation_jpy,
        usd_jpy_rate=usd_jpy_rate,
        market=market.value,
        lot_size=allocation.lot_size,
        quantity=allocation.quantity,
        portfolio_status=allocation.status,
        # holdings
        existing_shares=existing_shares,
        existing_avg_cost=existing_avg_cost,
        existing_investment_jpy=existing_investment_jpy,
        # risk_overlay
        risk_overlay_regime=risk_overlay.regime_state.value,
        risk_overlay_regime_cap=risk_overlay.regime_cap,
        risk_overlay_event_cap=risk_overlay.event_cap,
        risk_overlay_combined_cap=risk_overlay.combined_cap,
        risk_overlay_allow_new_entry=risk_overlay.allow_new_entry,
        risk_overlay_force_scale_in=risk_overlay.force_scale_in,
        risk_overlay_shadow_mode=risk_overlay.shadow_mode,
        risk_overlay_blocked_reason=risk_overlay.blocked_reason,
        risk_overlay_base_size_jpy=risk_adjusted.base_size_jpy,
        risk_overlay_final_size_jpy=risk_adjusted.final_size_jpy,
        risk_overlay_event_name=risk_overlay.event_name,
        risk_overlay_days_to_event=risk_overlay.days_to_event,
        risk_overlay_event_tier=risk_overlay.event_tier.value,
        risk_overlay_event_pressure=risk_overlay.event_pressure,
    )

    # BLOCK / STALE / RR_TOO_LOW の場合はプランを差し替え + Discord 通知
    if deviation.status == "BLOCK_REEVALUATE":
        spec.execution_notes = ["価格ズレ ±10%超: 停止→再評価要求。数量確定しない。"]
        spec.quantity = 0
        spec.portfolio_status = "BLOCK_REEVALUATE"
        _notify_planning_error(t, "BLOCK_REEVALUATE", f"価格ズレ {deviation.price_deviation_pct}% > {deviation.price_block_pct}%")

    if rr.status == "RR_TOO_LOW":
        spec.execution_notes.append(
            f"RR比 {rr.rr_ratio} < {rr.min_rr_ratio}: 期待値がマイナスのため実行不可。利確ラインの見直しが必要。"
        )
        spec.quantity = 0
        spec.portfolio_status = "RR_TOO_LOW"
        _notify_planning_error(t, "RR_TOO_LOW", f"RR比 {rr.rr_ratio} < {rr.min_rr_ratio}")

    if stop_loss == 0:
        _notify_planning_error(t, "STOP_LOSS_ZERO", "stop_loss_pct が 0 のためポジションサイジング計算不可")

    if freshness.status == "STALE_REEVALUATE":
        spec.execution_notes.append(
            f"ログ鮮度超過（{freshness.log_age_days}日 > {freshness.max_allowed_days}日）: 再評価推奨"
        )

    if risk_adjusted.blocked and not risk_overlay.shadow_mode:
        spec.execution_notes.append(f"Risk Overlay BLOCKED: {risk_adjusted.blocked_reason}")
        spec.quantity = 0
        spec.portfolio_status = "BLOCKED_BY_RISK_OVERLAY"
    elif risk_overlay.force_scale_in and not risk_overlay.shadow_mode:
        spec.order_style = "SCALE_IN"
        spec.execution_notes.append("Risk Overlay: STRESS/CRISIS のため分割エントリー強制")

    # --- 重要指標データを commentary に注入 ---
    # ii はステップ4.5で取得済み
    if ii and isinstance(ii, dict):
        ii_lines = []
        market_ii = ii.get("market", {})
        if market_ii:
            items = []
            if market_ii.get("vix") is not None:
                items.append(f"VIX {market_ii['vix']}")
            if market_ii.get("us_10y_yield") is not None:
                items.append(f"米10年債 {market_ii['us_10y_yield']}%")
            if market_ii.get("ffr") is not None:
                items.append(f"FRB金利 {market_ii['ffr']}%")
            if market_ii.get("boj_rate") is not None:
                items.append(f"日銀金利 {market_ii['boj_rate']}%")
            if items:
                ii_lines.append(f"市場環境: {', '.join(items)}")

        event = ii.get("event_risk", {})
        if event.get("nearest_event"):
            ev_text = f"イベントリスク: {event['nearest_event']} まで {event.get('days_to_event', '?')}日"
            if event.get("implied_move_pct"):
                ev_text += f"（期待変動 {event['implied_move_pct']}%）"
            ii_lines.append(ev_text)

        vol = ii.get("volume", {})
        if vol.get("dollar_volume") is not None:
            currency = vol.get("currency", "USD")
            if currency == "JPY":
                ii_lines.append(f"売買代金: {vol['dollar_volume']/100_000_000:,.0f}億円")
            else:
                ii_lines.append(f"売買代金: ${vol['dollar_volume']/1_000_000:,.0f}M")

        if ii_lines:
            additional_texts.append(("重要指標（API取得データ）", "\n".join(ii_lines)))

    # 保有状況をコンテキストに追加
    if existing_shares:
        _current_val = existing_shares * current_price * _rate
        _unrealized = _current_val - existing_investment_jpy
        _unrealized_pct = (_unrealized / existing_investment_jpy * 100) if existing_investment_jpy else 0
        holdings_lines = [
            f"保有数: {existing_shares}株",
            f"平均取得単価: {existing_avg_cost}",
            f"投入額: {existing_investment_jpy:,.0f}円",
            f"現在評価額: {_current_val:,.0f}円",
            f"含み損益: {_unrealized:+,.0f}円（{_unrealized_pct:+.1f}%）",
        ]
        additional_texts.append(("現在の保有状況", "\n".join(holdings_lines)))
    else:
        additional_texts.append(("現在の保有状況", "保有なし"))

    # --- エージェント呼び出し（commentary 生成、リトライ付き） ---
    print(f">>> commentary 生成（plan-generator エージェント）")
    prompt = build_commentary_prompt(spec, judgment.raw_text, additional_texts)
    agent_file = AGENTS_DIR / "plan-generator.md"

    dbg = load_debug_config("plan")

    commentary_result = None
    for attempt in range(1, MAX_AGENT_RETRIES + 1):
        if attempt > 1:
            print(f"  commentary 生成 リトライ {attempt}/{MAX_AGENT_RETRIES}")
        try:
            result = await call_agent(prompt, file_path=str(agent_file), **{**dbg, "show_cost": True})
            if result and result.cost:
                print(f"  コスト: ${result.cost:.4f}")
            if result and result.text:
                commentary_result = result
                break
            print(f"  commentary 生成 警告: 応答なし")
        except Exception as e:
            print(f"  commentary 生成 エラー: {e}")

    # --- 8. commentary 反映 ---
    if commentary_result and commentary_result.text:
        _merge_commentary(spec, commentary_result.text)
        print(f"  commentary 反映完了")
    else:
        print(f"  警告: 全リトライ失敗。数値のみの PlanSpec を出力します")

    # --- 9. 最終 YAML 保存 ---
    output_path = save_plan_spec(spec, LOGS_DIR)
    print()
    print(f"{'='*60}")
    print(f"=== PlanSpec 出力完了 ===")
    print(f"  ファイル: {output_path.name}")
    print(f"  パス: {output_path}")
    print(f"{'='*60}")

    # --- 10. DB 書き込み（archive.newplan_full / verdict） ---
    _db_archivelog_id = _db_archivelog["id"] if _db_archivelog else None
    if _db_archivelog_id:
        yaml_full = build_yaml(spec)
        safe_db(update_archivelog, _db_archivelog_id,
                newplan_full=yaml_full,
                verdict=spec.decision_final,
                status="completed")

    return output_path


# ═══════════════════════════════════════════════════════
# CLI エントリーポイント
# ═══════════════════════════════════════════════════════

def _parse_optional_float(argv: list[str], idx: int) -> float | None:
    """CLI引数を float に変換。省略または "-" なら None を返す。"""
    if len(argv) <= idx:
        return None
    val = argv[idx]
    if val == "-":
        return None
    return float(val)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("使い方:")
        print("  python main.py <銘柄> <期間> [予算] [リスク上限]    # 手動指定")
        print()
        print("  バッチ実行は planning_batch.py を使用してください。")
        print()
        print("  期間（必須）: '短期' / '中期' / '長期'")
        print("  予算・リスク上限は省略時に Supabase DB から自動取得")
        sys.exit(1)

    if sys.argv[2] not in _HORIZON_MAP:
        print(f"エラー: 期間 '{sys.argv[2]}' は無効です。'短期' / '中期' / '長期' のいずれかを指定してください。")
        sys.exit(1)

    # --archive-id をパース（位置引数の前後どちらでも対応）
    _archive_id = None
    _filtered_argv = [sys.argv[0]]
    _i = 1
    while _i < len(sys.argv):
        if sys.argv[_i] == "--archive-id" and _i + 1 < len(sys.argv):
            _archive_id = sys.argv[_i + 1]
            _i += 2
        else:
            _filtered_argv.append(sys.argv[_i])
            _i += 1
    sys.argv = _filtered_argv

    ticker = sys.argv[1]
    horizon = _HORIZON_MAP[sys.argv[2]]

    # DB からデフォルト値を取得（CLI 引数で上書き可能）
    _db_config = safe_db(get_portfolio_config) or {}
    def _cli_or_db_int(idx: int, db_key: str, db_src: dict = _db_config) -> int:
        if len(sys.argv) > idx and sys.argv[idx] != "-":
            return int(sys.argv[idx])
        val = db_src.get(db_key)
        return int(val) if val is not None else 0

    def _cli_or_db_float(idx: int, db_key: str, db_src: dict = _db_config) -> float:
        if len(sys.argv) > idx and sys.argv[idx] != "-":
            return float(sys.argv[idx])
        val = db_src.get(db_key)
        return float(val) if val is not None else 0.0

    budget = _cli_or_db_int(3, "total_budget_jpy")
    if len(sys.argv) > 4 and sys.argv[4] != "-":
        risk_limit = sys.argv[4]
    elif _db_config.get("risk_limit_pct") is not None:
        risk_limit = f"{_db_config['risk_limit_pct']}%"
    else:
        risk_limit = "5%"
    current_price = _parse_optional_float(sys.argv, 5)
    anchor_price = _parse_optional_float(sys.argv, 6)

    if budget == 0:
        print("エラー: 予算が 0 です。CLI 引数または Supabase の portfolio_config を設定してください。")
        sys.exit(1)

    plan_config = load_plan_config(_db_config)

    print(f"[設定] 予算={budget:,}円 リスク上限={risk_limit}")
    if _db_config:
        print(f"  (DB フォールバック有効)")

    anyio.run(lambda: run_plan(
        ticker, budget, risk_limit, horizon,
        current_price, anchor_price,
        config=plan_config,
        archive_id=_archive_id,
    ))
