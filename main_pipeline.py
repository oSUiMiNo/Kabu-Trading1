"""
パイプラインオーケストレーター（Technical → Monitor → Analyzer → Planning → Watch）

5 大ブロックを順番に実行する。各ブロックは DB（伝言板方式）で連携し、
ブロック間のデータ受け渡しは archive テーブルを介して行う。

各ブロックの複数銘柄並列実行は *_batch.py が担い、
このファイルはブロックを順番に呼び出すだけ。

Usage:
    python main_pipeline.py                    # 全銘柄パイプライン
    python main_pipeline.py --ticker NVDA      # 指定銘柄のみ
    python main_pipeline.py --market US        # 米国株のみ
    python main_pipeline.py --skip-span long   # 長期スキップ
    python main_pipeline.py --monitor-only     # Monitor のみ
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio

_JST = timezone(timedelta(hours=9))

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from supabase_client import (
    safe_db,
    list_watchlist,
    fetch_active_for_analyzer,
    fetch_active_for_planning,
    fetch_today_monitor_results,
    fetch_monitor_results_since,
    get_archivelog_by_id,
    update_archivelog,
)
from notification_types import NotifyLabel, NotifyPayload, classify_label, MARKET_JA
from discord_notifier import notify, send_start_notification


# ── ユーティリティ ───────────────────────────────────────

def _load_event_context() -> dict | None:
    """環境変数 EVENT_CONTEXT から JSON を読み取る。"""
    raw = os.environ.get("EVENT_CONTEXT", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _run_batch(script_name: str, extra_args: list[str] | None = None) -> int:
    """PJTルートの batch スクリプトを subprocess で実行する。"""
    cmd = [sys.executable, str(PROJECT_ROOT / script_name)] + (extra_args or [])
    print(f"  起動: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"  {script_name} 失敗 (exit code: {result.returncode})")
    return result.returncode


# ── パイプライン本体 ─────────────────────────────────────

async def run_pipeline(
    target_ticker: str | None = None,
    monitor_only: bool = False,
    market: str | None = None,
    skip_spans: set[str] | None = None,
):
    """Technical → Monitor → Analyzer → Planning → Watch パイプライン。"""
    event_context = _load_event_context()
    send_start_notification(market)

    # display_names を事前取得（全フェーズの通知で使用）
    wl = safe_db(list_watchlist, active_only=True, market=market) or []
    dn_map = {w["ticker"]: w.get("display_name") or w["ticker"] for w in wl}

    # Technical が archive レコードを作成するため、その前にタイムスタンプを記録
    # （後続の fetch_monitor_results_since で created_at >= この時刻を使う）
    pipeline_start = datetime.now(_JST).isoformat()

    # ── Phase 1: Technical ──
    print(f"\n{'='*60}")
    print(f"=== Phase 1: Technical ===")
    print(f"{'='*60}")
    tech_args = []
    if target_ticker:
        tech_args.extend(["--ticker", target_ticker])
    if market:
        tech_args.extend(["--market", market])
    _run_batch("technical_batch.py", tech_args)

    # ── Phase 2: Monitor ──
    print(f"\n{'='*60}")
    print(f"=== Phase 2: Monitor ===")
    print(f"{'='*60}")
    mon_args = []
    if target_ticker:
        mon_args.extend(["--ticker", target_ticker])
    if market:
        mon_args.extend(["--market", market])
    if skip_spans:
        for span in skip_spans:
            mon_args.extend(["--skip-span", span])
    _run_batch("monitor_batch.py", mon_args)

    # Monitor 後処理：この実行で作られた結果のみ取得し、対象 market で絞る
    wl_tickers = {w["ticker"] for w in wl}
    monitor_results = safe_db(fetch_monitor_results_since, pipeline_start) or []
    monitor_results = [r for r in monitor_results if r["ticker"] in wl_tickers]
    for rec in monitor_results:
        ticker = rec["ticker"]
        monitor_data = rec.get("monitor") or {}
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
    ng_tickers = safe_db(fetch_active_for_analyzer) or []

    if not ng_tickers:
        print("\nNG 銘柄なし。Analyzer/Planning/Watch は起動しません。")
        ok_tickers = [rec["ticker"] for rec in monitor_results
                      if (rec.get("monitor") or {}).get("result") == "OK"]
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
        print("\n--monitor-only: Analyzer/Planning/Watch は起動しません。")
        return

    # ── Phase 3: Analyzer ──
    print(f"\n{'='*60}")
    print(f"=== Phase 3: Analyzer ===")
    print(f"{'='*60}")
    _run_batch("analyzer_batch.py")

    # Post-Analyzer: エラー検出
    ng_ticker_names = []
    for row in ng_tickers:
        ticker = row["ticker"]
        archive_id = row["id"]
        record = safe_db(get_archivelog_by_id, archive_id)
        if record and record.get("final_judge"):
            ng_ticker_names.append(ticker)
            print(f"  [{ticker}] Analyzer 完了 → Planning に引き継ぎ")
        else:
            print(f"  [{ticker}] Analyzer 失敗（final_judge 未生成）")
            safe_db(update_archivelog, archive_id, status="failed", active=False)
            dn = dn_map.get(ticker, ticker)
            payload = NotifyPayload(
                label=NotifyLabel.ERROR,
                ticker=ticker,
                monitor_data={},
                event_context=event_context,
                error_detail="Analyzer で final_judge が生成されませんでした",
                display_name=dn,
            )
            await notify(payload)

    # ── Phase 4: Planning ──
    print(f"\n{'='*60}")
    print(f"=== Phase 4: Planning ===")
    print(f"{'='*60}")
    _run_batch("planning_batch.py")

    # Post-Planning: エラー検出 + 失敗レコードを status=failed にして残存防止
    planning_failed = safe_db(fetch_active_for_planning) or []
    for row in planning_failed:
        ticker = row["ticker"]
        archive_id = row["id"]
        print(f"  [{ticker}] Planning 失敗（newplan_full 未生成）")
        safe_db(update_archivelog, archive_id, status="failed", active=False)
        dn = dn_map.get(ticker, ticker)
        payload = NotifyPayload(
            label=NotifyLabel.ERROR,
            ticker=ticker,
            monitor_data={},
            event_context=event_context,
            error_detail="Planning で newplan_full が生成されませんでした",
            display_name=dn,
        )
        await notify(payload)

    # ── Phase 5: Watch ──
    print(f"\n{'='*60}")
    print(f"=== Phase 5: Watch ===")
    print(f"{'='*60}")
    _run_batch("watch_batch.py")

    # ── COMPLETE 通知 ──
    print(f"\n{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"{'='*60}")

    all_tickers = [rec["ticker"] for rec in monitor_results]
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
