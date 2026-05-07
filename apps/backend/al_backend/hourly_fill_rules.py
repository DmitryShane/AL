from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MICROSECONDS_PER_SECOND = 1_000_000
FILL_KINDS = ("active", "overtime", "afk", "meeting", "idle", "missed")
INTERNAL_OVERTIME_FILL_SECONDS = "_visualOvertimeSeconds"
INTERNAL_MISSED_START_SECONDS = "_visualMissedStartSeconds"
INTERNAL_MISSED_END_SECONDS = "_visualMissedEndSeconds"


def seconds_from_microseconds(value: Any) -> int:
    return max(0, int(round((int(value or 0)) / MICROSECONDS_PER_SECOND)))


def time_microseconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    if microseconds_key in item:
        return max(0, int(item.get(microseconds_key) or 0))

    return max(0, int(item.get(seconds_key) or 0)) * MICROSECONDS_PER_SECOND


def time_seconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    return seconds_from_microseconds(time_microseconds(item, seconds_key, microseconds_key))


def empty_hourly_activity() -> list[dict[str, Any]]:
    return [
        {
            "hour": hour,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "breakSeconds": 0,
            "meetingSeconds": 0,
            "overtimeActiveSeconds": 0,
            INTERNAL_OVERTIME_FILL_SECONDS: 0,
            "missedSeconds": 0,
            INTERNAL_MISSED_START_SECONDS: 0,
            INTERNAL_MISSED_END_SECONDS: 0,
            "telegramToFirstActivityIdleSeconds": 0,
            "fillSegments": [],
        }
        for hour in range(24)
    ]


def public_hourly_activity(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_hour(item) for item in source]


def public_hour(item: dict[str, Any]) -> dict[str, Any]:
    hour = int(item.get("hour", 0))
    fill_segments = normalized_fill_segments(item)
    visible_totals = _totals_from_segments(fill_segments)
    totals = {
        "activeSeconds": visible_totals["active"],
        "overtimeSeconds": visible_totals["overtime"],
        "afkSeconds": visible_totals["afk"],
        "meetingSeconds": visible_totals["meeting"],
        "idleSeconds": visible_totals["idle"],
        "missedSeconds": visible_totals["missed"],
    }
    return {"hour": hour, "totals": totals, "fillSegments": fill_segments}


def normalized_fill_segments(hour: dict[str, Any]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    cursor = 0
    cursor = _append_stacked_seconds(generated, "missed", int(hour.get(INTERNAL_MISSED_START_SECONDS, 0)), cursor)
    missed_end_seconds = int(hour.get(INTERNAL_MISSED_END_SECONDS, 0))
    _append_stacked_seconds(generated, "missed", missed_end_seconds, 3600 - missed_end_seconds)
    afk_positioned = bool(_segments_for_kind(hour, "afk"))
    afk_segments = _positioned_or_stacked_segments(hour, "afk", "breakSeconds", cursor)
    generated.extend(afk_segments)
    if afk_segments and not afk_positioned:
        cursor = max(cursor, max(segment["endSecond"] for segment in afk_segments))
    meeting_positioned = bool(_segments_for_kind(hour, "meeting"))
    meeting_segments = _positioned_or_stacked_segments(hour, "meeting", "meetingSeconds", cursor)
    generated.extend(meeting_segments)
    if meeting_segments and not meeting_positioned:
        cursor = max(cursor, max(segment["endSecond"] for segment in meeting_segments))
    active_segments = _segments_for_kind(hour, "active")
    if active_segments:
        generated.extend(active_segments)
    else:
        _append_available_seconds(generated, "active", time_seconds(hour, "activeSeconds", "activeMicroseconds"))
    overtime_segments = _segments_for_kind(hour, "overtime")
    if overtime_segments:
        generated.extend(overtime_segments)
    else:
        _append_available_seconds(generated, "overtime", time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds"))
    _append_available_seconds(generated, "overtime", int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)))
    _append_available_seconds(generated, "idle", time_seconds(hour, "idleSeconds", "idleMicroseconds"))
    return normalize_hour_fill(generated)


