"""
NG ディスパッチャー（Monitor → Discussion パイプライン）

Monitor を実行し、NG 判定が出た銘柄に対して
Discussion（parallel_orchestrator）を自動起動する。

Usage:
    python ng_dispatch.py              # watchlist 全銘柄を監視 → NG銘柄を再議論
    python ng_dispatch.py --ticker NVDA # 特定銘柄のみ
    python ng_dispatch.py --monitor-only # 監視のみ（Discussion は起動しない）
"""
import subprocess
import sys
from pathlib import Path

import anyio

from monitor_orchestrator import run_monitor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DISCUSSION_DIR = PROJECT_ROOT / "Discusion" / "src"
DISCUSSION_VENV_PYTHON = DISCUSSION_DIR / ".venv" / "Scripts" / "python.exe"
DISCUSSION_VENV_PYTHON_UNIX = DISCUSSION_DIR / ".venv" / "bin" / "python"

SPAN_TO_CLI = {"short": "short", "mid": "mid", "long": "long"}
MODE_TO_CLI = {"buy": "buy", "sell": "sell"}


def _get_python() -> str:
    """Discussion の venv の Python パスを返す。"""
    if DISCUSSION_VENV_PYTHON.exists():
        return str(DISCUSSION_VENV_PYTHON)
    if DISCUSSION_VENV_PYTHON_UNIX.exists():
        return str(DISCUSSION_VENV_PYTHON_UNIX)
    return "python"


def run_discussion(ticker: str, span: str, mode: str) -> int:
    """
    Discussion の parallel_orchestrator を subprocess で起動する。

    Returns:
        プロセスの exit code（0=正常）
    """
    python = _get_python()
    script = str(DISCUSSION_DIR / "parallel_orchestrator.py")
    cmd = [python, script, ticker, SPAN_TO_CLI.get(span, "mid"), MODE_TO_CLI.get(mode, "buy")]

    print(f"  [{ticker}] Discussion 起動: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(DISCUSSION_DIR))
    return result.returncode


async def run_pipeline(target_ticker: str | None = None, monitor_only: bool = False):
    """Monitor → Discussion パイプライン。"""
    summary = await run_monitor(target_ticker)

    if not summary.ng_tickers:
        print()
        print("NG 銘柄なし。Discussion は起動しません。")
        return

    print()
    print(f"{'='*60}")
    print(f"=== NG 検出: {len(summary.ng_tickers)} 銘柄 → Discussion 起動 ===")
    print(f"{'='*60}")
    for ng in summary.ng_tickers:
        print(f"  - {ng['ticker']} (mode={ng['mode']}, span={ng['span']})")

    if monitor_only:
        print()
        print("--monitor-only: Discussion は起動しません。")
        return

    print()
    for ng in summary.ng_tickers:
        ticker = ng["ticker"]
        span = ng["span"]
        mode = ng["mode"]

        print(f"{'='*60}")
        print(f"=== [{ticker}] Discussion 再議論開始 ===")
        print(f"{'='*60}")

        exit_code = run_discussion(ticker, span, mode)
        if exit_code == 0:
            print(f"  [{ticker}] Discussion 完了")
        else:
            print(f"  [{ticker}] Discussion エラー (exit code: {exit_code})")
        print()

    print(f"{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"{'='*60}")


if __name__ == "__main__":
    target = None
    monitor_only = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--monitor-only":
            monitor_only = True
            i += 1
        else:
            target = args[i]
            i += 1

    anyio.run(lambda: run_pipeline(target, monitor_only))
