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


# ── event_occurrence ─────────────────────────────────────

def upsert_event_occurrence(occ: dict) -> dict:
    existing = (
        get_client()
        .from_("event_occurrence")
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
                .from_("event_occurrence")
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
        resp = get_client().from_("event_occurrence").insert(occ).execute()
        return resp.data[0] if resp.data else {}


def list_event_occurrences(
    event_id: str, from_date: str | None = None, to_date: str | None = None
) -> list[dict]:
    q = (
        get_client()
        .from_("event_occurrence")
        .select("*")
        .eq("event_id", event_id)
    )
    if from_date:
        q = q.gte("scheduled_date_local", from_date)
    if to_date:
        q = q.lte("scheduled_date_local", to_date)
    resp = q.order("scheduled_date_local").execute()
    return resp.data


# ── watch_schedule ───────────────────────────────────────

def upsert_watch_schedule(watch: dict) -> dict:
    existing = (
        get_client()
        .from_("watch_schedule")
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
            get_client().from_("watch_schedule").update(update_data).eq("watch_id", wid).execute()
        return {"watch_id": wid}
    else:
        resp = get_client().from_("watch_schedule").insert(watch).execute()
        return resp.data[0] if resp.data else {}


def list_pending_watches(
    from_utc: str, to_utc: str, market: str | None = None
) -> list[dict]:
    q = (
        get_client()
        .from_("watch_schedule")
        .select("*, event_occurrence(event_id, scheduled_date_local)")
        .eq("consumed", False)
        .gte("watch_at_utc", from_utc)
        .lte("watch_at_utc", to_utc)
    )
    if market:
        q = q.eq("market", market.upper())
    resp = q.order("watch_at_utc").execute()
    return resp.data


def mark_watch_consumed(watch_id: int) -> dict:
    from datetime import datetime, timezone
    resp = (
        get_client()
        .from_("watch_schedule")
        .update({"consumed": True, "consumed_at": datetime.now(timezone.utc).isoformat()})
        .eq("watch_id", watch_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


# ── ingest_run ───────────────────────────────────────────

def create_ingest_run(run_type: str) -> dict:
    resp = (
        get_client()
        .from_("ingest_run")
        .insert({"run_type": run_type})
        .execute()
    )
    return resp.data[0] if resp.data else {}


def update_ingest_run(run_id: int, **fields) -> dict:
    resp = (
        get_client()
        .from_("ingest_run")
        .update(fields)
        .eq("run_id", run_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}