def normalize_hour_fill(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = _sanitize_fill_segments(segments)

    if not sanitized:
        return []

    priority = {kind: index for index, kind in enumerate(("idle", "active", "overtime", "afk", "meeting", "missed"))}
    timeline: list[str | None] = [None] * 3600

    for segment in sanitized:
        kind = segment["kind"]
        start_second = int(segment["startSecond"])
        end_second = int(segment["endSecond"])

        for second in range(start_second, end_second):
            current_kind = timeline[second]
            if current_kind is None or priority[kind] >= priority[current_kind]:
                timeline[second] = kind

    normalized = []
    cursor = 0

    while cursor < 3600:
        kind = timeline[cursor]

        if kind is None:
            cursor += 1
            continue

        end_second = cursor + 1
        while end_second < 3600 and timeline[end_second] == kind:
            end_second += 1

        normalized.append({"kind": kind, "startSecond": cursor, "endSecond": end_second})
        cursor = end_second

    return normalized


def merge_hourly_activity(target: list[dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    target_by_hour = {int(item.get("hour", 0)): item for item in target}

    for delta_item in deltas:
        hour = int(delta_item.get("hour", 0))
        target_item = target_by_hour.get(hour)

        if not target_item:
            continue

        active_microseconds = time_microseconds(target_item, "activeSeconds", "activeMicroseconds") + time_microseconds(
            delta_item, "activeSeconds", "activeMicroseconds"
        )
        idle_microseconds = time_microseconds(target_item, "idleSeconds", "idleMicroseconds") + time_microseconds(
            delta_item, "idleSeconds", "idleMicroseconds"
        )
        overtime_microseconds = time_microseconds(
            target_item, "overtimeActiveSeconds", "overtimeActiveMicroseconds"
        ) + time_microseconds(delta_item, "overtimeActiveSeconds", "overtimeActiveMicroseconds")
        target_item["activeMicroseconds"] = active_microseconds
        target_item["idleMicroseconds"] = idle_microseconds
        target_item["overtimeActiveMicroseconds"] = overtime_microseconds
        target_item["activeSeconds"] = seconds_from_microseconds(active_microseconds)
        target_item["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
        target_item["breakSeconds"] = int(target_item.get("breakSeconds", 0)) + int(delta_item.get("breakSeconds", 0))
        target_item["meetingSeconds"] = int(target_item.get("meetingSeconds", 0)) + int(delta_item.get("meetingSeconds", 0))
        target_item["overtimeActiveSeconds"] = seconds_from_microseconds(overtime_microseconds)
        target_item["telegramToFirstActivityIdleSeconds"] = int(target_item.get("telegramToFirstActivityIdleSeconds", 0)) + int(
            delta_item.get("telegramToFirstActivityIdleSeconds", 0)
        )
        target_item.setdefault("fillSegments", []).extend(_sanitize_fill_segments(delta_item.get("fillSegments", [])))


def add_afk_fill_segment_to_hour(hour: dict[str, Any], start_second: int, end_second: int) -> None:
    _add_fill_segment(hour, "afk", start_second, end_second)


def add_idle_seconds_to_hour(hour: dict[str, Any], seconds: int) -> None:
    if seconds <= 0:
        return

    idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds") + (seconds * MICROSECONDS_PER_SECOND)
    hour["idleMicroseconds"] = idle_microseconds
    hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
    _append_available_seconds(hour.setdefault("fillSegments", []), "idle", seconds)


def normalize_fill_segments_in_hour(hour: dict[str, Any]) -> None:
    afk_segments = _segments_for_kind(hour, "afk")
    hour["fillSegments"] = [segment for segment in _sanitize_fill_segments(hour.get("fillSegments", [])) if segment["kind"] != "afk"]
    hour["fillSegments"].extend(afk_segments)


def add_interval_to_hourly(target: list[dict[str, Any]], start: dt.datetime, end: dt.datetime, bucket: str) -> None:
    kind = "overtime" if bucket == "overtime" else bucket
    seconds_key = "overtimeActiveSeconds" if bucket == "overtime" else f"{bucket}Seconds"
    microseconds_key = "overtimeActiveMicroseconds" if bucket == "overtime" else f"{bucket}Microseconds"
    target_by_hour = {int(item.get("hour", 0)): item for item in target}
    cursor = start

    while cursor < end:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, end)
        microseconds = max(0, int((segment_end - cursor).total_seconds() * MICROSECONDS_PER_SECOND))
        item = target_by_hour.get(cursor.hour)

        if item:
            total_microseconds = time_microseconds(item, seconds_key, microseconds_key) + microseconds
            item[microseconds_key] = total_microseconds
            item[seconds_key] = seconds_from_microseconds(total_microseconds)
            _add_fill_segment(item, kind, _second_of_hour(cursor), _second_of_hour(segment_end, hour_end))

        cursor = segment_end


def add_report_activity_fill_segments(hourly_activity: list[dict[str, Any]], reports: list[dict[str, Any]], time_zone_id: str) -> None:
    for report in reports:
        recorded_at = _coerce_datetime(report.get("recordedAt"))

        if not recorded_at:
            continue

        active_seconds = max(0, int(report.get("activeDeltaSeconds", 0) or 0))

        if active_seconds > 0:
            _add_report_fill_segment(hourly_activity, recorded_at, active_seconds, time_zone_id, "active")


def apply_breaks_to_hourly_activity(
    source: list[dict[str, Any]],
    break_buckets: list[dict[str, Any]],
    consumed_buckets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    source_by_hour = {int(item.get("hour", 0)): item for item in source}
    breaks_by_hour = {int(item.get("hour", 0)): item for item in break_buckets}
    consumed_by_hour = {int(item.get("hour", 0)): item for item in consumed_buckets or []}
    hourly_activity = []

    for hour in range(24):
        source_hour = source_by_hour.get(hour, {})
        break_hour = breaks_by_hour.get(hour, {})
        consumed_hour = consumed_by_hour.get(hour, {})
        active_seconds = min(3600, time_seconds(source_hour, "activeSeconds", "activeMicroseconds"))
        overtime_seconds = min(3600, time_seconds(source_hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds"))
        raw_idle_seconds = time_seconds(source_hour, "idleSeconds", "idleMicroseconds")
        requested_break_seconds = max(0, int(break_hour.get("breakSeconds", 0)))
        consumed_break_seconds = max(0, int(consumed_hour.get("breakSeconds", 0)))
        available_break_seconds = max(0, requested_break_seconds - consumed_break_seconds)
        break_seconds = min(available_break_seconds, max(0, 3600 - active_seconds - overtime_seconds))
        idle_seconds = min(max(0, raw_idle_seconds - break_seconds), max(0, 3600 - active_seconds - overtime_seconds - break_seconds))
        break_segments = segments_after_consumed_seconds(
            _segments_for_kind(break_hour, "afk"),
            consumed_break_seconds,
            break_seconds,
        )

        if consumed_buckets is not None:
            consumed_hour["breakSeconds"] = consumed_break_seconds + break_seconds

        result = {
            "hour": hour,
            "activeSeconds": active_seconds,
            "idleSeconds": idle_seconds,
            "breakSeconds": break_seconds,
            "meetingSeconds": int(source_hour.get("meetingSeconds", 0)),
            "overtimeActiveSeconds": overtime_seconds,
            INTERNAL_OVERTIME_FILL_SECONDS: int(source_hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)),
            "missedSeconds": int(source_hour.get("missedSeconds", 0)),
            INTERNAL_MISSED_START_SECONDS: int(source_hour.get(INTERNAL_MISSED_START_SECONDS, 0)),
            INTERNAL_MISSED_END_SECONDS: int(source_hour.get(INTERNAL_MISSED_END_SECONDS, 0)),
            "telegramToFirstActivityIdleSeconds": int(source_hour.get("telegramToFirstActivityIdleSeconds", 0)),
            "fillSegments": _segments_for_kind(source_hour, "active")
            + _segments_for_kind(source_hour, "overtime")
            + break_segments
            + _segments_for_kind(source_hour, "meeting")
            + _segments_for_kind(source_hour, "idle")
            + _segments_for_kind(source_hour, "missed"),
        }
        hourly_activity.append(result)

    return hourly_activity


def apply_meetings_to_hourly_activity(
    source: list[dict[str, Any]],
    meeting_buckets: list[dict[str, Any]],
    consumed_buckets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_by_hour = {int(item.get("hour", 0)): item for item in source}
    meeting_by_hour = {int(item.get("hour", 0)): item for item in meeting_buckets}
    consumed_by_hour = {int(item.get("hour", 0)): item for item in consumed_buckets}
    hourly_activity = []

    for hour in range(24):
        source_hour = source_by_hour.get(hour, {})
        consumed_hour = consumed_by_hour.get(hour, {})
        break_seconds = int(source_hour.get("breakSeconds", 0))
        requested_meeting_seconds = max(0, int((meeting_by_hour.get(hour, {}) or {}).get("meetingSeconds", 0)))
        consumed_meeting_seconds = max(0, int(consumed_hour.get("meetingSeconds", 0)))
        available_meeting_seconds = max(0, requested_meeting_seconds - consumed_meeting_seconds)
        meeting_seconds = min(available_meeting_seconds, max(0, 3600 - break_seconds))
        meeting_overlap_seconds = meeting_seconds
        raw_idle_seconds = max(0, int(source_hour.get("idleSeconds", 0)))
        idle_seconds = max(0, raw_idle_seconds - meeting_overlap_seconds)
        raw_active_seconds = max(0, int(source_hour.get("activeSeconds", 0)))
        raw_overtime_seconds = max(0, int(source_hour.get("overtimeActiveSeconds", 0)))
        consumed_hour["meetingSeconds"] = consumed_meeting_seconds + meeting_seconds
        meeting_segments = segments_after_consumed_seconds(
            _segments_for_kind((meeting_by_hour.get(hour, {}) or {}), "meeting"),
            consumed_meeting_seconds,
            meeting_seconds,
        )

        hourly_activity.append(
            {
                "hour": hour,
                "activeSeconds": raw_active_seconds,
                "idleSeconds": idle_seconds,
                "breakSeconds": break_seconds,
                "meetingSeconds": meeting_seconds,
                "overtimeActiveSeconds": raw_overtime_seconds,
                INTERNAL_OVERTIME_FILL_SECONDS: int(source_hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)),
                "missedSeconds": int(source_hour.get("missedSeconds", 0)),
                INTERNAL_MISSED_START_SECONDS: int(source_hour.get(INTERNAL_MISSED_START_SECONDS, 0)),
                INTERNAL_MISSED_END_SECONDS: int(source_hour.get(INTERNAL_MISSED_END_SECONDS, 0)),
                "telegramToFirstActivityIdleSeconds": int(source_hour.get("telegramToFirstActivityIdleSeconds", 0)),
                "fillSegments": _segments_for_kind(source_hour, "active")
                + _segments_for_kind(source_hour, "overtime")
                + _segments_for_kind(source_hour, "afk")
                + meeting_segments
                + _segments_for_kind(source_hour, "idle")
                + _segments_for_kind(source_hour, "missed"),
            }
        )

    return hourly_activity


def merge_meeting_buckets_into_hourly_author_rows(
    hourly_by_author: dict[str, dict[str, Any]],
    meeting_buckets: dict[tuple[str, str], list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    display_name,
) -> dict[str, dict[str, int]]:
    adjustments_by_author: dict[str, dict[str, int]] = {}

    for (raw_author, _date), meeting_hours in meeting_buckets.items():
        author_row = hourly_by_author.get(raw_author)

        if not author_row:
            profile = profiles.get(raw_author, {})
            author_row = {
                "author": display_name(raw_author, profile),
                "rawAuthor": raw_author,
                "timeZoneId": profile.get("timeZoneId"),
                "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                "hourlyActivity": empty_hourly_activity(),
            }
            hourly_by_author[raw_author] = author_row

        current_by_hour = {int(item.get("hour", 0)): item for item in author_row.get("hourlyActivity", [])}

        for meeting_hour in meeting_hours:
            hour = int(meeting_hour.get("hour", 0))
            target_hour = current_by_hour.get(hour)

            if not target_hour:
                continue

            meeting_seconds = int(meeting_hour.get("meetingSeconds", 0))
            current_meeting_seconds = int(target_hour.get("meetingSeconds", 0))
            added_meeting_seconds = max(0, min(meeting_seconds - current_meeting_seconds, 3600 - current_meeting_seconds))

            if added_meeting_seconds <= 0:
                continue

            target_hour["meetingSeconds"] = current_meeting_seconds + added_meeting_seconds
            target_hour.setdefault("fillSegments", []).extend(
                segments_after_consumed_seconds(_segments_for_kind(meeting_hour, "meeting"), current_meeting_seconds, added_meeting_seconds)
            )
            adjustment = adjustments_by_author.setdefault(raw_author, {"idleSeconds": 0, "meetingSeconds": 0})
            adjustment["meetingSeconds"] += added_meeting_seconds

    return adjustments_by_author


def add_break_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, Any]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    _add_interval_to_buckets(buckets, raw_author, started_at, ended_at, time_zone_id, "afk", "breakSeconds")


def add_meeting_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, Any]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    _add_interval_to_buckets(buckets, raw_author, started_at, ended_at, time_zone_id, "meeting", "meetingSeconds")


def add_idle_interval_to_buckets(
    buckets: list[dict[str, Any]],
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    if not started_at or not ended_at or ended_at <= started_at:
        return

    for hour, start_second, end_second, seconds in split_interval_by_hour(started_at, ended_at, time_zone_id):
        if 0 <= hour < len(buckets):
            buckets[hour]["idleSeconds"] = int(buckets[hour].get("idleSeconds", 0)) + seconds
            _add_fill_segment(buckets[hour], "idle", start_second, end_second)


def add_visual_missed_seconds(hourly_activity: list[dict[str, Any]], hour: int, seconds: int, segment_key: str) -> None:
    if seconds <= 0 or hour < 0 or hour >= len(hourly_activity):
        return

    hourly_activity[hour]["missedSeconds"] = int(hourly_activity[hour].get("missedSeconds", 0)) + seconds
    hourly_activity[hour][segment_key] = int(hourly_activity[hour].get(segment_key, 0)) + seconds

    if segment_key == INTERNAL_MISSED_START_SECONDS:
        _add_fill_segment(hourly_activity[hour], "missed", 0, seconds)
    else:
        _add_fill_segment(hourly_activity[hour], "missed", 3600 - seconds, 3600)


def visual_missed_end_hour(hourly_activity: list[dict[str, Any]], start_hour: int, local_offline_at: dt.datetime | None) -> dict[str, Any]:
    if not hourly_activity:
        return {}

    clamped_start = min(max(0, start_hour), len(hourly_activity) - 1)

    if visual_hour_available_seconds(hourly_activity[clamped_start]) > 0:
        return hourly_activity[clamped_start]

    end_hour = local_offline_at.hour if local_offline_at else clamped_start

    for hour_index in range(clamped_start + 1, min(end_hour, len(hourly_activity) - 1) + 1):
        hour = hourly_activity[hour_index]

        if visual_hour_occupied_seconds(hour) > 0 and visual_hour_available_seconds(hour) > 0:
            return hour

    return hourly_activity[clamped_start]


def visual_hour_occupied_seconds(hour: dict[str, Any]) -> int:
    public = public_hour(hour)
    totals = public["totals"]
    return sum(int(totals[key]) for key in totals if key != "missedSeconds")


def visual_hour_available_seconds(hour: dict[str, Any]) -> int:
    return max(0, 3600 - visual_hour_occupied_seconds(hour))


def remove_visual_missed_seconds(hour: dict[str, Any], seconds: int) -> None:
    remaining_seconds = max(0, seconds)

    for key in (INTERNAL_MISSED_END_SECONDS, INTERNAL_MISSED_START_SECONDS):
        if remaining_seconds <= 0:
            return

        current_seconds = int(hour.get(key, 0))
        removed_seconds = min(current_seconds, remaining_seconds)

        if removed_seconds <= 0:
            continue

        hour[key] = current_seconds - removed_seconds
        hour["missedSeconds"] = max(0, int(hour.get("missedSeconds", 0)) - removed_seconds)
        remaining_seconds -= removed_seconds


def fill_overtime_hours_bracketed_by_reports(hourly_activity: list[dict[str, Any]], reports: list[dt.datetime]) -> None:
    if len(reports) < 2:
        return

    earliest_report = reports[0]
    latest_report = reports[-1]

    for hour in hourly_activity:
        hour_index = int(hour.get("hour", 0))
        hour_start = earliest_report.replace(hour=hour_index, minute=0, second=0, microsecond=0)
        hour_end = hour_start + dt.timedelta(hours=1)

        if earliest_report >= hour_start or latest_report <= hour_end:
            continue

        fill_visual_overtime_hour(hour)


def fill_overtime_hours_between_overtime_buckets(hourly_activity: list[dict[str, Any]]) -> None:
    overtime_hour_indexes = [
        index
        for index, hour in enumerate(hourly_activity)
        if int(hour.get("overtimeActiveSeconds", 0)) > 0
        or time_microseconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") > 0
    ]

    if len(overtime_hour_indexes) < 2:
        return

    for hour_index in range(overtime_hour_indexes[0] + 1, overtime_hour_indexes[-1]):
        fill_visual_overtime_hour(hourly_activity[hour_index], replace_visual_idle=True)


def fill_normal_to_overtime_transition_hours(hourly_activity: list[dict[str, Any]]) -> None:
    for hour in hourly_activity:
        if int(hour.get("activeSeconds", 0)) <= 0:
            continue

        if int(hour.get("overtimeActiveSeconds", 0)) <= 0:
            continue

        fill_visual_overtime_hour(hour)


def fill_visual_overtime_hour(hour: dict[str, Any], replace_visual_idle: bool = False) -> None:
    visual_idle_seconds = max(0, int(hour.get("idleSeconds", 0))) if replace_visual_idle else 0
    overtime_seconds = visual_hour_available_seconds(hour) + visual_idle_seconds

    if overtime_seconds <= 0:
        return

    if visual_idle_seconds > 0:
        hour["idleSeconds"] = 0
        hour["idleMicroseconds"] = 0

    hour[INTERNAL_OVERTIME_FILL_SECONDS] = int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) + overtime_seconds
    _append_available_seconds(hour.setdefault("fillSegments", []), "overtime", overtime_seconds)
    remove_visual_missed_seconds(hour, overtime_seconds)


def segments_after_consumed_seconds(source: Any, consumed_seconds: int, target_seconds: int) -> list[dict[str, Any]]:
    if target_seconds <= 0:
        return []

    remaining_consumed_seconds = max(0, consumed_seconds)
    remaining_target_seconds = max(0, target_seconds)
    segments = []

    for segment in _sanitize_fill_segments(source):
        segment_seconds = int(segment["endSecond"]) - int(segment["startSecond"])

        if remaining_consumed_seconds >= segment_seconds:
            remaining_consumed_seconds -= segment_seconds
            continue

        start_second = int(segment["startSecond"]) + remaining_consumed_seconds
        selected_seconds = min(int(segment["endSecond"]) - start_second, remaining_target_seconds)

        if selected_seconds > 0:
            segments.append({"kind": segment["kind"], "startSecond": start_second, "endSecond": start_second + selected_seconds})
            remaining_target_seconds -= selected_seconds

        remaining_consumed_seconds = 0

        if remaining_target_seconds <= 0:
            break

    return segments


def split_interval_by_hour(
    started_at: dt.datetime,
    ended_at: dt.datetime,
    time_zone_id: str,
) -> list[tuple[int, int, int, int]]:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    cursor = started_at.astimezone(zone)
    local_end = ended_at.astimezone(zone)
    result = []

    while cursor < local_end:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, local_end)
        seconds = max(0, int((segment_end - cursor).total_seconds()))
        result.append((cursor.hour, _second_of_hour(cursor), _second_of_hour(segment_end, hour_end), seconds))
        cursor = segment_end

    return result


def hourly_deltas(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, int]]:
    previous_by_hour = {int(item.get("hour", 0)): item for item in previous}
    deltas = []

    for item in current:
        hour = int(item.get("hour", 0))
        previous_item = previous_by_hour.get(hour, {})
        active_delta = max(0, int(item.get("activeSeconds", 0)) - int(previous_item.get("activeSeconds", 0)))
        idle_delta = max(0, int(item.get("idleSeconds", 0)) - int(previous_item.get("idleSeconds", 0)))
        deltas.append({"hour": hour, "activeSeconds": active_delta, "idleSeconds": idle_delta})

    return deltas


def _add_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, Any]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
    kind: str,
    seconds_key: str,
) -> None:
    if not raw_author or not started_at or not ended_at or ended_at <= started_at:
        return

    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    cursor = started_at.astimezone(zone)
    local_end = ended_at.astimezone(zone)

    while cursor < local_end:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, local_end)
        date = cursor.date().isoformat()
        target = buckets.get((str(raw_author), date))

        if target:
            seconds = max(0, int((segment_end - cursor).total_seconds()))
            target[cursor.hour][seconds_key] = int(target[cursor.hour].get(seconds_key, 0)) + seconds
            _add_fill_segment(target[cursor.hour], kind, _second_of_hour(cursor), _second_of_hour(segment_end, hour_end))

        cursor = segment_end


