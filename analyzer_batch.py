"""
Analyzer バッチ

DB から Analyzer 対象の銘柄を取得し、Analyzer/src/main.py を並列実行する。

Usage:
    python analyzer_batch.py    # DB から全対象銘柄を自動検出
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import safe_db, fetch_active_for_analyzer, list_watchlist

PROJECT_ROOT = Path(__file__).resolve().parent
ANALYZER_DIR = PROJECT_ROOT / "Analyzer"


def _find_venv_python() -> str:
    for base in [ANALYZER_DIR / "src", ANALYZER_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(python: str, script: str, ticker: str, horizon: str, mode: str, archive_id: str, display_name: str):
    cmd = [python, script, ticker, horizon, mode, "--archive-id", archive_id]
    if display_name:
        cmd.extend(["--display-name", display_name])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(ANALYZER_DIR / "src"))
    await proc.communicate()
    return ticker, proc.returncode


async def run():
    print(f"\n{'='*60}")
    print(f"=== Analyzer Batch ===")
    print(f"{'='*60}")

    pending = safe_db(fetch_active_for_analyzer) or []
    if not pending:
        print("  active な対象銘柄がありません。")
        return 0

    wl = safe_db(list_watchlist, active_only=False) or []
    dn_map = {w["ticker"]: w.get("display_name") or "" for w in wl}

    tickers = [row["ticker"] for row in pending]
    print(f"  対象: {len(tickers)} 銘柄")
    for t in tickers:
        print(f"    - {t}")
    print()

    python = _find_venv_python()
    script = str(ANALYZER_DIR / "src" / "main.py")

    tasks = []
    for row in pending:
        ticker = row["ticker"]
        mode = row.get("mode", "buy")
        span = row.get("span", "mid")
        archive_id = row.get("id", "")
        dn = dn_map.get(ticker, "")
        tasks.append(_run_one(python, script, ticker, span, mode, archive_id, dn))

    results = await asyncio.gather(*tasks)

    ok = sum(1 for _, rc in results if rc == 0)
    ng = len(results) - ok
    print(f"  完了: {ok} 成功 / {ng} 失敗 (計 {len(results)} 銘柄)")

    for t, rc in results:
        if rc != 0:
            print(f"  [{t}] 失敗 (exit code: {rc})")

    return 1 if ng > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
