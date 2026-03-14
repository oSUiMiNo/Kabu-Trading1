"""
Event & Regular Schedule Watch Check（軽量 DB チェッカー）

3つの役割を持つ:
  1. イベント watch … monitor_schedule テーブルで期限到来かつ未消化の watch
  2. 定期スケジュール … portfolio_config.monitor_schedules の時刻マッチ
  3. pg_cron 死活監視 … pg_cron の最終成功が24時間以上前なら自動 Fast Reboot

どちらかが該当すれば対象市場を GITHUB_OUTPUT に出力し、
後続の Monitor パイプラインを起動させる。

GitHub Actions の 5分間隔 cron ステップ、および
pg_cron → pg_net の workflow_dispatch 経由で呼ばれる。
"""

import json
import os
import sys
import urllib.request
import urllib.error
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


# ── 4. pg_cron 死活監視 ──
def check_pg_cron_and_reboot():
    """pg_cron の稼働状態を確認し、24時間以上成功なしなら自動 Fast Reboot する。"""
    mgmt_token = os.environ.get("SUPABASE_MANAGEMENT_API_TOKEN")
    project_ref = os.environ.get("SUPABASE_PROJECT_REF")
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if not mgmt_token or not project_ref:
        print("\n[pg_cron監視] SUPABASE_MANAGEMENT_API_TOKEN / SUPABASE_PROJECT_REF 未設定 → スキップ")
        return

    try:
        resp = client.rpc("check_pg_cron_health", {}).execute()
        data = resp.data
        if isinstance(data, list):
            health = data[0] if data else None
        elif isinstance(data, dict):
            health = data
        else:
            health = None
    except Exception as e:
        print(f"\n[pg_cron監視] ヘルスチェック失敗: {e}")
        return

    if not health:
        print("\n[pg_cron監視] ヘルスチェック結果なし → スキップ")
        return

    healthy = health.get("healthy", True)
    last_success = health.get("last_success")
    hours_since = health.get("hours_since_last_success")

    print(f"\n[pg_cron監視] healthy={healthy}  last_success={last_success}  "
          f"hours_since={hours_since}  failed_24h={health.get('failed_recent_24h', 0)}")

    if healthy:
        return

    if last_success is None and health.get("failed_recent_24h", 0) == 0:
        print("[pg_cron監視] まだ1度も発火していない（初期状態）→ リブート不要、スキップ")
        return

    print("[pg_cron監視] pg_cron が停止している可能性あり → 自動 Fast Reboot を実行")

    reboot_url = f"https://api.supabase.com/v1/projects/{project_ref}/restart"
    req = urllib.request.Request(
        reboot_url,
        method="POST",
        headers={
            "Authorization": f"Bearer {mgmt_token}",
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
        print(f"[pg_cron監視] Fast Reboot 要求完了 (HTTP {status})")
        reboot_success = True
    except urllib.error.HTTPError as e:
        print(f"[pg_cron監視] Fast Reboot 失敗: HTTP {e.code} {e.read().decode()}")
        reboot_success = False
    except Exception as e:
        print(f"[pg_cron監視] Fast Reboot 失敗: {e}")
        reboot_success = False

    if discord_url:
        status_text = "自動リブート実行済み" if reboot_success else "自動リブート失敗"
        msg = (
            f"**[pg_cron 死活監視]**\n"
            f"pg_cron の最終成功: {last_success or '記録なし'}\n"
            f"経過時間: {hours_since}時間\n"
            f"→ {status_text}"
        )
        discord_req = urllib.request.Request(
            discord_url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"content": msg}).encode(),
        )
        try:
            with urllib.request.urlopen(discord_req, timeout=10):
                pass
            print("[pg_cron監視] Discord 通知送信完了")
        except Exception as e:
            print(f"[pg_cron監視] Discord 通知失敗: {e}")


try:
    check_pg_cron_and_reboot()
except Exception as e:
    print(f"\n[pg_cron監視] 予期しないエラー: {e}")
