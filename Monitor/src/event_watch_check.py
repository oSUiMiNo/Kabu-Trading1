"""
Event Watch Check（軽量 DB チェッカー）

monitor_schedule テーブルで期限到来かつ未消化の watch を検出する。
該当があれば consumed マーク → 対象市場を GITHUB_OUTPUT に出力。

GitHub Actions の 5分間隔 cron ステップから呼ばれる。
依存は postgrest + python-dotenv のみで、フル deps 不要。
watch が無ければ即終了し、後続ステップをスキップさせる。
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

now = datetime.now(timezone.utc)
cutoff = now - timedelta(minutes=LOOKBACK_MINUTES)

resp = (
    client.from_("monitor_schedule")
    .select("watch_id, watch_kind, market, watch_at_utc, occurrence_id, "
            "event_date_time(event_id, scheduled_date_local)")
    .eq("consumed", False)
    .gte("watch_at_utc", cutoff.isoformat())
    .lte("watch_at_utc", now.isoformat())
    .order("watch_at_utc")
    .execute()
)
watches = resp.data or []

gh_output = os.environ.get("GITHUB_OUTPUT")


def write_output(key: str, value: str):
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"{key}={value}\n")


if not watches:
    print("期限到来の watch なし。終了。")
    write_output("has_watches", "false")
    sys.exit(0)

print(f"{len(watches)} 件の watch を検出:")
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

markets = sorted(set(w["market"] for w in watches))
print(f"\n対象市場: {markets}")

write_output("has_watches", "true")
write_output("markets", " ".join(markets))
