"""
Plan オーケストレーター

Discussion プロジェクトのログ一式（議論・判定・最終判定）を受け取り、
決定論的な計算ですべての数値を確定させた上で、
エージェントに commentary フィールド (decision_basis の why_it_matters,
monitoring_hint の reason, execution_notes) を生成させ、
PlanSpec YAML を出力する。

Usage:
    python plan_orchestrator.py <銘柄> <セッションDir> <期間> [予算(円)] [リスク上限] [現在価格] [基準価格]

    現在価格を省略すると price-fetcher エージェントがWeb検索で自動取得する。
    基準価格を省略すると現在価格と同値（ズレ0%）になる。
    "-" を指定しても省略扱い。

例:
    python plan_orchestrator.py 楽天 "C:\\...\\Discusion\\logs" 中期
    python plan_orchestrator.py 楽天 "C:\\...\\Discusion\\logs" 中期 5000000 50000
    python plan_orchestrator.py NVDA "C:\\...\\logs\\260210_1200" 長期 5000000 5% - 135
"""
import re
import sys
from datetime import datetime
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db, get_portfolio_config,
    get_latest_session, update_session,
)

from AgentUtil import call_agent, load_debug_config
from log_parser import SessionLogs, find_session_logs, parse_final_judge
from plan_calc import (
    Horizon, Market, Confidence, PlanConfig,
    check_freshness, check_price_deviation, calc_confidence, calc_allocation,
    load_plan_config, MONITORING_INTENSITY,
)
from plan_spec import PlanSpec, generate_plan_id, build_yaml, save_plan_spec

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


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
            result = await call_agent(prompt, file_path=str(agent_file), show_cost=True, **dbg)

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
    additional_file_paths: list[Path] | None = None,
) -> str:
    """
    plan-generator エージェントに渡すプロンプトを組み立てる。

    エージェントの責務:
    - decision_basis の why_it_matters を日本語で記述
    - monitoring_hint.reason を生成
    - execution_notes に状況に応じた注記を追加
    - 全ての数値はオーケストレーター算出済み。エージェントは数値を変更しない。
    - 追加ファイル（議論ログ・意見書・判定・アクションプラン）を Read で参照して commentary を補強
    """
    yaml_str = build_yaml(spec)

    additional_files_section = ""
    if additional_file_paths:
        paths_text = "\n".join(f"  - {p}" for p in additional_file_paths)
        additional_files_section = (
            f"\n"
            f"【参照可能な追加ファイル（必要に応じて Read で読んでください）】\n"
            f"Discussion プロジェクトが出力した議論・判定ログです。\n"
            f"議論ログ（set*.md）は大きいため必要な部分だけ読めば十分です。\n"
            f"{paths_text}\n"
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
        f"{additional_files_section}"
        f"\n"
        f"【あなたの作業】\n"
        f"1. 銘柄「{spec.ticker}」に関する最新ニュースや市場状況をWeb検索で確認\n"
        f"2. 追加ファイルがある場合は Read で参照し、根拠の詳細や議論の文脈を把握する\n"
        f"3. decision_basis の各項目に why_it_matters（結論の決め手になった理由を日本語1文。最新情報があれば言及）を付与\n"
        f"4. monitoring_hint.reason を生成（投票状況・confidence・freshness + 直近イベントを踏まえた1文）\n"
        f"5. execution_notes に追加すべき注記があれば追加（価格ズレ警告、鮮度警告、市況注記など）\n"
        f"6. 結果を YAML 形式で出力。数値フィールドは一切変更しないこと。\n"
        f"\n"
        f"出力は YAML ブロック（```yaml ... ```）のみ。説明文は不要。\n"
    )


