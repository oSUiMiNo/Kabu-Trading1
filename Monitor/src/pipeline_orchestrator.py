"""
パイプラインオーケストレーター（Monitor → Discussion → Planning → Watch）

4 大ブロックを順番に実行する。各ブロックは DB（伝言板方式）で連携し、
ブロック間のデータ受け渡しは archive テーブルを介して行う。

オーケストレーターの役割は大ブロックを順番に呼び出すだけであり、
ティッカー検出や並列化の責務は各ブロック（またはそのバッチアダプタ）が持つ。

Usage:
    python pipeline_orchestrator.py                    # 全銘柄パイプライン
    python pipeline_orchestrator.py --ticker NVDA      # 指定銘柄のみ
    python pipeline_orchestrator.py --market US        # 米国株のみ
    python pipeline_orchestrator.py --skip-span long   # 長期スキップ
    python pipeline_orchestrator.py --monitor-only     # Monitor のみ
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    fetch_active_for_discussion,
    fetch_active_for_planning,
    propagate_active_after_discussion,
)

from monitor_orchestrator import run_monitor
from notification_types import NotifyLabel, NotifyPayload, classify_label, MARKET_JA
from discord_notifier import notify, send_start_notification

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DISCUSSION_DIR = PROJECT_ROOT / "Discusion" / "src"
PLANNING_DIR = PROJECT_ROOT / "Planning" / "src"
WATCH_DIR = PROJECT_ROOT / "Watch" / "src"

SPAN_TO_JP = {"short": "短期", "mid": "中期", "long": "長期"}
MODE_TO_CLI = {"buy": "buy", "sell": "sell", "add": "add"}


# ── ユーティリティ ───────────────────────────────────────

def _find_venv_python(src_dir: Path) -> str:
    """プロジェクトの venv Python パスを返す。"""
    win = src_dir / ".venv" / "Scripts" / "python.exe"
    unix = src_dir / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if unix.exists():
        return str(unix)
    return "python"


def _find_latest_discussion_dir(ticker: str) -> Path | None:
    """銘柄の最新 Discussion ログディレクトリを返す。"""
    logs_dir = PROJECT_ROOT / "Discusion" / "logs"
    if not logs_dir.exists():
        return None
    pattern = re.compile(r"^\d{6}_\d{4}$")
    candidates = sorted(
        (d for d in logs_dir.iterdir()
         if d.is_dir() and pattern.match(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )
    t = ticker.upper()
    for d in candidates:
        if list(d.glob(f"{t}_final_judge_*.md")):
            return d
    return None


def _load_event_context() -> dict | None:
    """環境変数 EVENT_CONTEXT から JSON を読み取る。"""
    raw = os.environ.get("EVENT_CONTEXT", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Discussion バッチアダプタ ─────────────────────────────
#
# Discussion ブロックはバッチ実行モードを持たないため、
# DB から対象銘柄を検出し、各銘柄の Discussion subprocess を起動するアダプタ。

async def _run_discussion_subprocess(ticker: str, span: str, mode: str) -> int:
    """Discussion を subprocess で実行する（非同期）。"""
    python = _find_venv_python(DISCUSSION_DIR)
    script = str(DISCUSSION_DIR / "parallel_orchestrator.py")
    cmd = [python, script, ticker, span, MODE_TO_CLI.get(mode, "buy")]

    print(f"  [{ticker}] Discussion 起動: {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(DISCUSSION_DIR))
    await proc.wait()
    return proc.returncode


async def _run_discussion_with_retry(ticker: str, span: str, mode: str) -> int:
    """Discussion を実行し、失敗時はリトライする。"""
    exit_code = await _run_discussion_subprocess(ticker, span, mode)
    if exit_code == 0:
        return 0

    print(f"  [{ticker}] Discussion 失敗 (exit code: {exit_code})")

    discussion_dir = _find_latest_discussion_dir(ticker)
    if discussion_dir:
        print(f"  [{ticker}] Discussion ログ検出（{discussion_dir.name}）→ リトライ")
    else:
        print(f"  [{ticker}] Discussion ログなし → 全体リトライ")

    exit_code = await _run_discussion_subprocess(ticker, span, mode)
    if exit_code == 0:
        return 0

    print(f"  [{ticker}] Discussion リトライ失敗 (exit code: {exit_code})")
    return exit_code


async def run_discussion_batch(event_context: dict | None, display_names: dict | None = None) -> None:
    """Discussion ブロックのバッチ実行。DB から NG 銘柄を検出し、各銘柄の Discussion を実行する。"""
    print(f"\n{'='*60}")
    print(f"=== Phase 2: Discussion ===")
    print(f"{'='*60}")

    pending = safe_db(fetch_active_for_discussion) or []
    if not pending:
        print("  Discussion 対象なし。")
        return

    print(f"  対象: {len(pending)} 銘柄")
    for row in pending:
        print(f"    - {row['ticker']} (mode={row['mode']}, span={row['span']})")

    async def _process_one(row):
        ticker = row["ticker"]
        span = row["span"]
        mode = row["mode"]
        old_archive_id = row["id"]
        try:
            exit_code = await _run_discussion_with_retry(ticker, span, mode)
            if exit_code == 0:
                safe_db(propagate_active_after_discussion, ticker, old_archive_id)
                print(f"  [{ticker}] Discussion 完了")
            else:
                print(f"  [{ticker}] Discussion 全リトライ失敗 (exit code: {exit_code})")
                dn = (display_names or {}).get(ticker, ticker)
                payload = NotifyPayload(
                    label=NotifyLabel.ERROR,
                    ticker=ticker,
                    monitor_data={},
                    event_context=event_context,
                    error_detail=f"Discussion 失敗 (exit code: {exit_code})",
                    display_name=dn,
                )
                await notify(payload)
        except Exception as e:
            print(f"  [{ticker}] 予期しないエラー: {e}")

    await asyncio.gather(*[_process_one(row) for row in pending])


# ── Planning バッチアダプタ ───────────────────────────────
#
# Planning ブロックもバッチ実行モードを持たないため、
# DB から対象銘柄を検出し、各銘柄の Planning subprocess を起動するアダプタ。

async def run_planning_batch(event_context: dict | None, display_names: dict | None = None) -> None:
    """Planning ブロックのバッチ実行。DB から対象銘柄を検出し、各銘柄の Planning を実行する。"""
    print(f"\n{'='*60}")
    print(f"=== Phase 3: Planning ===")
    print(f"{'='*60}")

    pending = safe_db(fetch_active_for_planning) or []
    if not pending:
        print("  Planning 対象なし。")
        return

    print(f"  対象: {len(pending)} 銘柄")
    for row in pending:
        print(f"    - {row['ticker']} (span={row['span']})")

    async def _process_one(row):
        ticker = row["ticker"]
        span = row["span"]

        python = _find_venv_python(PLANNING_DIR)
        script = str(PLANNING_DIR / "plan_orchestrator.py")
        horizon_jp = SPAN_TO_JP.get(span, "中期")
        cmd = [python, script, ticker, horizon_jp]

        print(f"  [{ticker}] Planning 起動: {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(PLANNING_DIR))
            await proc.wait()
            if proc.returncode == 0:
                print(f"  [{ticker}] Planning 完了")
            else:
                print(f"  [{ticker}] Planning 失敗 (exit code: {proc.returncode})")
                dn = (display_names or {}).get(ticker, ticker)
                payload = NotifyPayload(
                    label=NotifyLabel.ERROR,
                    ticker=ticker,
                    monitor_data={},
                    event_context=event_context,
                    error_detail=f"Planning 失敗 (exit code: {proc.returncode})",
                    display_name=dn,
                )
                await notify(payload)
        except Exception as e:
            print(f"  [{ticker}] 予期しないエラー: {e}")

    await asyncio.gather(*[_process_one(row) for row in pending])


# ── Watch ────────────────────────────────────────────────

def run_watch() -> int:
    """Watch を subprocess で実行する。Watch が内部で全対象銘柄を自動検出・処理する。"""
    print(f"\n{'='*60}")
    print(f"=== Phase 4: Watch ===")
    print(f"{'='*60}")

    python = _find_venv_python(WATCH_DIR)
    script = str(WATCH_DIR / "watch_orchestrator.py")
    cmd = [python, script]

    print(f"  Watch 起動: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(WATCH_DIR))
    if result.returncode != 0:
        print(f"  Watch 失敗 (exit code: {result.returncode})")
    return result.returncode


# ── パイプライン本体 ─────────────────────────────────────
#
# オーケストレーターの役割：大ブロックを順番に呼び出すだけ。
# ティッカーの検出・ディスパッチは各ブロック（バッチアダプタ）の責務。

async def run_pipeline(
    target_ticker: str | None = None,
    monitor_only: bool = False,
    market: str | None = None,
    skip_spans: set[str] | None = None,
):
    """Monitor → Discussion → Planning → Watch パイプライン。"""
    event_context = _load_event_context()
    send_start_notification(market)

    # ── Phase 1: Monitor ──
    print(f"\n{'='*60}")
    print(f"=== Phase 1: Monitor ===")
    print(f"{'='*60}")

    summary = await run_monitor(target_ticker, market=market, skip_spans=skip_spans)
    dn_map = summary.display_names

    # Monitor ERROR / CHECK 通知（パイプラインレベル）
    for ticker, monitor_data in summary.results.items():
        if monitor_data.get("retries_exhausted"):
            payload = NotifyPayload(
                label=NotifyLabel.ERROR,
                ticker=ticker,
                monitor_data=monitor_data,
                event_context=event_context,
                error_detail=monitor_data.get("error_detail", "Monitor リトライ上限到達"),
                display_name=dn_map.get(ticker, ticker),
            )
            await notify(payload)
        elif classify_label(monitor_data) == NotifyLabel.CHECK:
            payload = NotifyPayload(
                label=NotifyLabel.CHECK,
                ticker=ticker,
                monitor_data=monitor_data,
                event_context=event_context,
                display_name=dn_map.get(ticker, ticker),
            )
            await notify(payload)

    # NG 銘柄の有無を DB から確認（伝言板方式）
    ng_tickers = safe_db(fetch_active_for_discussion) or []

    if not ng_tickers:
        print("\nNG 銘柄なし。Discussion/Planning/Watch は起動しません。")
        ok_tickers = [t for t, r in summary.results.items() if r.get("result") == "OK"]
        if ok_tickers:
            market_name = MARKET_JA.get(market, "全銘柄") if market else "全銘柄"
            ok_names = [dn_map.get(t, t) for t in ok_tickers]
            payload = NotifyPayload(
                label=NotifyLabel.COMPLETE,
                ticker=f"{market_name} 全銘柄OK",
                monitor_data={"tickers": ok_names},
                event_context=event_context,
            )
            await notify(payload)
        return

    if monitor_only:
        print("\n--monitor-only: Discussion/Planning/Watch は起動しません。")
        return

    # ── Phase 2〜4: 各ブロックを順番に呼び出す ──
    await run_discussion_batch(event_context, dn_map)
    await run_planning_batch(event_context, dn_map)
    run_watch()

    # ── COMPLETE 通知 ──
    print(f"\n{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"{'='*60}")

    all_tickers = list(summary.results.keys())
    ng_ticker_names = [ng["ticker"] for ng in ng_tickers]
    market_name = MARKET_JA.get(market, "全銘柄") if market else "全銘柄"
    all_names = [dn_map.get(t, t) for t in all_tickers]
    ng_names = [dn_map.get(t, t) for t in ng_ticker_names]
    payload = NotifyPayload(
        label=NotifyLabel.COMPLETE,
        ticker=f"{market_name} 全銘柄チェック完了",
        monitor_data={
            "tickers": all_names,
            "ng_tickers": ng_names,
        },
        event_context=event_context,
    )
    await notify(payload)


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
