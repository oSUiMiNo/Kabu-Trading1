"""
Planning バッチ

DB から Planning 対象の銘柄を取得し、Planning/src/main.py を並列実行する。

Usage:
    python planning_batch.py    # DB から全対象銘柄を自動検出
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import safe_db, fetch_active_for_planning, get_portfolio_config

PROJECT_ROOT = Path(__file__).resolve().parent
PLANNING_DIR = PROJECT_ROOT / "Planning"


def _find_venv_python() -> str:
    for base in [PLANNING_DIR / "src", PLANNING_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(python: str, script: str, ticker: str, horizon: str, budget: int, risk_limit: str):
    cmd = [python, script, ticker, horizon, str(budget), risk_limit]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(PLANNING_DIR / "src"))
    await proc.communicate()
    return ticker, proc.returncode


async def run():
    print(f"\n{'='*60}")
    print(f"=== Planning Batch ===")
    print(f"{'='*60}")

    pending = safe_db(fetch_active_for_planning) or []
    if not pending:
        print("  active な対象銘柄がありません。")
        return 0

    _db_config = safe_db(get_portfolio_config) or {}
    budget = int(_db_config.get("total_budget_jpy") or 0)
    if budget == 0:
        print("エラー: 予算が 0 です。portfolio_config を設定してください。")
        return 1

    risk_pct = _db_config.get("risk_limit_pct")
    risk_limit = f"{risk_pct}%" if risk_pct is not None else "5%"

    tickers = [row["ticker"] for row in pending]
    print(f"  対象: {len(tickers)} 銘柄")
    print(f"  予算={budget:,}円 リスク上限={risk_limit}")
    for t in tickers:
        print(f"    - {t}")
    print()

    python = _find_venv_python()
    script = str(PLANNING_DIR / "src" / "main.py")

    tasks = []
    for row in pending:
        ticker = row["ticker"]
        span = row.get("span", "mid")
        tasks.append(_run_one(python, script, ticker, span, budget, risk_limit))

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
