"""
Supabase 共有クライアント（postgrest ベース）

Discussion / Planning 両プロジェクトから利用する。
.env.local（プロジェクトルート）から認証情報を読み込み、
テーブルごとのヘルパー関数を提供する。

テーブル構成:
  sessions         … 1実行=1行。lanes/final_judge/plan/monitor を JSONB で格納
  portfolio_config … 投資設定（シングルトン）
  holdings         … 保有銘柄

使い方（各オーケストレーターから）:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    from supabase_client import safe_db, create_session, update_session, ...
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from postgrest import SyncPostgrestClient

_client: SyncPostgrestClient | None = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def safe_db(fn, *args, **kwargs):
    """DB 呼び出しの安全ラッパー。失敗時は警告出力のみで None を返す。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"  [DB警告] {e}")
        return None


def get_client() -> SyncPostgrestClient:
    global _client
    if _client is not None:
        return _client

    env_path = _PROJECT_ROOT / ".env.local"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_ANON_KEY"]

    _client = SyncPostgrestClient(
        f"{url}/rest/v1",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
    )
    return _client


# ── portfolio_config（シングルトン） ──────────────────────

def get_portfolio_config() -> dict:
    resp = get_client().from_("portfolio_config").select("*").limit(1).execute()
    return resp.data[0] if resp.data else {}


def get_discussion_config() -> dict:
    """Discussion パラメータを portfolio_config から取得。"""
    cfg = get_portfolio_config()
    return {
        "num_sets": cfg.get("discussion_num_lanes", 2),
        "max_rounds": cfg.get("discussion_max_rounds", 4),
        "opinions_per_set": cfg.get("discussion_opinions_per_lane", 2),
    }


def get_due_regular_schedules(now_utc: datetime) -> list[dict]:
    """
    現在時刻に該当する定期Monitorスケジュールを返す。
    マッチ条件: 予定時刻の40分前〜90分後、days_of_week 一致、
    monitor_last_runs で最終実行が150分以上前。
    ※ GitHub Actions の cron は最大78分程度の遅延が観測されており、許容幅を広くとる。
    """
    cfg = get_portfolio_config()
    if not cfg.get("monitor_schedule_enabled", True):
        return []

    schedules = cfg.get("monitor_schedules", [])
    last_runs = cfg.get("monitor_last_runs") or {}
    if isinstance(last_runs, str):
        last_runs = json.loads(last_runs)
    dow = now_utc.weekday()
    matched = []

    for sched in schedules:
        if dow not in sched.get("days_of_week", []):
            continue

        target_minute = sched["hour_utc"] * 60 + sched["minute_utc"]
        current_minute = now_utc.hour * 60 + now_utc.minute
        diff = current_minute - target_minute
        if diff < -40 or diff > 90:
            continue

        label = sched["label"]
        last_run_str = last_runs.get(label)
        if last_run_str:
            last_run_dt = datetime.fromisoformat(last_run_str)
            if last_run_dt.tzinfo is None:
                last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_utc - last_run_dt).total_seconds()
            if elapsed < 9000:
                continue

        matched.append(sched)

    return matched


def mark_regular_schedule_run(label: str, now_utc: datetime) -> None:
    """monitor_last_runs を更新して重複実行を防止。"""
    cfg = get_portfolio_config()
    last_runs = cfg.get("monitor_last_runs") or {}
    if isinstance(last_runs, str):
        last_runs = json.loads(last_runs)
    last_runs[label] = now_utc.isoformat()
    update_portfolio_config(monitor_last_runs=last_runs)


def update_portfolio_config(**fields) -> dict:
    cfg = get_portfolio_config()
    if not cfg:
        return {}
    resp = (
        get_client()
        .from_("portfolio_config")
        .update(fields)
        .eq("id", cfg["id"])
        .execute()
    )
    return resp.data[0] if resp.data else {}


# ── holdings ─────────────────────────────────────────────

def get_holding(ticker: str) -> dict | None:
    resp = (
        get_client()
        .from_("holdings")
        .select("*")
        .eq("ticker", ticker.upper())
        .execute()
    )
    return resp.data[0] if resp.data else None


def list_holdings() -> list[dict]:
    resp = get_client().from_("holdings").select("*").execute()
    return resp.data


def upsert_holding(ticker: str, **fields) -> dict:
    row = {"ticker": ticker.upper(), **fields}
    existing = get_holding(ticker)
    if existing:
        resp = (
            get_client()
            .from_("holdings")
            .update(row)
            .eq("id", existing["id"])
            .execute()
        )
    else:
        resp = get_client().from_("holdings").insert(row).execute()
    return resp.data[0] if resp.data else {}


# ── sessions（統合テーブル） ─────────────────────────────

def create_session(ticker: str, mode: str, horizon: str) -> dict:
    """セッション作成。horizon は DB カラム span にマッピングされる。"""
    resp = (
        get_client()
        .from_("sessions")
        .insert({
            "ticker": ticker.upper(),
            "mode": mode,
            "span": horizon,
            "status": "running",
        })
        .execute()
    )
    return resp.data[0]