def _add_report_fill_segment(
    hourly_activity: list[dict[str, Any]],
    recorded_at: dt.datetime,
    seconds: int,
    time_zone_id: str,
    kind: str,
) -> None:
    start = recorded_at - dt.timedelta(seconds=seconds)
    by_hour = {int(item.get("hour", 0)): item for item in hourly_activity}

    for hour, start_second, end_second, _seconds in split_interval_by_hour(start, recorded_at, time_zone_id):
        target = by_hour.get(hour)

        if target is not None:
            _add_fill_segment(target, kind, start_second, end_second)


def _coerce_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)

    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()

    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _add_fill_segment(hour: dict[str, Any], kind: str, start_second: int, end_second: int) -> None:
    if kind not in FILL_KINDS:
        return

    start_second = max(0, min(3600, int(start_second)))
    end_second = max(0, min(3600, int(end_second)))

    if end_second <= start_second:
        return

    hour.setdefault("fillSegments", []).append({"kind": kind, "startSecond": start_second, "endSecond": end_second})


def _append_stacked_seconds(segments: list[dict[str, Any]], kind: str, seconds: int, cursor: int) -> int:
    seconds = max(0, min(3600 - cursor, int(seconds)))

    if seconds <= 0:
        return cursor

    segments.append({"kind": kind, "startSecond": cursor, "endSecond": cursor + seconds})
    return cursor + seconds


