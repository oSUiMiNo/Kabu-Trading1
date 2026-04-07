"""
Monitor バッチ

watchlist の active 銘柄に対して Monitor/src/main.py を並列実行する。

Usage:
    python monitor_batch.py                          # watchlist 全銘柄
    python monitor_batch.py --ticker NVDA             # 特定銘柄のみ
    python monitor_batch.py --tickers NVDA,TSLA,AMZN  # 複数銘柄指定（IndicatorFilter 連携用）
    python monitor_batch.py --market US               # 米国株のみ
    python monitor_batch.py --skip-span long          # 長期銘柄をスキップ
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))
from supabase_client import (
    safe_db,
    list_watchlist,
    get_archivelog_by_id,
    get_latest_archivelog_with_newplan,
)

PROJECT_ROOT = Path(__file__).resolve().parent
MONITOR_DIR = PROJECT_ROOT / "Monitor"


def _find_venv_python() -> str:
    for base in [MONITOR_DIR / "src", MONITOR_DIR]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


async def _run_one(ticker: str, python: str, script: str):
    cmd = [python, script, "--ticker", ticker]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(MONITOR_DIR / "src"))
    await proc.communicate()
    return ticker, proc.returncode


async def run(
    target_ticker: str | None = None,
    market: str | None = None,
    skip_spans: set[str] | None = None,
    tickers_override: list[str] | None = None,
):
    market_label = f" [{market}]" if market else ""
    skip_label = f" (skip: {','.join(skip_spans)})" if skip_spans else ""
    print(f"\n{'='*60}")
    print(f"=== Monitor Batch{market_label}{skip_label} ===")
    print(f"{'='*60}")

    if tickers_override:
        tickers = [t.upper() for t in tickers_override]
    elif target_ticker:
        tickers = [target_ticker.upper()]
    else:
        watchlist = safe_db(list_watchlist, active_only=True, market=market)
        if not watchlist:
            print(f"  watchlist に active な銘柄がありません{market_label}。")
            return 0
        tickers = [w["ticker"] for w in watchlist]

    # skip_spans フィルタリング（tickers_override 時はフィルター側で処理済みのためスキップ）
    filtered = []
    if skip_spans and not target_ticker and not tickers_override:
        watchlist = safe_db(list_watchlist, active_only=True, market=market) or []
        watchlist_map = {w["ticker"]: w for w in watchlist}
        for ticker in tickers:
            wl_entry = watchlist_map.get(ticker)
            aid = wl_entry.get("latest_archive_id") if wl_entry else None
            if aid:
                archivelog = safe_db(get_archivelog_by_id, aid)
            else:
                archivelog = safe_db(get_latest_archivelog_with_newplan, ticker)
            if archivelog:
                span = archivelog.get("span", "mid")
                if span in skip_spans:
                    print(f"  [{ticker}] スキップ: {span} は対象外")
                    continue
            filtered.append(ticker)
    else:
        filtered = list(tickers)

    if not filtered:
        print("  対象銘柄がありません。")
        return 0

    python = _find_venv_python()
    script = str(MONITOR_DIR / "src" / "main.py")

    print(f"  対象: {len(filtered)} 銘柄")
    for t in filtered:
        print(f"    - {t}")
    print()

    results = await asyncio.gather(
        *[_run_one(t, python, script) for t in filtered],
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
    print(f"  完了: {ok} 成功 / {ng} 失敗 (計 {len(results)} 銘柄)")

    return 1 if ng > 0 else 0


if __name__ == "__main__":
    target = None
    _market = None
    _skip_spans: set[str] = set()
    _tickers_csv: str | None = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--tickers" and i + 1 < len(args):
            _tickers_csv = args[i + 1]
            i += 2
        elif args[i] == "--market" and i + 1 < len(args):
            _market = args[i + 1].upper()
            i += 2
        elif args[i] == "--skip-span" and i + 1 < len(args):
            _skip_spans.add(args[i + 1].lower())
            i += 2
        else:
            i += 1

    _tickers_list = [t.strip() for t in _tickers_csv.split(",") if t.strip()] if _tickers_csv else None

    sys.exit(asyncio.run(run(target, _market, _skip_spans or None, _tickers_list)))
