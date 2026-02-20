"""
NG ディスパッチャー（Monitor → Discussion → Planning パイプライン）

Monitor を実行し、NG 判定が出た銘柄に対して
Discussion（再議論）→ Planning（プラン再生成）を自動起動する。

Usage:
    python ng_dispatch.py                       # watchlist 全銘柄を監視 → NG銘柄を再議論+再プラン
    python ng_dispatch.py --market US           # 米国株のみ
    python ng_dispatch.py --market JP           # 日本株のみ
    python ng_dispatch.py --skip-span long      # 長期銘柄をスキップ
    python ng_dispatch.py --ticker NVDA         # 特定銘柄のみ
    python ng_dispatch.py --monitor-only        # 監視のみ（Discussion/Planning は起動しない）
"""
import subprocess
import sys
from pathlib import Path

import anyio

from monitor_orchestrator import run_monitor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_SCRIPT = PROJECT_ROOT / "discuss_and_plan.py"

SPAN_TO_CLI = {"short": "short", "mid": "mid", "long": "long"}
MODE_TO_CLI = {"buy": "buy", "sell": "sell"}


def run_discuss_and_plan(ticker: str, span: str, mode: str) -> int:
    """
    discuss_and_plan.py（Discussion → Planning パイプライン）を subprocess で起動する。

    Returns:
        プロセスの exit code（0=正常）
    """
    cmd = [
        sys.executable,
        str(PIPELINE_SCRIPT),
        ticker,
        SPAN_TO_CLI.get(span, "mid"),
        MODE_TO_CLI.get(mode, "buy"),
    ]

    print(f"  [{ticker}] Discussion → Planning 起動: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


async def run_pipeline(
    target_ticker: str | None = None,
    monitor_only: bool = False,
    market: str | None = None,
    skip_spans: set[str] | None = None,
):
    """Monitor → Discussion → Planning パイプライン。"""
    summary = await run_monitor(target_ticker, market=market, skip_spans=skip_spans)

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
        return

    print()
    for ng in summary.ng_tickers:
        ticker = ng["ticker"]
        span = ng["span"]
        mode = ng["mode"]

        print(f"{'='*60}")
        print(f"=== [{ticker}] Discussion → Planning 開始 ===")
        print(f"{'='*60}")

        exit_code = run_discuss_and_plan(ticker, span, mode)
        if exit_code == 0:
            print(f"  [{ticker}] Discussion → Planning 完了")
        else:
            print(f"  [{ticker}] Discussion → Planning エラー (exit code: {exit_code})")
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
