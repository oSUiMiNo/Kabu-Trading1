"""
Watch バッチ

DB から Watch 対象の銘柄を取得し、Watch/src/main.py を並列実行する。

Usage:
    python watch_batch.py                  # archive.active=True の全銘柄を処理
    python watch_batch.py --ticker NVDA    # 指定銘柄のみ
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import safe_db, fetch_active_for_watch

PROJECT_ROOT = Path(__file__).resolve().parent
WATCH_DIR = PROJECT_ROOT / "Watch"


def _find_venv_python() -> str:
    for base in [WATCH_DIR / "src", WATCH_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(python: str, script: str, ticker: str):
    cmd = [python, script, "--ticker", ticker]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(WATCH_DIR / "src"))
    await proc.communicate()
    return ticker, proc.returncode


async def run(target_ticker: str | None = None):
    print(f"\n{'='*60}")
    print(f"=== Watch Batch ===")
    print(f"{'='*60}")

    if target_ticker:
        tickers = [target_ticker.upper()]
    else:
        tickers = safe_db(fetch_active_for_watch) or []
        if not tickers:
            print("  active な対象銘柄がありません。")
            return 0

    print(f"  対象: {len(tickers)} 銘柄")
    for t in tickers:
        print(f"    - {t}")
    print()

    python = _find_venv_python()
    script = str(WATCH_DIR / "src" / "main.py")

    results = await asyncio.gather(*[_run_one(python, script, t) for t in tickers])

    ok = sum(1 for _, rc in results if rc == 0)
    ng = len(results) - ok
    print(f"  完了: {ok} 成功 / {ng} 失敗 (計 {len(results)} 銘柄)")

    for t, rc in results:
        if rc != 0:
            print(f"  [{t}] 失敗 (exit code: {rc})")

    return 1 if ng > 0 else 0


if __name__ == "__main__":
    target = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        else:
            i += 1

    sys.exit(asyncio.run(run(target)))
