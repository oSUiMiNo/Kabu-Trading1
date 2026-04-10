"""
Technical バッチ

watchlist の active 銘柄に対して Technical/src/main.py を並列実行する。

Usage:
    python technical_batch.py                 # watchlist 全銘柄
    python technical_batch.py --ticker AAPL   # 特定銘柄のみ
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import safe_db, list_watchlist

PROJECT_ROOT = Path(__file__).resolve().parent
TECHNICAL_DIR = PROJECT_ROOT / "Technical"


def _find_venv_python() -> str:
    for base in [TECHNICAL_DIR / "src", TECHNICAL_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(ticker: str, python: str, script: str, extra_args: list[str]):
    cmd = [python, script, "--ticker", ticker] + extra_args
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(TECHNICAL_DIR))
    await proc.communicate()
    return ticker, proc.returncode


async def run(
    target_ticker: str | None = None,
    market: str | None = None,
    create_archive: bool | None = None,
):
    market_label = f" [{market}]" if market else ""
    print(f"\n{'='*60}")
    print(f"=== Technical Batch{market_label} ===")
    print(f"{'='*60}")

    if target_ticker:
        tickers = [target_ticker.upper()]
        market_map = {}
        _create = create_archive if create_archive is not None else False
    else:
        watchlist = safe_db(list_watchlist, active_only=True, market=market)
        if not watchlist:
            print(f"  watchlist に active な銘柄がありません{market_label}。")
            return 0
        tickers = [w["ticker"] for w in watchlist]
        market_map = {w["ticker"]: w.get("market") for w in watchlist}
        _create = create_archive if create_archive is not None else True

    python = _find_venv_python()
    script = str(TECHNICAL_DIR / "src" / "main.py")

    print(f"  対象: {len(tickers)} 銘柄")
    for t in tickers:
        print(f"    - {t}")
    print()

    tasks = []
    for t in tickers:
        args = []
        if _create:
            args.append("--create-archive")
        m = market_map.get(t)
        if m:
            args.extend(["--market", m])
        tasks.append(_run_one(t, python, script, args))

    results = await asyncio.gather(*tasks, return_exceptions=True)

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
    print(f"  完了: {ok} 成功 / {ng} 失敗 (計 {len(results)} 銘柄)")

    return 1 if ng > 0 else 0


if __name__ == "__main__":
    target = None
    _market = None
    _create_archive = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--market" and i + 1 < len(args):
            _market = args[i + 1].upper()
            i += 2
        elif args[i] == "--create-archive":
            _create_archive = True
            i += 1
        else:
            i += 1

    sys.exit(asyncio.run(run(target, _market, _create_archive)))
