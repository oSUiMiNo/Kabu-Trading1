"""
Event & Regular Schedule Watch Check（軽量 DB チェッカー）

2種類のトリガーを検出する:
  1. イベント watch … monitor_schedule テーブルで期限到来かつ未消化の watch
  2. 定期スケジュール … portfolio_config.monitor_schedules の時刻マッチ

どちらかが該当すれば対象市場を GITHUB_OUTPUT に出力し、
後続の Monitor パイプラインを起動させる。

GitHub Actions の 5分間隔 cron ステップから呼ばれる。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from postgrest import SyncPostgrestClient

LOOKBACK_MINUTES = 30

env_path = Path(__file__).resolve().parent.parent.parent / ".env.local"
if env_path.exists():
    load_dotenv(env_path, override=False)

url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_ANON_KEY", "")
if not url or not key:
    print("[ERROR] SUPABASE_URL / SUPABASE_ANON_KEY が未設定")
    sys.exit(1)

client = SyncPostgrestClient(
    f"{url}/rest/v1",
    headers={"apikey": key, "Authorization": f"Bearer {key}"},
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import get_due_regular_schedules, mark_regular_schedule_run

now = datetime.now(timezone.utc)
cutoff = now - timedelta(minutes=LOOKBACK_MINUTES)

gh_output = os.environ.get("GITHUB_OUTPUT")


def write_output(key: str, value: str):
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"{key}={value}\n")


# ── 1. イベント watch チェック ──
resp = (
    client.from_("monitor_schedule")
    .select("watch_id, watch_kind, market, watch_at_utc, occurrence_id, "
            "event_date_time(event_id, scheduled_date_local, "
            "event_master(name_ja))")
    .eq("consumed", False)
    .gte("watch_at_utc", cutoff.isoformat())
    .lte("watch_at_utc", now.isoformat())
    .order("watch_at_utc")
    .execute()
)
watches = resp.data or []

event_markets: set[str] = set()
event_details: list[dict] = []
skip_spans_from_events: list[str] = []

if watches:
    print(f"{len(watches)} 件のイベント watch を検出:")
    for w in watches:
        evt = w.get("event_date_time") or {}
        event_id = evt.get("event_id", "-")
        sched = evt.get("scheduled_date_local", "-")
        print(f"  watch_id={w['watch_id']}  event={event_id}  date={sched}  "
              f"kind={w['watch_kind']}  market={w['market']}  at={w['watch_at_utc']}")

    for w in watches:
        (
            client.from_("monitor_schedule")
            .update({
                "consumed": True,
                "consumed_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("watch_id", w["watch_id"])
            .execute()
        )
        print(f"  watch_id={w['watch_id']} → consumed")

    event_markets = set(w["market"] for w in watches)
    for w in watches:
        evt = w.get("event_date_time") or {}
        master = evt.get("event_master") or {}
        event_details.append({
            "event_id": evt.get("event_id", ""),
            "name_ja": master.get("name_ja", ""),
            "watch_kind": w.get("watch_kind", ""),
        })
else:
    print("期限到来のイベント watch なし。")


# ── 2. 定期スケジュール チェック ──
regular_markets: set[str] = set()
regular_skip_spans: list[str] = []

try:
    due_schedules = get_due_regular_schedules(now)
except Exception as e:
    print(f"  [定期スケジュール警告] {e}")
    due_schedules = []

if due_schedules:
    print(f"\n{len(due_schedules)} 件の定期スケジュールがマッチ:")
    for sched in due_schedules:
        label = sched["label"]
        market = sched.get("market")
        skip_spans = sched.get("skip_spans", [])
        print(f"  label={label}  market={market or '(全銘柄)'}  "
              f"skip_spans={skip_spans}")

        if market:
            regular_markets.add(market)
        regular_skip_spans.extend(skip_spans)

        try:
            mark_regular_schedule_run(label, now)
            print(f"  label={label} → last_run 更新")
        except Exception as e:
            print(f"  [警告] last_run 更新失敗: {e}")
else:
    print("マッチする定期スケジュールなし。")


# ── 3. 結果合成 ──
all_markets = sorted(event_markets | regular_markets)
all_skip_spans = sorted(set(regular_skip_spans + skip_spans_from_events))
has_watches = bool(watches) or bool(due_schedules)

if not has_watches:
    print("\nトリガーなし。終了。")
    write_output("has_watches", "false")
    sys.exit(0)

print(f"\n対象市場: {all_markets or ['(全銘柄)']}")
if all_skip_spans:
    print(f"skip_spans: {all_skip_spans}")

write_output("has_watches", "true")
write_output("markets", " ".join(all_markets))
write_output("skip_spans", " ".join(all_skip_spans))
if event_details:
    write_output("event_details", json.dumps(event_details, ensure_ascii=False))
