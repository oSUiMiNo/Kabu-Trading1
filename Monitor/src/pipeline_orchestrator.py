"""
pipeline_orchestrator.py — 4フェーズパイプライン統合オーケストレーター

Phase 1: Monitor  — 全銘柄並列チェック
Phase 2: Discussion — watchlist.MotivationID!=0 を並列議論
Phase 3: Planning   — 同銘柄を並列プラン生成
Phase 4: Watch      — archive サマリー書込 + Discord 通知 + MotivationID リセット

ng_dispatch.py と discuss_and_plan.py を置き換える。
"""
import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(Path(__file__).parent))

from discord_notifier import send_start_notification


def _venv_python(src_dir: Path) -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = src_dir.parent / rel
        if p.exists():
            return str(p)
    return "uv"


def _run_discussion(ticker: str, span: str, mode: str) -> tuple[str, bool]:
    python = _venv_python(ROOT / "Discusion" / "src")
    cmd = [python, str(ROOT / "Discusion" / "src" / "parallel_orchestrator.py"),
           ticker, span, mode]
    rc = subprocess.run(cmd, cwd=str(ROOT / "Discusion" / "src")).returncode
    return ticker, rc == 0


def run_phase2(ng_tickers: list[dict]) -> dict[str, bool]:
    results = {}
    with ThreadPoolExecutor() as ex:
        for ticker, ok in ex.map(
            lambda ng: _run_discussion(ng["ticker"], ng["span"], ng["mode"]),
            ng_tickers
        ):
            results[ticker] = ok
    return results


def _run_planning(ticker: str, span: str) -> tuple[str, bool]:
    span_ja = {"short": "短期", "mid": "中期", "long": "長期"}.get(span, "中期")
    python = _venv_python(ROOT / "Planning" / "src")
    cmd = [python, str(ROOT / "Planning" / "src" / "plan_orchestrator.py"),
           ticker, span_ja]
    rc = subprocess.run(cmd, cwd=str(ROOT / "Planning" / "src")).returncode
    return ticker, rc == 0


def run_phase3(ng_tickers: list[dict], disc_ok: dict[str, bool]):
    targets = [ng for ng in ng_tickers if disc_ok.get(ng["ticker"])]
    with ThreadPoolExecutor() as ex:
        list(ex.map(lambda ng: _run_planning(ng["ticker"], ng["span"]), targets))


def run_phase4(market: str | None = None):
    python = _venv_python(ROOT / "Watch" / "src")
    cmd = [python, str(ROOT / "Watch" / "src" / "watch_orchestrator.py")]
    if market:
        cmd += ["--market", market]
    subprocess.run(cmd, cwd=str(ROOT / "Watch" / "src"))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--market", choices=["US", "JP"])
    parser.add_argument("--skip-span", dest="skip_spans", action="append")
    parser.add_argument("--monitor-only", action="store_true")
    args = parser.parse_args()

    send_start_notification(market=args.market)

    from monitor_orchestrator import run_monitor
    summary = await run_monitor(
        target_ticker=args.ticker,
        market=args.market,
        skip_spans=set(args.skip_spans or []),
    )

    if not summary.ng_tickers or args.monitor_only:
        run_phase4(args.market)
        return

    disc_ok = run_phase2(summary.ng_tickers)
    run_phase3(summary.ng_tickers, disc_ok)
    run_phase4(args.market)


if __name__ == "__main__":
    anyio.run(main)