def _append_available_seconds(segments: list[dict[str, Any]], kind: str, seconds: int) -> None:
    remaining_seconds = max(0, int(seconds))

    if remaining_seconds <= 0:
        return

    occupied = [False] * 3600

    for segment in normalize_hour_fill(segments):
        for second in range(segment["startSecond"], segment["endSecond"]):
            occupied[second] = True

    cursor = 0

    while cursor < 3600 and remaining_seconds > 0:
        while cursor < 3600 and occupied[cursor]:
            cursor += 1

        if cursor >= 3600:
            break

        end_second = cursor
        while end_second < 3600 and not occupied[end_second] and remaining_seconds > 0:
            end_second += 1
            remaining_seconds -= 1

        if end_second > cursor:
            segments.append({"kind": kind, "startSecond": cursor, "endSecond": end_second})

        cursor = end_second


def _positioned_or_stacked_segments(hour: dict[str, Any], kind: str, seconds_key: str, cursor: int) -> list[dict[str, Any]]:
    positioned = _segments_for_kind(hour, kind)

    if positioned:
        return positioned

    seconds = max(0, int(hour.get(seconds_key, 0)))
    if seconds <= 0:
        return []

    return [{"kind": kind, "startSecond": cursor, "endSecond": min(3600, cursor + seconds)}]


