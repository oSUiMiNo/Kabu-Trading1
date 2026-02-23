"""
EventScheduler オーケストレーター

経済イベントの日程を取得し、Monitor 用の watch_schedule を生成する。

Usage:
    python scheduler_orchestrator.py seed                    # マスターデータのみ upsert
    python scheduler_orchestrator.py annual                  # 年次: 1年分取得
    python scheduler_orchestrator.py monthly                 # 月次: 当月+翌月
    python scheduler_orchestrator.py monthly --months 3      # 月次: 3ヶ月分
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    upsert_event_master,
    list_event_masters,
    upsert_event_occurrence,
    upsert_watch_schedule,
    create_ingest_run,
    update_ingest_run,
)

from AgentUtil import call_agent, load_debug_config
from event_master_seed import EVENT_MASTERS
from watch_time_rules import generate_watches

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"

JST = timezone(timedelta(hours=9))


def seed_event_master() -> int:
    count = 0
    for event in EVENT_MASTERS:
        row = {**event}
        if isinstance(row.get("release_time_rule"), dict):
            row["release_time_rule"] = json.dumps(row["release_time_rule"])
        result = safe_db(upsert_event_master, row)
        if result:
            count += 1
            print(f"  [SEED] {event['event_id']}: {event['name_ja']}")
    return count


def build_fetch_prompt(event: dict, target_year: int, target_months: list[int]) -> str:
    months_str = ", ".join(str(m) for m in target_months)
    return (
        f"以下の経済イベントの日程を公式ソースから取得してください。\n"
        f"\n"
        f"event_id: {event['event_id']}\n"
        f"name: {event['name']}\n"
        f"name_ja: {event['name_ja']}\n"
        f"source_url: {event.get('source_url', 'N/A')}\n"
        f"region: {event['region']}\n"
        f"target_year: {target_year}\n"
        f"target_months: [{months_str}]\n"
    )


def parse_calendar_result(agent_output: str) -> dict | None:
    blocks = re.findall(r"```yaml\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        cal = data.get("calendar_result", data)
        if isinstance(cal, dict) and "dates" in cal:
            return cal
    return None


async def fetch_and_store_one(
    event: dict,
    target_year: int,
    target_months: list[int],
    run_id: int | None = None,
) -> dict:
    event_id = event["event_id"]
    result_info = {"event_id": event_id, "success": False, "dates_count": 0, "watches_count": 0}

    prompt = build_fetch_prompt(event, target_year, target_months)
    agent_file = AGENTS_DIR / "calendar-fetcher.md"
    dbg = load_debug_config("scheduler")

    result = await call_agent(prompt, file_path=str(agent_file), **dbg)

    cost = result.cost if result else None
    if cost:
        print(f"  [{event_id}] コスト: ${cost:.4f}")

    if not result or not result.text:
        print(f"  [{event_id}] 警告: エージェント応答なし")
        return result_info

    cal = parse_calendar_result(result.text)
    if not cal:
        print(f"  [{event_id}] 警告: 結果パース失敗")
        return result_info

    if not cal.get("source_verified", False):
        err = cal.get("error", "不明")
        print(f"  [{event_id}] 警告: ソース未確認 - {err}")
        return result_info

    dates = cal.get("dates", [])
    press_confs = {p["date"]: p for p in cal.get("press_conferences", [])}

    release_rule = event.get("release_time_rule") or {}
    if isinstance(release_rule, str):
        release_rule = json.loads(release_rule)

    tz_name = release_rule.get("tz", "America/New_York")

    for entry in dates:
        d_str = entry.get("date")
        if not d_str:
            continue

        d = date.fromisoformat(d_str)
        if d.month not in target_months:
            continue

        occ_data = {
            "event_id": event_id,
            "scheduled_date_local": d_str,
            "timezone": tz_name,
            "status": "scheduled",
            "source_last_checked_at": datetime.now(timezone.utc).isoformat(),
        }

        if release_rule.get("hour") is not None:
            from watch_time_rules import _local_to_utc
            release_utc = _local_to_utc(d, release_rule["hour"], release_rule.get("minute", 0), tz_name)
            occ_data["scheduled_at_utc"] = release_utc.isoformat()

        if d_str in press_confs:
            pc = press_confs[d_str]
            time_local = pc.get("time_local", "")
            if time_local:
                parts = time_local.split(":")
                if len(parts) == 2:
                    from watch_time_rules import _local_to_utc
                    press_utc = _local_to_utc(d, int(parts[0]), int(parts[1]), tz_name)
                    occ_data["press_start_utc"] = press_utc.isoformat()

        occ_result = safe_db(upsert_event_occurrence, occ_data)
        if not occ_result or "occurrence_id" not in occ_result:
            print(f"  [{event_id}] 警告: occurrence upsert 失敗 ({d_str})")
            continue

        result_info["dates_count"] += 1

        occ_for_watch = {**occ_data, "occurrence_id": occ_result["occurrence_id"]}
        watches = generate_watches(occ_for_watch, event, run_id)
        for w in watches:
            safe_db(upsert_watch_schedule, w)
            result_info["watches_count"] += 1

    result_info["success"] = True
    print(f"  [{event_id}] 完了: {result_info['dates_count']} 日程, {result_info['watches_count']} 監視時刻")
    return result_info


async def run_scheduler(run_type: str, months_ahead: int = 2) -> None:
    now = datetime.now(JST)
    target_year = now.year

    print(f"{'='*60}")
    print(f"=== EventScheduler ({run_type}) ===")
    print(f"=== {now.strftime('%Y-%m-%d %H:%M %Z')} ===")
    print(f"{'='*60}")

    print("\n[1/4] マスターデータ upsert...")
    seed_count = seed_event_master()
    print(f"  → {seed_count} イベント登録\n")

    if run_type == "annual":
        target_months = list(range(1, 13))
    else:
        current_month = now.month
        target_months = [(current_month + i - 1) % 12 + 1 for i in range(months_ahead)]

    print(f"[2/4] 対象: {target_year}年 {target_months} 月\n")

    ingest = safe_db(create_ingest_run, run_type)
    run_id = ingest.get("run_id") if ingest else None

    events = EVENT_MASTERS
    success_count = 0
    fail_count = 0
    errors: list[str] = []

    print(f"[3/4] 日程取得 ({len(events)} イベント)...\n")
    for event in events:
        print(f"  --- {event['event_id']} ({event['name_ja']}) ---")
        try:
            info = await fetch_and_store_one(event, target_year, target_months, run_id)
            if info["success"]:
                success_count += 1
            else:
                fail_count += 1
                errors.append(f"{event['event_id']}: 取得失敗")
        except Exception as e:
            fail_count += 1
            errors.append(f"{event['event_id']}: {e}")
            print(f"  [{event['event_id']}] エラー: {e}")
        print()

    if run_id:
        safe_db(
            update_ingest_run,
            run_id,
            finished_at=datetime.now(timezone.utc).isoformat(),
            events_processed=len(events),
            success_count=success_count,
            fail_count=fail_count,
            error_summary="\n".join(errors) if errors else None,
        )

    print(f"{'='*60}")
    print(f"[4/4] 完了")
    print(f"  成功: {success_count}, 失敗: {fail_count}")
    if errors:
        print(f"  エラー:")
        for e in errors:
            print(f"    - {e}")
    print(f"{'='*60}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python scheduler_orchestrator.py <seed|annual|monthly> [--months N]")
        sys.exit(1)

    command = args[0]
    months_ahead = 2

    i = 1
    while i < len(args):
        if args[i] == "--months" and i + 1 < len(args):
            months_ahead = int(args[i + 1])
            i += 2
        else:
            i += 1

    if command == "seed":
        count = seed_event_master()
        print(f"\n完了: {count} イベント upsert")
    elif command in ("annual", "monthly"):
        anyio.run(lambda: run_scheduler(command, months_ahead))
    else:
        print(f"不明なコマンド: {command}")
        sys.exit(1)
