"""
パイプラインオーケストレーター
Technical → ImportantIndicators → Monitor → Analyzer → Planning → Watch → ActionLog

各ブロックは DB（伝言板方式）で連携し、
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
    upsert_holding,
    get_portfolio_config,
)
from notification_types import NotifyLabel, NotifyPayload, classify_label, MARKET_JA, LABEL_COLOR
from discord_notifier import notify, send_start_notification
from indicator_filter import run_filter, ScheduleContext


# ── ユーティリティ ───────────────────────────────────────

def _load_event_context() -> dict | None:
    """環境変数 EVENT_CONTEXT から JSON を読み取る。"""
    raw = os.environ.get("EVENT_CONTEXT", "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed[0] if parsed else None
        return parsed
    except (json.JSONDecodeError, TypeError):
        return None


def _run_batch(script_name: str, extra_args: list[str] | None = None) -> int:
    """PJTルートの batch スクリプトを subprocess で実行する。"""
    cmd = [sys.executable, str(PROJECT_ROOT / script_name)] + (extra_args or [])
    print(f"  起動: {' '.join(cmd)}", flush=True)
    sys.stdout.flush()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"  {script_name} 失敗 (exit code: {result.returncode})", flush=True)
    return result.returncode


def _sync_holding_prices(wl: list[dict], target_ticker: str | None = None):
    """Technical が取得した最新株価を holdings.current_price に反映する。"""
    try:
        from supabase_client import get_client
        import yaml as _yaml
        today = datetime.now(_JST).strftime("%Y-%m-%d")
        for w in wl:
            tk = w["ticker"]
            if target_ticker and tk != target_ticker:
                continue
            resp = (
                get_client().from_("archive").select("technical")
                .eq("ticker", tk).not_.is_("technical", "null")
                .order("created_at", desc=True).limit(1).execute()
            )
            if not resp.data:
                continue
            tech = resp.data[0].get("technical")
            if isinstance(tech, str):
                tech = _yaml.safe_load(tech) if tech else {}
            if isinstance(tech, dict):
                price = tech.get("latest_price")
                if price:
                    safe_db(upsert_holding, tk, current_price=price, price_updated_at=today)
                    print(f"  [holdings] {tk}: current_price={price}")
    except Exception as e:
        print(f"  [holdings] 更新スキップ: {e}")


def _build_schedule_context(
    market: str | None,
    skip_spans: set[str] | None,
    config: dict | None = None,
) -> ScheduleContext:
    """引数 + 現在時刻 + config から定期フル実行対象の span を決定する。
    - 引け後スケジュール（skip_spans に "long" が含まれる）の場合:
      short は毎営業日フル実行、mid は config の full_run_mid_days の曜日のみフル実行
    - 週末スケジュール（market=None かつ土日）の場合: long をフル実行
    """
    now = datetime.now(_JST)
    dow = now.weekday()  # 0=Mon
    is_close_schedule = skip_spans and "long" in skip_spans
    mid_days = (config or {}).get("full_run_mid_days", [1, 4])

    full_run_spans: set[str] = set()
    if dow >= 5 and not market:
        full_run_spans.add("long")
    elif is_close_schedule and dow < 5:
        full_run_spans.add("short")
        if dow in mid_days:
            full_run_spans.add("mid")

    return ScheduleContext(full_run_spans=full_run_spans)


# ── パイプライン本体 ─────────────────────────────────────

async def run_pipeline(
    target_ticker: str | None = None,
    monitor_only: bool = False,
    market: str | None = None,
    skip_spans: set[str] | None = None,
):
    """Technical → Monitor → Analyzer → Planning → Watch パイプライン。"""
    from dotenv import load_dotenv
    env_path = PROJECT_ROOT / ".env.local"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    event_context = _load_event_context()
    send_start_notification(market, target_ticker=target_ticker)

    # display_names を事前取得（全フェーズの通知で使用）
    wl = safe_db(list_watchlist, active_only=True, market=market) or []
    dn_map = {w["ticker"]: w.get("display_name") or w["ticker"] for w in wl}
    if target_ticker:
        display_label = dn_map.get(target_ticker, target_ticker)
    else:
        display_label = MARKET_JA.get(market, "全銘柄") if market else "全銘柄"

    # Technical が archive レコードを作成するため、その前にタイムスタンプを記録
    # （後続の fetch_monitor_results_since で created_at >= この時刻を使う）
    pipeline_start = datetime.now(_JST).isoformat()

    # ── Phase 1: Technical ──
    print(f"\n{'='*60}", flush=True)
    print(f"=== Phase 1: Technical (market={market}) ===", flush=True)
    print(f"{'='*60}", flush=True)
    tech_args = ["--create-archive"]
    if target_ticker:
        tech_args.extend(["--ticker", target_ticker])
    if market:
        tech_args.extend(["--market", market])
    _run_batch("technical_batch.py", tech_args)

    # ── holdings.current_price を最新化 ──
    _sync_holding_prices(wl, target_ticker)

    # ── Phase 1.5: ImportantIndicators ──
    print(f"\n{'='*60}")
    print(f"=== Phase 1.5: ImportantIndicators ===")
    print(f"{'='*60}")
    ii_args = []
    if target_ticker:
        ii_args.extend(["--ticker", target_ticker])
    _run_batch("importantindicators_batch.py", ii_args)

    # ── Phase 1.7: IndicatorFilter ──
    # バイパス条件: --ticker 指定 / --monitor-only / filter_enabled=false
    cfg = safe_db(get_portfolio_config) or {}
    filter_enabled = cfg.get("indicator_filter_enabled", True) and not target_ticker and not monitor_only

    filtered_tickers: list[str] | None = None  # None = フィルター未適用（全銘柄 Monitor）
    if filter_enabled:
        print(f"\n{'='*60}")
        print(f"=== Phase 1.7: IndicatorFilter ===")
        print(f"{'='*60}")
        all_tickers_list = [w["ticker"] for w in wl]
        schedule_ctx = _build_schedule_context(market, skip_spans, cfg)
        print(f"  full_run_spans: {schedule_ctx.full_run_spans or '(なし)'}")

        filter_result = run_filter(all_tickers_list, pipeline_start, cfg, schedule_ctx)

        print(f"  status: {filter_result.status}")
        print(f"  market_gate: {filter_result.market_gate_triggered}")
        if filter_result.full_run_tickers:
            full_names = [dn_map.get(t, t) for t in filter_result.full_run_tickers]
            print(f"  full_run: {full_names}")
        if filter_result.triggered_tickers:
            trig_names = [dn_map.get(t, t) for t in filter_result.triggered_tickers]
            print(f"  triggered: {trig_names}")
        if filter_result.skipped_tickers:
            skip_names = [dn_map.get(t, t) for t in filter_result.skipped_tickers]
            print(f"  skipped: {skip_names}")
        for tk, reasons in filter_result.trigger_details.items():
            print(f"    [{tk}] {reasons}")

        monitor_target_tickers = filter_result.triggered_tickers + filter_result.full_run_tickers

        if not monitor_target_tickers:
            print("\nIndicatorFilter: 全銘柄スキップ。Monitor 以降は起動しません。", flush=True)

            # ── ActionLog（FILTER_SKIPPED path） ──
            print(f"\n{'='*60}")
            print(f"=== Phase 6: ActionLog ===")
            print(f"{'='*60}")
            _run_batch("actionlog_batch.py")

            all_names = [dn_map.get(t, t) for t in all_tickers_list]
            gate_text = "市場全体ゲート発火なし" if not filter_result.market_gate_triggered else "市場全体ゲート発火"
            payload = NotifyPayload(
                label=NotifyLabel.COMPLETE,
                ticker=f"{display_label} フィルター通過なし",
                monitor_data={
                    "tickers": all_names,
                    "filter_status": "FILTER_SKIPPED",
                    "gate": gate_text,
                },
                event_context=event_context,
            )
            await notify(payload)
            return

        filtered_tickers = monitor_target_tickers

    # ── Phase 2: Monitor ──
    print(f"\n{'='*60}")
    print(f"=== Phase 2: Monitor ===")
    print(f"{'='*60}")
    mon_args = []
    if target_ticker:
        mon_args.extend(["--ticker", target_ticker])
    elif filtered_tickers is not None:
        mon_args.extend(["--tickers", ",".join(filtered_tickers)])
    if market:
        mon_args.extend(["--market", market])
    if skip_spans and filtered_tickers is None:
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
        print("\nNG 銘柄なし。Analyzer/Planning/Watch は起動しません。", flush=True)

        # ── Phase 6: ActionLog（OK path） ──
        print(f"\n{'='*60}")
        print(f"=== Phase 6: ActionLog ===")
        print(f"{'='*60}")
        _run_batch("actionlog_batch.py")

        ok_tickers = [rec["ticker"] for rec in monitor_results
                      if (rec.get("monitor") or {}).get("result") == "OK"]
        error_tickers = [rec["ticker"] for rec in monitor_results
                         if (rec.get("monitor") or {}).get("result") == "ERROR"]
        ok_names = [dn_map.get(t, t) for t in ok_tickers]
        error_names = [dn_map.get(t, t) for t in error_tickers]
        if error_tickers and not ok_tickers:
            title = f"{display_label} エラー" if target_ticker else f"{display_label} 全銘柄エラー"
            summary_label = NotifyLabel.ERROR
        elif error_tickers:
            title = f"{display_label} チェック完了（エラーあり）" if target_ticker else f"{display_label} チェック完了（一部エラー）"
            summary_label = NotifyLabel.WARNING
        else:
            title = f"{display_label} OK" if target_ticker else f"{display_label} 全銘柄OK"
            summary_label = NotifyLabel.COMPLETE
        payload = NotifyPayload(
            label=summary_label,
            ticker=title,
            monitor_data={"tickers": ok_names, "error_tickers": error_names},
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

    # ── Phase 6: ActionLog ──
    print(f"\n{'='*60}")
    print(f"=== Phase 6: ActionLog ===")
    print(f"{'='*60}")
    _run_batch("actionlog_batch.py")

    # ── COMPLETE 通知 ──
    print(f"\n{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"{'='*60}")

    all_tickers = [rec["ticker"] for rec in monitor_results]
    all_names = [dn_map.get(t, t) for t in all_tickers]
    ng_names = [dn_map.get(t, t) for t in ng_ticker_names]
    payload = NotifyPayload(
        label=NotifyLabel.COMPLETE,
        ticker=f"{display_label} チェック完了" if target_ticker else f"{display_label} 全銘柄チェック完了",
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

    try:
        anyio.run(lambda: run_pipeline(target, monitor_only, _market, _skip_spans or None))
    except Exception as e:
        print(f"\n[FATAL] パイプライン異常終了: {e}", flush=True)
        try:
            from discord_notifier import send_webhook
            embed = {
                "title": "❌ [ エラー ]　パイプライン異常終了",
                "description": str(e)[:2000],
                "color": LABEL_COLOR.get(NotifyLabel.ERROR, 0x808080),
            }
            send_webhook(embed)
        except Exception:
            pass
        sys.exit(1)
