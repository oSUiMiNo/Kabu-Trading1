"""
Monitor オーケストレーター

指定された1銘柄について、最新プランの前提が現在の市場状況で維持されているかをチェックする。
複数銘柄の並列実行は monitor_batch.py（PJTルート）が担う。

Usage:
    python main.py --ticker NVDA
"""
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import asyncio

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_latest_archivelog_with_newplan,
    create_archivelog,
    update_archivelog,
    get_client,
)

from AgentUtil import call_agent, load_debug_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"

JST = timezone(timedelta(hours=9))


def build_check_prompt(ticker: str, archivelog: dict) -> str:
    """monitor-checker エージェントに渡すプロンプトを組み立てる。"""
    newplan_full = archivelog.get("newplan_full", "")
    verdict = archivelog.get("verdict", "N/A")

    plan_price = "不明"
    horizon = "不明"
    confidence = "N/A"
    basis_text = "  （なし）"
    monitoring_hint = {}

    if newplan_full:
        try:
            parsed = yaml.safe_load(newplan_full)
            if parsed:
                data_checks = parsed.get("data_checks", {})
                plan_price = data_checks.get("current_price", "不明")

                decision = parsed.get("decision", {})
                horizon = decision.get("horizon", "不明")
                confidence = decision.get("confidence", "N/A")

                basis_list = decision.get("decision_basis", [])
                if basis_list:
                    basis_text = "\n".join(
                        f"  - [{b.get('lane', '?')}] {b.get('why_it_matters', '')}"
                        for b in basis_list
                    )

                monitoring_hint = parsed.get("monitoring_hint", {})
        except yaml.YAMLError:
            pass

    return (
        f"以下の投資プランが現在も有効かチェックしてください。\n"
        f"\n"
        f"【銘柄】{ticker}\n"
        f"【判定】{verdict}\n"
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


def _extract_plan_price(newplan_full: str) -> float | None:
    """newplan_full（YAML テキスト）からプラン時の current_price を返す。"""
    if not newplan_full:
        return None
    try:
        parsed = yaml.safe_load(newplan_full)
        return parsed.get("data_checks", {}).get("current_price")
    except yaml.YAMLError:
        return None


MAX_MONITOR_RETRIES = 3


async def check_one_ticker(ticker: str, archivelog: dict | None = None, target_archive_id: str | None = None) -> dict | None:
    """1銘柄に対する監視チェックを実行する。エージェント呼び出し〜パースを最大3回リトライ。
    target_archive_id: Technical が作成した archive ID。指定時はそこに書き込む。"""
    now = datetime.now(JST)

    if not archivelog:
        archivelog = safe_db(get_latest_archivelog_with_newplan, ticker)
    if not archivelog:
        print(f"  [{ticker}] スキップ: プラン付きセッションが見つかりません")
        return None

    newplan_full = archivelog.get("newplan_full")
    if not newplan_full:
        print(f"  [{ticker}] スキップ: newplan_full が null")
        return None

    print(f"  [{ticker}] チェック開始")

    prompt = build_check_prompt(ticker, archivelog)
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
                prompt, file_path=str(agent_file), **{**dbg, "show_cost": True},
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
        plan_price = _extract_plan_price(newplan_full)
        error_record = {
            "checked_at": now.isoformat(),
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
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=0, active=False,
                    monitor=error_record, status="error")
        else:
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=0, active=False,
                        monitor=error_record, status="error")
        return error_record

    plan_price = _extract_plan_price(newplan_full)

    tech_price = None
    if target_archive_id:
        try:
            _tech_rec = get_client().from_("archive").select("technical").eq("id", target_archive_id).limit(1).execute()
            if _tech_rec.data:
                _tech = _tech_rec.data[0].get("technical") or {}
                tech_price = _tech.get("latest_price") if isinstance(_tech, dict) else None
        except Exception:
            pass
    current_price = tech_price if tech_price is not None else monitor_data.get("current_price")

    price_change_pct = None
    if plan_price and current_price and plan_price > 0:
        price_change_pct = round((current_price - plan_price) / plan_price * 100, 2)

    monitor_record = {
        "checked_at": now.isoformat(),
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
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=1,
                    motivation_full=monitor_data.get("ng_reason", ""),
                    active=True, monitor=monitor_record)
        else:
            safe_db(update_archivelog, archivelog["id"], active=False)
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=1,
                        motivation_full=monitor_data.get("ng_reason", ""),
                        active=True, monitor=monitor_record)
    else:
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=0, active=False,
                    monitor=monitor_record, status="completed")
        else:
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=0, active=False,
                        monitor=monitor_record, status="completed")

    status = monitor_record["result"]
    print(f"  [{ticker}] 結果: {status}")
    if status == "NG":
        print(f"  [{ticker}] 理由: {monitor_record.get('ng_reason', '')}")

    return monitor_record


async def run_single(ticker: str):
    """1銘柄の Monitor チェックを実行する。"""
    ticker = ticker.upper()

    target_archive_id = None
    try:
        resp = (
            get_client()
            .from_("archive")
            .select("id")
            .eq("ticker", ticker)
            .not_.is_("technical", "null")
            .is_("monitor", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            target_archive_id = resp.data[0]["id"]
    except Exception:
        pass

    result = await check_one_ticker(ticker, archivelog=None, target_archive_id=target_archive_id)

    if result and result.get("result") != "ERROR":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    _ticker = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            _ticker = args[i + 1]
            i += 2
        else:
            _ticker = args[i]
            i += 1

    if not _ticker:
        print("エラー: --ticker は必須です。バッチ実行は monitor_batch.py を使用してください。")
        sys.exit(1)

    asyncio.run(run_single(_ticker))
