"""
ActionLog バッチ（Phase 6）

DB から action_log 未投入の archive を検出し、
ActionLog/src/pipeline_main.py を並列実行する。

Usage:
    python actionlog_batch.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import safe_db, fetch_pending_for_actionlog

PROJECT_ROOT = Path(__file__).resolve().parent
ACTIONLOG_DIR = PROJECT_ROOT / "ActionLog"


def _find_venv_python() -> str:
    for base in [ACTIONLOG_DIR / "src", ACTIONLOG_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(python: str, script: str, ticker: str, archive_id: str):
    cmd = [python, script, "--ticker", ticker, "--archive-id", archive_id]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(ACTIONLOG_DIR / "src"))
    await proc.communicate()
    return ticker, proc.returncode


async def run():
    print(f"\n{'='*60}")
    print(f"=== ActionLog Batch (Phase 6) ===")
    print(f"{'='*60}")

    pending = safe_db(fetch_pending_for_actionlog) or []
    if not pending:
        print("  ActionLog 対象の archive がありません。")
        return 0

    print(f"  対象: {len(pending)} 件")
    for row in pending:
        print(f"    - {row['ticker']} (archive={row['id']})")
    print()

    python = _find_venv_python()
    script = str(ACTIONLOG_DIR / "src" / "pipeline_main.py")

    results = await asyncio.gather(
        *[_run_one(python, script, row["ticker"], row["id"]) for row in pending],
        return_exceptions=True,
    )

    ok = 0
    ng = 0
    for r in results:
        if isinstance(r, Exception):
            ng += 1
            print(f"  [例外] {r}")
        elif r[1] == 0:
            ok += 1
        else:
            ng += 1
            print(f"  [{r[0]}] 失敗 (exit code: {r[1]})")
    print(f"  完了: {ok} 成功 / {ng} 失敗 (計 {len(results)} 件)")

    return 1 if ng > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