def _merge_commentary(spec: PlanSpec, agent_output: str) -> None:
    """
    エージェント出力から commentary フィールドを PlanSpec に反映する。

    エージェントが返す YAML ブロックをパースし、以下のフィールドのみを上書き:
    - decision_basis[].why_it_matters
    - monitoring_hint.reason
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

    # monitoring_hint.reason を上書き
    agent_monitoring = data.get("monitoring_hint", {})
    if isinstance(agent_monitoring, dict):
        reason = agent_monitoring.get("reason")
        if reason:
            spec.monitoring_reason = str(reason)

    # execution_plan.notes を上書き
    agent_exec = data.get("execution_plan", {})
    if isinstance(agent_exec, dict):
        notes = agent_exec.get("notes")
        if isinstance(notes, list) and notes:
            spec.execution_notes = [str(n) for n in notes]


# ═══════════════════════════════════════════════════════
# メインフロー
# ═══════════════════════════════════════════════════════

async def run_plan_orchestrator(
    ticker: str,
    session_dir: str,
    budget_total_jpy: int,
    risk_limit: str,
    horizon: str,
    current_price: float | None = None,
    anchor_price: float | None = None,
    config: PlanConfig | None = None,
) -> Path | None:
    """
    Plan オーケストレーターのメイン関数。

    フロー:
    1. ログ解析 (log_parser)
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
    h = Horizon[horizon]
    market = detect_market(ticker)
    risk_jpy = parse_risk_limit(risk_limit, budget_total_jpy)
    cfg = config or PlanConfig()

    print(f"{'='*60}")
    print(f"=== Plan オーケストレーター: {t} ===")
    print(f"=== 投資期間: {_HORIZON_JA.get(horizon, horizon)} ===")
    print(f"{'='*60}")

    # --- 1. ログ解析 ---
    session_path = Path(session_dir)
    logs = find_session_logs(session_path, ticker)
    if not logs.final_judge:
        print(f"  エラー: {session_path} に final_judge ログが見つかりません")
        return None

    print(f"  ログ: {logs.final_judge.name}")
    judgment = parse_final_judge(logs.final_judge)
    print(f"  判定: {judgment.decision}（raw: {judgment.decision_raw}）")
    print(f"  投票: for={judgment.vote_for}, against={judgment.vote_against}")
    print(f"  一致度: {judgment.overall_agreement}")

    additional_file_paths: list[Path] = [
        *logs.set_files,
        *logs.judge_files,
    ]
    if additional_file_paths:
        print(f"  追加ファイル: {len(additional_file_paths)} 件")
        for p in additional_file_paths:
            print(f"    - {p.name}")
    print()

    # --- 1.5 価格取得（current_price 未指定時）---
    if current_price is None:
        print(f">>> 価格取得（price-fetcher エージェント）")
        fetched = await _fetch_current_price(ticker, market)
        if fetched is not None:
            current_price = fetched
            print(f"  取得成功: {current_price}")
        else:
            print(f"  エラー: 価格取得に失敗しました。現在価格を手動指定してください。")
            return None

    if anchor_price is None:
        anchor_price = current_price
        print(f"  基準価格: 省略 → 現在価格と同値 ({anchor_price})")

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

    # --- 5. 配分計算 ---
    allocation = calc_allocation(budget_total_jpy, confidence, current_price, market, risk_jpy, config=cfg)
    print(f"  配分: {allocation.allocation_pct}% = {allocation.allocation_jpy:,}円")
    print(f"  株数: {allocation.quantity}（{allocation.market.value} lot={allocation.lot_size}）→ {allocation.status}")
    print()

    # --- 6. PlanSpec 組立 ---
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    from plan_spec import get_next_plan_num
    seq = get_next_plan_num(ticker, LOGS_DIR)
    plan_id = generate_plan_id(ticker, seq)

    # decision_basis を dict リストに変換
    basis_dicts = [
        {"fact_id": b.fact_id, "source_id": b.source_id, "why_it_matters": b.text}
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
        # risk_defaults
        stop_loss_pct=cfg.stop_loss_pct[h],
        # allocation_policy
        max_pct=cfg.max_allocation_pct[confidence],
        # portfolio_plan
        budget_total_jpy=allocation.budget_total_jpy,
        allocation_pct=allocation.allocation_pct,
        allocation_jpy=allocation.allocation_jpy,
        market=market.value,
        lot_size=allocation.lot_size,
        quantity=allocation.quantity,
        portfolio_status=allocation.status,
        # monitoring_hint
        monitoring_intensity=MONITORING_INTENSITY[confidence],
    )

    # BLOCK / STALE の場合はプランを差し替え
    if deviation.status == "BLOCK_REEVALUATE":
        spec.execution_notes = ["価格ズレ ±10%超: 停止→再評価要求。数量確定しない。"]
        spec.quantity = 0
        spec.portfolio_status = "BLOCK_REEVALUATE"

    if freshness.status == "STALE_REEVALUATE":
        spec.execution_notes.append(
            f"ログ鮮度超過（{freshness.log_age_days}日 > {freshness.max_allowed_days}日）: 再評価推奨"
        )

    # --- 7. エージェント呼び出し（commentary 生成、リトライ付き） ---
    print(f">>> commentary 生成（plan-generator エージェント）")
    prompt = build_commentary_prompt(spec, judgment.raw_text, additional_file_paths)
    agent_file = AGENTS_DIR / "plan-generator.md"

    dbg = load_debug_config("plan")

    commentary_result = None
    for attempt in range(1, MAX_AGENT_RETRIES + 1):
        if attempt > 1:
            print(f"  commentary 生成 リトライ {attempt}/{MAX_AGENT_RETRIES}")
        try:
            result = await call_agent(prompt, file_path=str(agent_file), show_cost=True, **dbg)
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

    # --- 10. DB 書き込み（sessions.plan JSONB に格納） ---
    _db_session = safe_db(get_latest_session, spec.ticker)
    _db_session_id = _db_session["id"] if _db_session else None
    if _db_session_id:
        plan_data = {
            "plan_id": spec.plan_id,
            "decision_final": spec.decision_final,
            "vote_for": spec.vote_for,
            "vote_against": spec.vote_against,
            "confidence": spec.confidence,
            "freshness_status": spec.freshness_status,
            "data_checks_status": spec.data_checks_status,
            "allocation_jpy": spec.allocation_jpy,
            "quantity": spec.quantity,
            "yaml_full": build_yaml(spec),
        }
        safe_db(update_session, _db_session_id, plan=plan_data)

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
    if len(sys.argv) < 4:
        print("使い方: python plan_orchestrator.py <銘柄> <セッションDir> <期間> [予算(円)] [リスク上限] [現在価格] [基準価格]")
        print()
        print("  期間（必須）: '短期' / '中期' / '長期'")
        print("  予算・リスク上限は省略時に Supabase DB から自動取得")
        print("  保有株数・平均取得単価は holdings テーブルから自動取得")
        print("  リスク上限: 円（例: 50000）または %（例: 5%）")
        print("  現在価格: 省略または '-' → Web検索で自動取得")
        print("  基準価格: 省略または '-' → 現在価格と同値（ズレ0%）")
        print()
        print("例:")
        print("  python plan_orchestrator.py NVDA \"C:\\...\\logs\\260210\" 長期")
        print("  python plan_orchestrator.py 楽天 \"C:\\...\\Discusion\\logs\" 中期 5000000 50000")
        print("  python plan_orchestrator.py NVDA \"C:\\...\\logs\\260210\" 長期 5000000 5% - 135")
        sys.exit(1)

    if sys.argv[3] not in _HORIZON_MAP:
        print(f"エラー: 期間 '{sys.argv[3]}' は無効です。'短期' / '中期' / '長期' のいずれかを指定してください。")
        sys.exit(1)

    ticker = sys.argv[1]
    session_dir = sys.argv[2]
    horizon = _HORIZON_MAP[sys.argv[3]]

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

    budget = _cli_or_db_int(4, "total_budget_jpy")
    if len(sys.argv) > 5 and sys.argv[5] != "-":
        risk_limit = sys.argv[5]
    elif _db_config.get("risk_limit_pct") is not None:
        risk_limit = f"{_db_config['risk_limit_pct']}%"
    else:
        risk_limit = "5%"
    current_price = _parse_optional_float(sys.argv, 6)
    anchor_price = _parse_optional_float(sys.argv, 7)

    if budget == 0:
        print("エラー: 予算が 0 です。CLI 引数または Supabase の portfolio_config を設定してください。")
        sys.exit(1)

    plan_config = load_plan_config(_db_config)

    print(f"[設定] 予算={budget:,}円 リスク上限={risk_limit}")
    if _db_config:
        print(f"  (DB フォールバック有効)")

    anyio.run(lambda: run_plan_orchestrator(
        ticker, session_dir, budget, risk_limit, horizon,
        current_price, anchor_price,
        config=plan_config,
    ))
