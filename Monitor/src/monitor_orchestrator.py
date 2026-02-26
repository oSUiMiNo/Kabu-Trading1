"""
Monitor オーケストレーター

watchlist テーブルの active 銘柄について、
最新プランの前提が現在の市場状況で維持されているかをチェックする。

Usage:
    python monitor_orchestrator.py                          # watchlist 全銘柄
    python monitor_orchestrator.py --ticker NVDA             # 特定銘柄のみ
    python monitor_orchestrator.py --market US               # 米国株のみ
    python monitor_orchestrator.py --market JP               # 日本株のみ
    python monitor_orchestrator.py --skip-span long          # 長期銘柄をスキップ
"""
import json
import re
import sys
from dataclasses import dataclass, field
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


MAX_MONITOR_RETRIES = 3


async def check_one_ticker(ticker: str) -> dict | None:
    """1銘柄に対する監視チェックを実行する。エージェント呼び出し〜パースを最大3回リトライ。"""
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

    monitor_data = None
    total_cost = 0.0
    last_error = ""

    for attempt in range(1, MAX_MONITOR_RETRIES + 1):
        if attempt > 1:
            print(f"  [{ticker}] リトライ {attempt}/{MAX_MONITOR_RETRIES}")

        try:
            result = await call_agent(
                prompt, file_path=str(agent_file), show_cost=True, **dbg,
            )
        except Exception as e:
            last_error = f"エージェント呼び出し例外: {e}"
            print(f"  [{ticker}] {last_error}")
            continue

        cost = result.cost if result else None
        if cost:
            total_cost += cost
            print(f"  [{ticker}] コスト: ${cost:.4f}")

        if not result or not result.text:
            last_error = "エージェント応答なし"
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        parsed = parse_monitor_result(result.text)
        if not parsed:
            last_error = "結果パース失敗"
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        if parsed.get("result") == "ERROR":
            last_error = parsed.get("summary", "result=ERROR")
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        monitor_data = parsed
        break

    if monitor_data is None:
        print(f"  [{ticker}] リトライ上限到達 — ERROR として記録")
        plan_price = _extract_plan_price(plan)
        error_record = {
            "checked_at": now.isoformat(),
            "plan_id": plan_id,
            "result": "ERROR",
            "current_price": None,
            "plan_price": plan_price,
            "price_change_pct": None,
            "summary": f"リトライ {MAX_MONITOR_RETRIES} 回失敗: {last_error}",
            "risk_flags": [],
            "cost_usd": total_cost or None,
            "retries_exhausted": True,
            "error_detail": last_error,
        }
        session_id = session["id"]
        safe_db(update_session, session_id, monitor=error_record)
        return error_record

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
        "cost_usd": total_cost or None,
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


@dataclass
class MonitorSummary:
    """run_monitor() の戻り値。NG銘柄のセッション情報を含む。"""
    results: dict = field(default_factory=dict)
    ng_tickers: list[dict] = field(default_factory=list)
    total_cost: float = 0.0


async def run_monitor(
    target_ticker: str | None = None,
    market: str | None = None,
    skip_spans: set[str] | None = None,
) -> MonitorSummary:
    """watchlist の active 銘柄をチェックする。

    Args:
        market: 'US'/'JP' で市場フィルタ。
        skip_spans: スキップする投資期間の集合（例: {'long'}）。
    """
    now = datetime.now(JST)
    summary = MonitorSummary()

    market_label = f" [{market}]" if market else ""
    skip_label = f" (skip: {','.join(skip_spans)})" if skip_spans else ""
    print(f"{'='*60}")
    print(f"=== Monitor オーケストレーター{market_label}{skip_label} ===")
    print(f"=== {now.strftime('%Y-%m-%d %H:%M %Z')} ===")
    print(f"{'='*60}")

    if target_ticker:
        tickers = [target_ticker.upper()]
        print(f"  対象: {tickers[0]}（指定銘柄）")
    else:
        watchlist = safe_db(list_watchlist, active_only=True, market=market)
        if not watchlist:
            print(f"  watchlist に active な銘柄がありません{market_label}。終了します。")
            return summary
        tickers = [w["ticker"] for w in watchlist]
        print(f"  対象: {len(tickers)} 銘柄{market_label}")
        for t in tickers:
            print(f"    - {t}")

    print()

    for ticker in tickers:
        session = safe_db(get_latest_session_with_plan, ticker)

        if skip_spans and session:
            span = session.get("span", "mid")
            if span in skip_spans:
                print(f"  [{ticker}] スキップ: {span} は対象外")
                print()
                continue

        result = await check_one_ticker(ticker)
        if result:
            summary.results[ticker] = result
            summary.total_cost += result.get("cost_usd", 0) or 0
            if result.get("result") == "NG" and session:
                summary.ng_tickers.append({
                    "ticker": ticker,
                    "mode": session.get("mode", "buy"),
                    "span": session.get("span", "mid"),
                })
        print()

    ok_count = sum(1 for r in summary.results.values() if r.get("result") == "OK")
    ng_count = len(summary.ng_tickers)
    err_count = sum(1 for r in summary.results.values() if r.get("result") == "ERROR")
    skip_count = len(tickers) - len(summary.results)

    print(f"{'='*60}")
    print(f"=== 完了 ===")
    print(f"  OK: {ok_count}, NG: {ng_count}, ERROR: {err_count}, SKIP: {skip_count}")
    print(f"  合計コスト: ${summary.total_cost:.4f}")
    print(f"{'='*60}")

    return summary


if __name__ == "__main__":
    target = None
    _market = None
    _skip_spans: set[str] = set()
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--market" and i + 1 < len(args):
            _market = args[i + 1].upper()
            i += 2
        elif args[i] == "--skip-span" and i + 1 < len(args):
            _skip_spans.add(args[i + 1].lower())
            i += 2
        else:
            target = args[i]
            i += 1

    anyio.run(lambda: run_monitor(target, _market, _skip_spans or None))
