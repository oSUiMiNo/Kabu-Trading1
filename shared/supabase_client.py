"""
Supabase 共有クライアント（postgrest ベース）

Discussion / Planning 両プロジェクトから利用する。
.env.local（プロジェクトルート）から認証情報を読み込み、
テーブルごとのヘルパー関数を提供する。

使い方（各オーケストレーターから）:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    from supabase_client import get_portfolio_config, get_holding, ...
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from postgrest import SyncPostgrestClient

_client: SyncPostgrestClient | None = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


# ── sessions ─────────────────────────────────────────────

def create_session(
    ticker: str, mode: str, horizon: str,
    num_lanes: int, session_dir: str,
) -> dict:
    resp = (
        get_client()
        .from_("sessions")
        .insert({
            "ticker": ticker.upper(),
            "mode": mode,
            "horizon": horizon,
            "num_lanes": num_lanes,
            "session_dir": session_dir,
            "status": "running",
        })
        .execute()
    )
    return resp.data[0]


def update_session(session_id: int, **fields) -> dict:
    resp = (
        get_client()
        .from_("sessions")
        .update(fields)
        .eq("id", session_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


def find_session_by_dir(session_dir: str) -> dict | None:
    resp = (
        get_client()
        .from_("sessions")
        .select("*")
        .eq("session_dir", session_dir)
        .execute()
    )
    return resp.data[0] if resp.data else None


# ── discussion_logs ──────────────────────────────────────

def insert_discussion_log(
    session_id: int, ticker: str, lane_num: int, **fields,
) -> dict:
    row = {
        "session_id": session_id,
        "ticker": ticker.upper(),
        "lane_num": lane_num,
        **fields,
    }
    resp = get_client().from_("discussion_logs").insert(row).execute()
    return resp.data[0]


# ── judge_logs ───────────────────────────────────────────

def insert_judge_log(
    session_id: int, ticker: str, lane_num: int,
    judge_num: int, agreement: str, **fields,
) -> dict:
    row = {
        "session_id": session_id,
        "ticker": ticker.upper(),
        "lane_num": lane_num,
        "judge_num": judge_num,
        "agreement": agreement,
        **fields,
    }
    resp = get_client().from_("judge_logs").insert(row).execute()
    return resp.data[0]


# ── final_judge_logs ─────────────────────────────────────

def insert_final_judge_log(
    session_id: int, ticker: str, verdict: str, **fields,
) -> dict:
    row = {
        "session_id": session_id,
        "ticker": ticker.upper(),
        "verdict": verdict,
        **fields,
    }
    resp = get_client().from_("final_judge_logs").insert(row).execute()
    return resp.data[0]


# ── plan_specs ───────────────────────────────────────────

def insert_plan_spec(
    ticker: str, plan_id: str, decision_final: str,
    horizon: str, yaml_full: str,
    session_id: int | None = None, **fields,
) -> dict:
    row = {
        "ticker": ticker.upper(),
        "plan_id": plan_id,
        "decision_final": decision_final,
        "horizon": horizon,
        "yaml_full": yaml_full,
        **fields,
    }
    if session_id is not None:
        row["session_id"] = session_id
    resp = get_client().from_("plan_specs").insert(row).execute()
    return resp.data[0]