def _segments_for_kind(hour: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind not in _allowed_kinds_for_hour(hour):
        return []

    return [segment for segment in _sanitize_fill_segments(hour.get("fillSegments", [])) if segment["kind"] == kind]


def _sanitize_fill_segments(source: Any) -> list[dict[str, Any]]:
    if not isinstance(source, list):
        return []

    segments = []
    for segment in source:
        if not isinstance(segment, dict):
            continue

        kind = segment.get("kind")
        if kind not in FILL_KINDS:
            continue

        start_second = max(0, min(3600, int(segment.get("startSecond", 0))))
        end_second = max(0, min(3600, int(segment.get("endSecond", 0))))

        if end_second <= start_second:
            continue

        segments.append({"kind": kind, "startSecond": start_second, "endSecond": end_second})

    return sorted(segments, key=lambda item: (item["startSecond"], item["endSecond"], FILL_KINDS.index(item["kind"])))


def _totals_from_segments(segments: list[dict[str, Any]]) -> dict[str, int]:
    totals = {kind: 0 for kind in FILL_KINDS}

    for segment in segments:
        totals[segment["kind"]] += int(segment["endSecond"]) - int(segment["startSecond"])

    return totals


def _allowed_kinds_for_hour(hour: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()

    if time_seconds(hour, "activeSeconds", "activeMicroseconds") > 0:
        allowed.add("active")
    if time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") > 0 or int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) > 0:
        allowed.add("overtime")
    if int(hour.get("breakSeconds", 0)) > 0:
        allowed.add("afk")
    if int(hour.get("meetingSeconds", 0)) > 0:
        allowed.add("meeting")
    if time_seconds(hour, "idleSeconds", "idleMicroseconds") > 0:
        allowed.add("idle")
    if (
        int(hour.get("missedSeconds", 0)) > 0
        or int(hour.get(INTERNAL_MISSED_START_SECONDS, 0)) > 0
        or int(hour.get(INTERNAL_MISSED_END_SECONDS, 0)) > 0
    ):
        allowed.add("missed")

    return allowed


def _second_of_hour(value: dt.datetime, hour_end: dt.datetime | None = None) -> int:
    if hour_end is not None and value == hour_end:
        return 3600

    return value.minute * 60 + value.second
