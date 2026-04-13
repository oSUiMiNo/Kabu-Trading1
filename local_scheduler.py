"""
ローカル定期実行スケジューラー

Windows タスクスケジューラーから5分間隔で呼び出される。
Monitor/src/event_watch_check.py + event-monitor.yml の機能をローカル用に統合。

役割:
  1. monitor_schedule テーブルからイベント watch を検出（未消化 + 24時間以内）
  2. portfolio_config.monitor_schedules から定期スケジュールを検出
  3. マッチしたら main_pipeline.py を market ごとに subprocess 実行

Usage:
    uv run --project Monitor/src python local_scheduler.py
    uv run --project Monitor/src python local_scheduler.py --dry-run
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# .env.local ロード
from dotenv import load_dotenv

env_path = PROJECT_ROOT / ".env.local"
if env_path.exists():
    load_dotenv(env_path, override=False)

# shared モジュールを参照
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from postgrest import SyncPostgrestClient
from supabase_client import (
    get_due_regular_schedules,
    get_portfolio_config,
    mark_regular_schedule_run,
)

EVENT_WATCH_MAX_AGE_HOURS = 24


def setup_logging() -> logging.Logger:
    log_dir = PROJECT_ROOT / "logs" / "scheduler"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("scheduler")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = TimedRotatingFileHandler(
            log_dir / "scheduler.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(ch)

    return logger


def get_db_client() -> SyncPostgrestClient:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_ANON_KEY が未設定")
    return SyncPostgrestClient(
        f"{url}/rest/v1",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )


def check_event_watches(
    client: SyncPostgrestClient,
    now: datetime,
    log: logging.Logger,
    dry_run: bool = False,
) -> tuple[set[str], list[dict], list[str]]:
    """monitor_schedule テーブルからイベント watch を検出。

    Returns:
        (markets, event_details, skip_spans)
    """
    cutoff = now - timedelta(hours=EVENT_WATCH_MAX_AGE_HOURS)

    resp = (
        client.from_("monitor_schedule")
        .select(
            "watch_id, watch_kind, market, watch_at_utc, occurrence_id, "
            "event_date_time(event_id, scheduled_date_local, "
            "event_master(name_ja))"
        )
        .eq("consumed", False)
        .gte("watch_at_utc", cutoff.isoformat())
        .lte("watch_at_utc", now.isoformat())
        .order("watch_at_utc")
        .execute()
    )
    watches = resp.data or []

    if not watches:
        log.info("イベント watch: 0 件")
        return set(), [], []

    log.info("イベント watch: %d 件検出", len(watches))
    markets: set[str] = set()
    event_details: list[dict] = []

    for w in watches:
        evt = w.get("event_date_time") or {}
        master = evt.get("event_master") or {}
        event_id = evt.get("event_id", "-")
        sched_date = evt.get("scheduled_date_local", "-")
        log.info(
            "  watch_id=%s  event=%s  date=%s  kind=%s  market=%s",
            w["watch_id"], event_id, sched_date, w["watch_kind"], w["market"],
        )

        # consumed に更新（dry-run 時はスキップ）
        if not dry_run:
            client.from_("monitor_schedule").update(
                {
                    "consumed": True,
                    "consumed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("watch_id", w["watch_id"]).execute()

        if w.get("market"):
            markets.add(w["market"])
        event_details.append(
            {
                "event_id": evt.get("event_id", ""),
                "name_ja": master.get("name_ja", ""),
                "watch_kind": w.get("watch_kind", ""),
            }
        )

    return markets, event_details, []


def check_regular_schedules(
    now: datetime,
    log: logging.Logger,
    dry_run: bool = False,
) -> tuple[set[str], list[str]]:
    """portfolio_config.monitor_schedules から定期スケジュールを検出。

    Returns:
        (markets, skip_spans)
    """
    try:
        due = get_due_regular_schedules(now)
    except Exception as e:
        log.warning("定期スケジュール取得エラー: %s", e)
        return set(), []

    if not due:
        log.info("定期スケジュール: 0 件マッチ")
        return set(), []

    log.info("定期スケジュール: %d 件マッチ", len(due))
    markets: set[str] = set()
    skip_spans: list[str] = []

    for sched in due:
        label = sched["label"]
        market = sched.get("market")
        sched_skip = sched.get("skip_spans", [])
        log.info(
            "  label=%s  market=%s  skip_spans=%s",
            label, market or "(全銘柄)", sched_skip,
        )

        if market:
            markets.add(market)
        skip_spans.extend(sched_skip)

        if not dry_run:
            try:
                mark_regular_schedule_run(label, now)
                log.info("  label=%s → last_run 更新", label)
            except Exception as e:
                log.warning("  last_run 更新失敗: %s", e)

    return markets, skip_spans


def run_pipeline(
    markets: list[str],
    skip_spans: list[str],
    event_details: list[dict],
    log: logging.Logger,
) -> bool:
    """main_pipeline.py を market ごとに subprocess 実行。

    1 market 失敗しても他 market は続行する。
    Returns: 全 market 成功なら True、1つでも失敗なら False。
    """
    env = os.environ.copy()
    if event_details:
        env["EVENT_CONTEXT"] = json.dumps(event_details, ensure_ascii=False)

    skip_args = []
    for span in skip_spans:
        skip_args.extend(["--skip-span", span])

    failed: list[str] = []

    if not markets:
        cmd = [sys.executable, str(PROJECT_ROOT / "main_pipeline.py")] + skip_args
        log.info("パイプライン起動: 全銘柄 %s", " ".join(skip_args) if skip_args else "")
        result = subprocess.run(cmd, env=env, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            log.error("main_pipeline.py 失敗 (exit code: %d)", result.returncode)
            failed.append("全銘柄")
        else:
            log.info("main_pipeline.py 完了 (exit code: 0)")
    else:
        for market in sorted(markets):
            cmd = (
                [sys.executable, str(PROJECT_ROOT / "main_pipeline.py")]
                + ["--market", market]
                + skip_args
            )
            log.info("パイプライン起動: market=%s %s", market, " ".join(skip_args) if skip_args else "")
            result = subprocess.run(cmd, env=env, cwd=str(PROJECT_ROOT))
            if result.returncode != 0:
                log.error("main_pipeline.py --market %s 失敗 (exit code: %d)", market, result.returncode)
                failed.append(market)
            else:
                log.info("main_pipeline.py --market %s 完了 (exit code: 0)", market)

    if failed:
        log.error("失敗した market: %s", failed)
    return len(failed) == 0


def main():
    dry_run = "--dry-run" in sys.argv

    log = setup_logging()
    log.info("=== ローカルスケジューラー起動%s ===", "（dry-run）" if dry_run else "")

    # monitor_schedule_enabled チェック
    try:
        cfg = get_portfolio_config() or {}
    except Exception as e:
        log.error("portfolio_config 取得失敗: %s", e)
        return
    if not cfg.get("monitor_schedule_enabled", True):
        log.info("monitor_schedule_enabled=false → 終了")
        return

    now = datetime.now(timezone.utc)

    # DB クライアント作成
    try:
        client = get_db_client()
    except RuntimeError as e:
        log.error("%s", e)
        return

    # 1. イベント watch チェック
    event_markets, event_details, event_skip_spans = check_event_watches(client, now, log, dry_run)

    # 2. 定期スケジュールチェック
    regular_markets, regular_skip_spans = check_regular_schedules(now, log, dry_run)

    # 3. 結果統合（market は set で一意化、skip_spans は union）
    all_markets = sorted(event_markets | regular_markets)
    all_skip_spans = sorted(set(regular_skip_spans + event_skip_spans))

    if not event_markets and not regular_markets and not event_details:
        log.info("トリガーなし。終了。")
        return

    log.info("対象市場: %s", all_markets or ["(全銘柄)"])
    if all_skip_spans:
        log.info("skip_spans: %s", all_skip_spans)

    if dry_run:
        log.info("dry-run モードのためパイプラインは起動しません。")
        return

    # 4. パイプライン起動
    success = run_pipeline(all_markets, all_skip_spans, event_details, log)

    if success:
        log.info("=== ローカルスケジューラー完了 ===")
    else:
        log.error("=== ローカルスケジューラー完了（一部失敗あり） ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
