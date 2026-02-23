"""
監視時刻生成ロジック（決定論的）

generate_watches() は event_occurrence + event_master の情報から
Monitor が参照する watch_schedule レコードを生成する。
"""

from datetime import datetime, timedelta, timezone, date, time

from jpx_calendar import next_trading_day

JST = timezone(timedelta(hours=9))
UTC = timezone.utc

TIMEZONE_MAP = {
    "America/New_York": {
        "standard_offset": timedelta(hours=-5),
        "dst_offset": timedelta(hours=-4),
    },
    "Asia/Tokyo": {
        "standard_offset": timedelta(hours=9),
        "dst_offset": timedelta(hours=9),
    },
    "Europe/Frankfurt": {
        "standard_offset": timedelta(hours=1),
        "dst_offset": timedelta(hours=2),
    },
    "Europe/Luxembourg": {
        "standard_offset": timedelta(hours=1),
        "dst_offset": timedelta(hours=2),
    },
}


def _is_us_dst(d: date) -> bool:
    """米国のサマータイム判定（3月第2日曜〜11月第1日曜）"""
    year = d.year
    march_second_sunday = date(year, 3, 8)
    while march_second_sunday.weekday() != 6:
        march_second_sunday += timedelta(days=1)
    nov_first_sunday = date(year, 11, 1)
    while nov_first_sunday.weekday() != 6:
        nov_first_sunday += timedelta(days=1)
    return march_second_sunday <= d < nov_first_sunday


def _is_eu_dst(d: date) -> bool:
    """EU のサマータイム判定（3月最終日曜〜10月最終日曜）"""
    year = d.year
    march_last_sunday = date(year, 3, 31)
    while march_last_sunday.weekday() != 6:
        march_last_sunday -= timedelta(days=1)
    oct_last_sunday = date(year, 10, 31)
    while oct_last_sunday.weekday() != 6:
        oct_last_sunday -= timedelta(days=1)
    return march_last_sunday <= d < oct_last_sunday


def _get_utc_offset(tz_name: str, d: date) -> timedelta:
    tz_info = TIMEZONE_MAP.get(tz_name)
    if not tz_info:
        return timedelta(0)

    if tz_name == "America/New_York":
        return tz_info["dst_offset"] if _is_us_dst(d) else tz_info["standard_offset"]
    elif tz_name in ("Europe/Frankfurt", "Europe/Luxembourg"):
        return tz_info["dst_offset"] if _is_eu_dst(d) else tz_info["standard_offset"]
    else:
        return tz_info["standard_offset"]


def _local_to_utc(d: date, hour: int, minute: int, tz_name: str) -> datetime:
    offset = _get_utc_offset(tz_name, d)
    local_tz = timezone(offset)
    local_dt = datetime.combine(d, time(hour, minute), tzinfo=local_tz)
    return local_dt.astimezone(UTC)


def _utc_to_jst(dt_utc: datetime) -> datetime:
    return dt_utc.astimezone(JST)


def _make_watch(
    occurrence_id: int,
    market: str,
    watch_at_utc: datetime,
    watch_kind: str,
    run_id: int | None = None,
) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "market": market,
        "watch_at_utc": watch_at_utc.isoformat(),
        "watch_at_jst": _utc_to_jst(watch_at_utc).isoformat(),
        "watch_kind": watch_kind,
        "consumed": False,
        "created_by_run_id": run_id,
    }


def generate_watches(
    occurrence: dict, event: dict, run_id: int | None = None
) -> list[dict]:
    """
    1つの event_occurrence + event_master から watch_schedule レコード群を生成する。

    Args:
        occurrence: event_occurrence テーブルの行（occurrence_id, scheduled_date_local 等）
        event: event_master テーブルの行（event_id, release_time_rule, category 等）
        run_id: ingest_run の run_id

    Returns:
        watch_schedule に upsert するための dict のリスト
    """
    watches: list[dict] = []
    oid = occurrence["occurrence_id"]
    event_id = event["event_id"]
    category = event.get("category", "statistics")
    region = event.get("region", "US")
    rule = event.get("release_time_rule") or {}
    tz_name = rule.get("tz", "America/New_York")

    scheduled_date = occurrence["scheduled_date_local"]
    if isinstance(scheduled_date, str):
        scheduled_date = date.fromisoformat(scheduled_date)

    release_utc = occurrence.get("scheduled_at_utc")
    if release_utc:
        if isinstance(release_utc, str):
            release_utc = datetime.fromisoformat(release_utc)
    elif rule.get("hour") is not None:
        release_utc = _local_to_utc(
            scheduled_date, rule["hour"], rule.get("minute", 0), tz_name
        )

    market = region if region in ("JP", "US", "EU") else "US"

    if release_utc:
        watches.append(
            _make_watch(oid, market, release_utc + timedelta(minutes=5), "post_release_5m", run_id)
        )
        watches.append(
            _make_watch(oid, market, release_utc + timedelta(minutes=20), "post_release_20m", run_id)
        )

    if category == "central_bank" and event.get("has_press_conference"):
        press_utc = occurrence.get("press_start_utc")
        if press_utc:
            if isinstance(press_utc, str):
                press_utc = datetime.fromisoformat(press_utc)
            watches.append(
                _make_watch(oid, market, press_utc + timedelta(minutes=10), "post_press_10m", run_id)
            )
        elif release_utc:
            estimated_press = release_utc + timedelta(minutes=30)
            watches.append(
                _make_watch(oid, market, estimated_press + timedelta(minutes=10), "post_press_10m", run_id)
            )

    if event.get("jp_follow_required"):
        next_tse = next_trading_day(scheduled_date)
        jp_follow_utc = _local_to_utc(next_tse, 9, 10, "Asia/Tokyo")
        watches.append(
            _make_watch(oid, "JP", jp_follow_utc, "jp_follow_tse_open", run_id)
        )

    if event_id == "JP_BOJ":
        boj_midday_utc = _local_to_utc(scheduled_date, 12, 30, "Asia/Tokyo")
        watches.append(
            _make_watch(oid, "JP", boj_midday_utc, "boj_midday", run_id)
        )
        boj_afternoon_utc = _local_to_utc(scheduled_date, 15, 45, "Asia/Tokyo")
        watches.append(
            _make_watch(oid, "JP", boj_afternoon_utc, "boj_afternoon", run_id)
        )

    return watches
