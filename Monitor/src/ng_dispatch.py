"""
NG ディスパッチャー（Monitor → Discussion → Planning パイプライン）

Monitor を実行し、NG 判定が出た銘柄に対して
Discussion（再議論）→ Planning（プラン再生成）を自動起動する。
パイプライン完了後に Discord 通知を送信する。

Usage:
    python ng_dispatch.py                       # watchlist 全銘柄を監視 → NG銘柄を再議論+再プラン
    python ng_dispatch.py --market US           # 米国株のみ
    python ng_dispatch.py --market JP           # 日本株のみ
    python ng_dispatch.py --skip-span long      # 長期銘柄をスキップ
    python ng_dispatch.py --ticker NVDA         # 特定銘柄のみ
    python ng_dispatch.py --monitor-only        # 監視のみ（Discussion/Planning は起動しない）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import safe_db, get_latest_session_with_plan

from monitor_orchestrator import run_monitor
from notification_types import NotifyLabel, NotifyPayload, classify_label
from discord_notifier import notify

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_SCRIPT = PROJECT_ROOT / "discuss_and_plan.py"

SPAN_TO_CLI = {"short": "short", "mid": "mid", "long": "long"}
MODE_TO_CLI = {"buy": "buy", "sell": "sell"}

MAX_DISPATCH_RETRIES = 2


def _load_event_context() -> dict | None:
    """環境変数 EVENT_CONTEXT から JSON を読み取る。"""
    raw = os.environ.get("EVENT_CONTEXT", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _fetch_new_plan(ticker: str) -> dict | None:
    """Discussion → Planning 完了後の最新プランを DB から取得する。"""
    session = safe_db(get_latest_session_with_plan, ticker)
    if not session:
        return None
    plan = session.get("plan")
    if not plan:
        return None
    if isinstance(plan, str):
        plan = json.loads(plan)
    return plan


def run_discuss_and_plan(ticker: str, span: str, mode: str) -> int:
    """
    discuss_and_plan.py（Discussion → Planning パイプライン）を subprocess で起動する。
    最大 MAX_DISPATCH_RETRIES 回リトライする。

    Returns:
        プロセスの exit code（0=正常、全リトライ失敗時は最後の exit code）
    """
    cmd = [
        sys.executable,
        str(PIPELINE_SCRIPT),
        ticker,
        SPAN_TO_CLI.get(span, "mid"),
        MODE_TO_CLI.get(mode, "buy"),
    ]

    for attempt in range(1, MAX_DISPATCH_RETRIES + 1):
        if attempt > 1:
            print(f"  [{ticker}] Discussion → Planning リトライ {attempt}/{MAX_DISPATCH_RETRIES}")

        print(f"  [{ticker}] Discussion → Planning 起動: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

        if result.returncode == 0:
            return 0

        print(f"  [{ticker}] Discussion → Planning 失敗 (exit code: {result.returncode})")

    return result.returncode


async def run_pipeline(
    target_ticker: str | None = None,
    monitor_only: bool = False,
    market: str | None = None,
    skip_spans: set[str] | None = None,
):
    """Monitor → Discussion → Planning パイプライン（通知統合済み）。"""
    event_context = _load_event_context()
    summary = await run_monitor(target_ticker, market=market, skip_spans=skip_spans)

    for ticker, monitor_data in summary.results.items():
        if monitor_data.get("retries_exhausted"):
            payload = NotifyPayload(
                label=NotifyLabel.ERROR,
                ticker=ticker,
                monitor_data=monitor_data,
                event_context=event_context,
                error_detail=monitor_data.get("error_detail", "Monitor リトライ上限到達"),
            )
            await notify(payload)
            continue

        label = classify_label(monitor_data)
        if label == NotifyLabel.CHECK:
            payload = NotifyPayload(
                label=label,
                ticker=ticker,
                monitor_data=monitor_data,
                event_context=event_context,
            )
            await notify(payload)

    if not summary.ng_tickers:
        print()
        print("NG 銘柄なし。Discussion/Planning は起動しません。")
        return

    print()
    print(f"{'='*60}")
    print(f"=== NG 検出: {len(summary.ng_tickers)} 銘柄 → Discussion → Planning 起動 ===")
    print(f"{'='*60}")
    for ng in summary.ng_tickers:
        print(f"  - {ng['ticker']} (mode={ng['mode']}, span={ng['span']})")

    if monitor_only:
        print()
        print("--monitor-only: Discussion/Planning は起動しません。")
        for ng in summary.ng_tickers:
            ticker = ng["ticker"]
            monitor_data = summary.results.get(ticker, {})
            label = classify_label(monitor_data)
            if label:
                payload = NotifyPayload(
                    label=label,
                    ticker=ticker,
                    monitor_data=monitor_data,
                    event_context=event_context,
                )
                await notify(payload)
        return

    print()
    for ng in summary.ng_tickers:
        ticker = ng["ticker"]
        span = ng["span"]
        mode = ng["mode"]
        monitor_data = summary.results.get(ticker, {})

        print(f"{'='*60}")
        print(f"=== [{ticker}] Discussion → Planning 開始 ===")
        print(f"{'='*60}")

        exit_code = run_discuss_and_plan(ticker, span, mode)

        if exit_code == 0:
            print(f"  [{ticker}] Discussion → Planning 完了")
            new_plan = _fetch_new_plan(ticker)
            label = classify_label(monitor_data)
            if label:
                payload = NotifyPayload(
                    label=label,
                    ticker=ticker,
                    monitor_data=monitor_data,
                    new_plan=new_plan,
                    event_context=event_context,
                )
                await notify(payload)
        else:
            print(f"  [{ticker}] Discussion → Planning 全リトライ失敗 (exit code: {exit_code})")
            payload = NotifyPayload(
                label=NotifyLabel.ERROR,
                ticker=ticker,
                monitor_data=monitor_data,
                event_context=event_context,
                error_detail=f"Discussion → Planning 失敗 (exit code: {exit_code})",
            )
            await notify(payload)
        print()

    print(f"{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"{'='*60}")


if __name__ == "__main__":
    target = None
    monitor_only = False
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
        elif args[i] == "--monitor-only":
            monitor_only = True
            i += 1
        else:
            target = args[i]
            i += 1

    anyio.run(lambda: run_pipeline(target, monitor_only, _market, _skip_spans or None))
