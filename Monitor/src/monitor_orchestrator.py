"""
Monitor オーケストレーター

watchlist テーブルの active 銘柄について、
最新プランの前提が現在の市場状況で維持されているかをチェックする。

Usage:
    python monitor_orchestrator.py              # watchlist 全銘柄
    python monitor_orchestrator.py --ticker NVDA # 特定銘柄のみ
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    list_watchlist,
    get_latest_session_with_plan,
    update_session,
)

from AgentUtil import call_agent, load_debug_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"

JST = timezone(timedelta(hours=9))


def build_check_prompt(ticker: str, session: dict) -> str:
    """monitor-checker エージェントに渡すプロンプトを組み立てる。"""
    plan = session.get("plan", {})
    if isinstance(plan, str):
        plan = json.loads(plan)

    plan_id = plan.get("plan_id", "N/A")
    decision_final = plan.get("decision_final", "N/A")
    confidence = plan.get("confidence", "N/A")

    plan_price = "不明"
    horizon = "不明"
    basis_text = "  （なし）"
    monitoring_hint = {}

    plan_yaml = plan.get("yaml_full", "")
    if plan_yaml:
        try:
            parsed = yaml.safe_load(plan_yaml)
            if parsed:
                data_checks = parsed.get("data_checks", {})
                plan_price = data_checks.get("current_price", "不明")

                decision = parsed.get("decision", {})
                horizon = decision.get("horizon", "不明")

                basis_list = decision.get("decision_basis", [])
                if basis_list:
                    basis_text = "\n".join(
                        f"  - [{b.get('fact_id', '?')}] {b.get('why_it_matters', '')}"
                        for b in basis_list
                    )

                monitoring_hint = parsed.get("monitoring_hint", {})
        except yaml.YAMLError:
            pass

    return (
        f"以下の投資プランが現在も有効かチェックしてください。\n"
        f"\n"
        f"【銘柄】{ticker}\n"
        f"【プランID】{plan_id}\n"
        f"【判定】{decision_final}\n"
        f"【confidence】{confidence}\n"
        f"【投資期間】{horizon}\n"
        f"【プラン時点の株価】{plan_price}\n"
        f"\n"
        f"【判定根拠】\n{basis_text}\n"
        f"\n"
        f"【モニタリングヒント】\n"
        f"  強度: {monitoring_hint.get('intensity', 'N/A')}\n"
        f"  理由: {monitoring_hint.get('reason', 'N/A')}\n"
        f"\n"
        f"上記プランの前提が現在の市場状況でまだ有効か、"
        f"WebSearch で最新の株価・ニュースを調査して判定してください。\n"
    )


def parse_monitor_result(agent_output: str) -> dict | None:
    """monitor-checker エージェントの出力から monitor_result を抽出する。"""
    blocks = re.findall(r"```yaml\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
        except yaml.YAMLError:
            continue

        if not isinstance(data, dict):
            continue

        monitor_result = data.get("monitor_result", data)
        if isinstance(monitor_result, dict) and "result" in monitor_result:
            return monitor_result

    m = re.search(r"result:\s*(OK|NG|ERROR)", agent_output)
    if m:
        return {"result": m.group(1), "summary": "パース不完全（フォールバック）"}

    return None


def _extract_plan_price(plan: dict) -> float | None:
    """plan JSONB から yaml_full をパースしてプラン時の current_price を返す。"""
    plan_yaml = plan.get("yaml_full", "")
    if not plan_yaml:
        return None
    try:
        parsed = yaml.safe_load(plan_yaml)
        return parsed.get("data_checks", {}).get("current_price")
    except yaml.YAMLError:
        return None


async def check_one_ticker(ticker: str) -> dict | None:
    """1銘柄に対する監視チェックを実行する。"""
    now = datetime.now(JST)

    session = safe_db(get_latest_session_with_plan, ticker)
    if not session:
        print(f"  [{ticker}] スキップ: プラン付きセッションが見つかりません")
        return None

    plan = session.get("plan")
    if not plan:
        print(f"  [{ticker}] スキップ: plan が null")
        return None

    if isinstance(plan, str):
        plan = json.loads(plan)

    plan_id = plan.get("plan_id", "N/A")
    print(f"  [{ticker}] チェック開始 (plan: {plan_id})")

    prompt = build_check_prompt(ticker, session)

    agent_file = AGENTS_DIR / "monitor-checker.md"
    dbg = load_debug_config("monitor")
    result = await call_agent(
        prompt, file_path=str(agent_file), show_cost=True, **dbg,
    )

    cost = result.cost if result else None
    if cost:
        print(f"  [{ticker}] コスト: ${cost:.4f}")

    if not result or not result.text:
        print(f"  [{ticker}] 警告: エージェント応答なし")
        return None

    monitor_data = parse_monitor_result(result.text)
    if not monitor_data:
        print(f"  [{ticker}] 警告: 結果パース失敗")
        return None

    plan_price = _extract_plan_price(plan)
    current_price = monitor_data.get("current_price")

    price_change_pct = None
    if plan_price and current_price and plan_price > 0:
        price_change_pct = round((current_price - plan_price) / plan_price * 100, 2)

    monitor_record = {
        "checked_at": now.isoformat(),
        "plan_id": plan_id,
        "result": monitor_data.get("result", "ERROR"),
        "current_price": current_price,
        "plan_price": plan_price,
        "price_change_pct": price_change_pct,
        "summary": monitor_data.get("summary", ""),
        "risk_flags": monitor_data.get("risk_flags", []),
        "cost_usd": cost,
    }

    if monitor_data.get("result") == "NG":
        monitor_record["ng_reason"] = monitor_data.get("ng_reason", "")

    session_id = session["id"]
    safe_db(update_session, session_id, monitor=monitor_record)

    status = monitor_record["result"]
    print(f"  [{ticker}] 結果: {status}")
    if status == "NG":
        print(f"  [{ticker}] 理由: {monitor_record.get('ng_reason', '')}")

    return monitor_record


async def run_monitor(target_ticker: str | None = None):
    """watchlist の全 active 銘柄をチェックする。"""
    now = datetime.now(JST)

    print(f"{'='*60}")
    print(f"=== Monitor オーケストレーター ===")
    print(f"=== {now.strftime('%Y-%m-%d %H:%M %Z')} ===")
    print(f"{'='*60}")

    if target_ticker:
        tickers = [target_ticker.upper()]
        print(f"  対象: {tickers[0]}（指定銘柄）")
    else:
        watchlist = safe_db(list_watchlist, active_only=True)
        if not watchlist:
            print("  watchlist に active な銘柄がありません。終了します。")
            return
        tickers = [w["ticker"] for w in watchlist]
        print(f"  対象: {len(tickers)} 銘柄")
        for t in tickers:
            print(f"    - {t}")

    print()

    results = {}
    total_cost = 0.0

    for ticker in tickers:
        result = await check_one_ticker(ticker)
        if result:
            results[ticker] = result
            total_cost += result.get("cost_usd", 0) or 0
        print()

    print(f"{'='*60}")
    print(f"=== 完了 ===")
    ok_count = sum(1 for r in results.values() if r.get("result") == "OK")
    ng_count = sum(1 for r in results.values() if r.get("result") == "NG")
    err_count = sum(1 for r in results.values() if r.get("result") == "ERROR")
    skip_count = len(tickers) - len(results)

    print(f"  OK: {ok_count}, NG: {ng_count}, ERROR: {err_count}, SKIP: {skip_count}")
    print(f"  合計コスト: ${total_cost:.4f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    target = None
    if len(sys.argv) > 1:
        if sys.argv[1] == "--ticker" and len(sys.argv) > 2:
            target = sys.argv[2]
        else:
            target = sys.argv[1]

    anyio.run(lambda: run_monitor(target))