def update_session(session_id: int, **fields) -> dict:
    """セッション更新。jsonb カラム (lanes, final_judge, plan, monitor) は dict で渡す。"""
    payload = {}
    for k, v in fields.items():
        if isinstance(v, dict):
            payload[k] = json.dumps(v, ensure_ascii=False)
        else:
            payload[k] = v
    resp = (
        get_client()
        .from_("sessions")
        .update(payload)
        .eq("id", session_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


def get_latest_session(ticker: str) -> dict | None:
    """指定銘柄の最新セッションを取得。"""
    resp = (
        get_client()
        .from_("sessions")
        .select("*")
        .eq("ticker", ticker.upper())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_latest_session_with_plan(ticker: str) -> dict | None:
    """指定銘柄で plan が存在する最新の completed セッションを取得。"""
    resp = (
        get_client()
        .from_("sessions")
        .select("*")
        .eq("ticker", ticker.upper())
        .eq("status", "completed")
        .not_.is_("plan", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


# ── watchlist ─────────────────────────────────────────

def list_watchlist(active_only: bool = True, market: str | None = None) -> list[dict]:
    """監視対象の銘柄一覧を取得。market='US'/'JP' で市場フィルタ。"""
    q = get_client().from_("watchlist").select("*")
    if active_only:
        q = q.eq("active", True)
    if market:
        q = q.eq("market", market.upper())
    resp = q.order("created_at").execute()
    return resp.data


# ── event_master ─────────────────────────────────────────

def upsert_event_master(event: dict) -> dict:
    existing = (
        get_client()
        .from_("event_master")
        .select("event_id")
        .eq("event_id", event["event_id"])
        .execute()
    )
    if existing.data:
        resp = (
            get_client()
            .from_("event_master")
            .update(event)
            .eq("event_id", event["event_id"])
            .execute()
        )
    else:
        resp = get_client().from_("event_master").insert(event).execute()
    return resp.data[0] if resp.data else {}


def list_event_masters(region: str | None = None) -> list[dict]:
    q = get_client().from_("event_master").select("*")
    if region:
        q = q.eq("region", region.upper())
    resp = q.order("event_id").execute()
    return resp.data


# ── event_date_time ──────────────────────────────────────

def upsert_event_date_time(occ: dict) -> dict:
    existing = (
        get_client()
        .from_("event_date_time")
        .select("occurrence_id")
        .eq("event_id", occ["event_id"])
        .eq("scheduled_date_local", occ["scheduled_date_local"])
        .execute()
    )
    if existing.data:
        oid = existing.data[0]["occurrence_id"]
        update_data = {k: v for k, v in occ.items() if k not in ("event_id", "scheduled_date_local")}
        if update_data:
            resp = (
                get_client()
                .from_("event_date_time")
                .update(update_data)
                .eq("occurrence_id", oid)
                .execute()
            )
        else:
            resp = existing
        result = resp.data[0] if resp.data else {}
        result["occurrence_id"] = oid
        return result
    else:
        resp = get_client().from_("event_date_time").insert(occ).execute()
        return resp.data[0] if resp.data else {}


def list_event_date_times(
    event_id: str, from_date: str | None = None, to_date: str | None = None
) -> list[dict]:
    q = (
        get_client()
        .from_("event_date_time")
        .select("*")
        .eq("event_id", event_id)
    )
    if from_date:
        q = q.gte("scheduled_date_local", from_date)
    if to_date:
        q = q.lte("scheduled_date_local", to_date)
    resp = q.order("scheduled_date_local").execute()
    return resp.data


# ── monitor_schedule ─────────────────────────────────────

def upsert_monitor_schedule(watch: dict) -> dict:
    existing = (
        get_client()
        .from_("monitor_schedule")
        .select("watch_id")
        .eq("occurrence_id", watch["occurrence_id"])
        .eq("watch_kind", watch["watch_kind"])
        .eq("market", watch["market"])
        .execute()
    )
    if existing.data:
        wid = existing.data[0]["watch_id"]
        update_data = {k: v for k, v in watch.items() if k not in ("occurrence_id", "watch_kind", "market")}
        if update_data:
            get_client().from_("monitor_schedule").update(update_data).eq("watch_id", wid).execute()
        return {"watch_id": wid}
    else:
        resp = get_client().from_("monitor_schedule").insert(watch).execute()
        return resp.data[0] if resp.data else {}


def list_pending_monitors(
    from_utc: str, to_utc: str, market: str | None = None
) -> list[dict]:
    q = (
        get_client()
        .from_("monitor_schedule")
        .select("*, event_date_time(event_id, scheduled_date_local)")
        .eq("consumed", False)
        .gte("watch_at_utc", from_utc)
        .lte("watch_at_utc", to_utc)
    )
    if market:
        q = q.eq("market", market.upper())
    resp = q.order("watch_at_utc").execute()
    return resp.data


def mark_monitor_consumed(watch_id: int) -> dict:
    from datetime import datetime, timezone
    resp = (
        get_client()
        .from_("monitor_schedule")
        .update({"consumed": True, "consumed_at": datetime.now(timezone.utc).isoformat()})
        .eq("watch_id", watch_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


# ── event_scheduler_log ──────────────────────────────────

def create_scheduler_log(run_type: str) -> dict:
    resp = (
        get_client()
        .from_("event_scheduler_log")
        .insert({"run_type": run_type})
        .execute()
    )
    return resp.data[0] if resp.data else {}


def update_scheduler_log(run_id: int, **fields) -> dict:
    resp = (
        get_client()
        .from_("event_scheduler_log")
        .update(fields)
        .eq("run_id", run_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}
