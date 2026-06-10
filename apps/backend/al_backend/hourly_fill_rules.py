from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .overtime_rules import NIGHT_OVERTIME_END_HOUR, NIGHT_OVERTIME_START_HOUR

MICROSECONDS_PER_SECOND = 1_000_000
FILL_KINDS = ("active", "overtime", "overtime-fill", "afk", "auto-afk", "meeting", "telegram-idle", "idle", "missed")
BOTTOM_STACK_KINDS = ("active",)
MEETING_STACK_KINDS = ("meeting",)
AUTO_AFK_STACK_KINDS = ("auto-afk",)
MIDDLE_STACK_KINDS = ("idle",)
POST_ACTIVITY_STACK_KINDS = ("overtime",)
TOP_STACK_KINDS = ("overtime-fill",)
INTERNAL_OVERTIME_FILL_SECONDS = "_visualOvertimeSeconds"
INTERNAL_OVERTIME_START_SECOND = "_overtimeStartSecond"
INTERNAL_MISSED_START_SECONDS = "_visualMissedStartSeconds"
INTERNAL_MISSED_END_SECONDS = "_visualMissedEndSeconds"
INTERNAL_HAS_SOURCE_BREAK_OVERLAP = "_hasSourceBreakOverlap"
INTERNAL_AUTO_BREAK_START_SECOND = "_autoBreakStartSecond"
VISUAL_ACTIVE_NOISE_SECONDS = 10


def is_night_overtime_hour(hour_index: int) -> bool:
    return NIGHT_OVERTIME_START_HOUR <= int(hour_index) < NIGHT_OVERTIME_END_HOUR


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
            "autoBreakSeconds": 0,
            "meetingSeconds": 0,
            "overtimeActiveSeconds": 0,
            INTERNAL_OVERTIME_FILL_SECONDS: 0,
            INTERNAL_OVERTIME_START_SECOND: None,
            "missedSeconds": 0,
            INTERNAL_MISSED_START_SECONDS: 0,
            INTERNAL_MISSED_END_SECONDS: 0,
            INTERNAL_AUTO_BREAK_START_SECOND: None,
            "telegramToFirstActivityIdleSeconds": 0,
            "workdayHourGapIdleSeconds": 0,
            "fillSegments": [],
        }
        for hour in range(24)
    ]


def public_hourly_activity(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_hour(item) for item in source]


def public_hour(item: dict[str, Any]) -> dict[str, Any]:
    hour = int(item.get("hour", 0))
    fill_segments = _collapse_visual_active_noise(normalized_fill_segments(item))
    fill_segments = _cap_fill_segments_kind(
        fill_segments,
        "active",
        time_seconds(item, "activeSeconds", "activeMicroseconds"),
    )
    visible_totals = _totals_from_segments(fill_segments)
    totals = {
        "activeSeconds": visible_totals["active"],
        "overtimeSeconds": visible_totals["overtime"] + visible_totals["overtime-fill"],
        "afkSeconds": visible_totals["afk"] + visible_totals["auto-afk"],
        "meetingSeconds": visible_totals["meeting"],
        "idleSeconds": visible_totals["idle"] + visible_totals["telegram-idle"],
        "missedSeconds": visible_totals["missed"],
    }
    return {"hour": hour, "totals": totals, "fillSegments": fill_segments}


def _collapse_visual_active_noise(fill_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed = []

    for segment in fill_segments:
        if segment.get("kind") == "active" and int(segment.get("endSecond", 0)) - int(segment.get("startSecond", 0)) <= VISUAL_ACTIVE_NOISE_SECONDS:
            collapsed.append({**segment, "kind": "idle"})
        else:
            collapsed.append(segment)

    return normalize_hour_fill(collapsed, apply_stack_rules=False)


def _cap_fill_segments_kind(
    fill_segments: list[dict[str, Any]],
    kind: str,
    max_seconds: int,
) -> list[dict[str, Any]]:
    remaining_seconds = max(0, int(max_seconds))
    capped = []

    for segment in fill_segments:
        if segment.get("kind") != kind:
            capped.append(segment)
            continue

        start_second = int(segment.get("startSecond", 0))
        end_second = int(segment.get("endSecond", 0))
        segment_seconds = max(0, end_second - start_second)

        if segment_seconds <= 0 or remaining_seconds <= 0:
            continue

        kept_seconds = min(segment_seconds, remaining_seconds)
        capped.append({**segment, "endSecond": start_second + kept_seconds})
        remaining_seconds -= kept_seconds

    return capped


def normalized_fill_segments(hour: dict[str, Any]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    cursor = 0
    cursor = _append_stacked_seconds(generated, "missed", int(hour.get(INTERNAL_MISSED_START_SECONDS, 0)), cursor)
    missed_end_seconds = int(hour.get(INTERNAL_MISSED_END_SECONDS, 0))
    _append_stacked_seconds(generated, "missed", missed_end_seconds, 3600 - missed_end_seconds)
    afk_positioned = bool(_segments_for_kind(hour, "afk"))
    afk_segments = _segments_for_kind(hour, "afk") if afk_positioned else _stacked_segments(
        "afk",
        max(0, int(hour.get("breakSeconds", 0)) - int(hour.get("autoBreakSeconds", 0))),
        cursor,
    )
    generated.extend(afk_segments)
    if afk_segments and not afk_positioned:
        cursor = max(cursor, max(segment["endSecond"] for segment in afk_segments))
    meeting_positioned = bool(_segments_for_kind(hour, "meeting"))
    meeting_segments = _positioned_or_stacked_segments(hour, "meeting", "meetingSeconds", cursor)
    generated.extend(meeting_segments)
    if meeting_segments and not meeting_positioned:
        cursor = max(cursor, max(segment["endSecond"] for segment in meeting_segments))
    telegram_idle_segments = _telegram_idle_segments(hour)
    generated.extend(telegram_idle_segments)
    active_segments = _segments_for_kind(hour, "active")
    if active_segments:
        generated.extend(active_segments)
    else:
        _append_available_seconds(generated, "active", time_seconds(hour, "activeSeconds", "activeMicroseconds"))
    overtime_start_second = _effective_overtime_start_second(hour)
    overtime_segments = _segments_for_kind(hour, "overtime")
    if _clamp_second(overtime_start_second) is not None:
        generated.extend(_stacked_segments("overtime", time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds"), int(overtime_start_second)))
    elif overtime_segments:
        generated.extend(overtime_segments)
    else:
        _append_available_seconds(generated, "overtime", time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds"))
    _append_available_seconds(generated, "auto-afk", int(hour.get("autoBreakSeconds", 0)))
    if _clamp_second(overtime_start_second) is None:
        _append_available_seconds(generated, "overtime-fill", int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)))
    generic_idle_seconds = _visible_generic_idle_seconds(
        hour,
        _segments_total_seconds(telegram_idle_segments),
    )
    _append_available_seconds(generated, "idle", generic_idle_seconds)
    if _clamp_second(overtime_start_second) is not None:
        _append_available_seconds(generated, "overtime-fill", int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)))
    return normalize_hour_fill(
        generated,
        post_activity_start_second=overtime_start_second,
        auto_afk_start_second=hour.get(INTERNAL_AUTO_BREAK_START_SECOND),
    )


def normalize_hour_fill(
    segments: list[dict[str, Any]],
    *,
    apply_stack_rules: bool = True,
    post_activity_start_second: Any = None,
    auto_afk_start_second: Any = None,
) -> list[dict[str, Any]]:
    sanitized = _sanitize_fill_segments(segments)

    if not sanitized:
        return []

    priority = {
        kind: index
        for index, kind in enumerate(("missed", "idle", "active", "overtime-fill", "overtime", "telegram-idle", "afk", "auto-afk", "meeting"))
    }
    timeline: list[str | None] = [None] * 3600

    for segment in sanitized:
        kind = segment["kind"]
        start_second = int(segment["startSecond"])
        end_second = int(segment["endSecond"])

        for second in range(start_second, end_second):
            current_kind = timeline[second]
            if current_kind is None or priority[kind] >= priority[current_kind]:
                timeline[second] = kind

    for segment in sanitized:
        if segment["kind"] != "missed" or int(segment["startSecond"]) != 0:
            continue

        for second in range(int(segment["startSecond"]), int(segment["endSecond"])):
            timeline[second] = "missed"

    if apply_stack_rules:
        _apply_stack_layers(
            timeline,
            post_activity_start_second=post_activity_start_second,
            auto_afk_start_second=auto_afk_start_second,
        )

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


def _apply_stack_layers(
    timeline: list[str | None],
    *,
    post_activity_start_second: Any = None,
    auto_afk_start_second: Any = None,
) -> None:
    bottom_stack_seconds = _stack_seconds_by_kind(timeline, BOTTOM_STACK_KINDS)
    meeting_stack_seconds = _stack_seconds_by_kind(timeline, MEETING_STACK_KINDS)
    auto_afk_stack_seconds = _stack_seconds_by_kind(timeline, AUTO_AFK_STACK_KINDS)
    middle_stack_seconds = _stack_seconds_by_kind(timeline, MIDDLE_STACK_KINDS)
    post_activity_stack_seconds = _stack_seconds_by_kind(timeline, POST_ACTIVITY_STACK_KINDS)
    top_stack_seconds = _stack_seconds_by_kind(timeline, TOP_STACK_KINDS)
    missed_end_seconds = _top_run_seconds(timeline, "missed")

    if (
        not bottom_stack_seconds
        and not meeting_stack_seconds
        and not auto_afk_stack_seconds
        and not middle_stack_seconds
        and not post_activity_stack_seconds
        and not top_stack_seconds
        and missed_end_seconds <= 0
    ):
        return

    for index, kind in enumerate(timeline):
        if (
            kind in BOTTOM_STACK_KINDS
            or kind in MEETING_STACK_KINDS
            or kind in AUTO_AFK_STACK_KINDS
            or kind in MIDDLE_STACK_KINDS
            or kind in POST_ACTIVITY_STACK_KINDS
            or kind in TOP_STACK_KINDS
        ):
            timeline[index] = None

    if missed_end_seconds > 0:
        for index in range(3600 - missed_end_seconds, 3600):
            timeline[index] = None

    for kind in BOTTOM_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, bottom_stack_seconds.get(kind, 0), range(3600))

    for kind in MEETING_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, meeting_stack_seconds.get(kind, 0), range(3600))

    auto_afk_start = _clamp_second(auto_afk_start_second)
    auto_afk_indexes = range(auto_afk_start, 3600) if auto_afk_start is not None else range(3600)

    for kind in AUTO_AFK_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, auto_afk_stack_seconds.get(kind, 0), auto_afk_indexes)

    for kind in MIDDLE_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, middle_stack_seconds.get(kind, 0), range(3600))

    post_activity_start = _clamp_second(post_activity_start_second)
    post_activity_indexes = range(post_activity_start, 3600) if post_activity_start is not None else range(3600)

    for kind in POST_ACTIVITY_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, post_activity_stack_seconds.get(kind, 0), post_activity_indexes)

    _fill_empty_timeline_slots(timeline, "missed", missed_end_seconds, range(3599, -1, -1))

    for kind in TOP_STACK_KINDS:
        _fill_empty_timeline_slots(timeline, kind, top_stack_seconds.get(kind, 0), range(3599, -1, -1))

    if missed_end_seconds > 0:
        _fill_missed_hour_tail(timeline)


def _stack_seconds_by_kind(timeline: list[str | None], kinds: tuple[str, ...]) -> dict[str, int]:
    return {kind: sum(1 for timeline_kind in timeline if timeline_kind == kind) for kind in kinds}


def _top_run_seconds(timeline: list[str | None], kind: str) -> int:
    seconds = 0

    for index in range(3599, -1, -1):
        if timeline[index] != kind:
            break

        seconds += 1

    return seconds


def _effective_overtime_start_second(hour: dict[str, Any]) -> int | None:
    start_second = _clamp_second(hour.get(INTERNAL_OVERTIME_START_SECOND))

    if start_second is None:
        return None

    overtime_segments = _segments_for_kind(hour, "overtime")
    earlier_overtime_starts = [
        int(segment["startSecond"])
        for segment in overtime_segments
        if int(segment["startSecond"]) < start_second
    ]

    if not earlier_overtime_starts:
        return start_second

    return min(earlier_overtime_starts)


def _fill_missed_hour_tail(timeline: list[str | None]) -> None:
    last_occupied_index = -1

    for index, kind in enumerate(timeline):
        if kind is not None and kind != "missed":
            last_occupied_index = index

    if last_occupied_index < 0:
        return

    for index in range(last_occupied_index + 1, 3600):
        timeline[index] = "missed"


def _fill_empty_timeline_slots(timeline: list[str | None], kind: str, seconds: int, indexes: range) -> None:
    remaining_seconds = max(0, int(seconds))

    if remaining_seconds <= 0:
        return

    for index in indexes:
        if timeline[index] is not None:
            continue

        timeline[index] = kind
        remaining_seconds -= 1

        if remaining_seconds <= 0:
            return


def _clamp_second(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return min(3600, max(0, int(value)))
    except (TypeError, ValueError):
        return None


def _clamp_synthetic_idle_counters(hour: dict[str, Any]) -> None:
    remaining_idle_seconds = max(0, int(hour.get("idleSeconds", 0)))

    for key in (
        "telegramToFirstActivityIdleSeconds",
        "workdayHourGapIdleSeconds",
        "pluginHourGapIdleSeconds",
        "offlineIdleGapSeconds",
        "overtimeBoundaryIdleSeconds",
    ):
        seconds = min(remaining_idle_seconds, max(0, int(hour.get(key, 0))))
        hour[key] = seconds
        remaining_idle_seconds -= seconds


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
        target_item["autoBreakSeconds"] = int(target_item.get("autoBreakSeconds", 0)) + int(delta_item.get("autoBreakSeconds", 0))
        target_item["meetingSeconds"] = int(target_item.get("meetingSeconds", 0)) + int(delta_item.get("meetingSeconds", 0))
        target_item["overtimeActiveSeconds"] = seconds_from_microseconds(overtime_microseconds)
        target_item["telegramToFirstActivityIdleSeconds"] = int(target_item.get("telegramToFirstActivityIdleSeconds", 0)) + int(
            delta_item.get("telegramToFirstActivityIdleSeconds", 0)
        )
        for key in ("workdayHourGapIdleSeconds", "pluginHourGapIdleSeconds", "offlineIdleGapSeconds", "overtimeBoundaryIdleSeconds"):
            target_item[key] = int(target_item.get(key, 0)) + int(delta_item.get(key, 0))
        target_item.setdefault("fillSegments", []).extend(_sanitize_fill_segments(delta_item.get("fillSegments", [])))


def add_afk_fill_segment_to_hour(hour: dict[str, Any], start_second: int, end_second: int) -> None:
    _add_fill_segment(hour, "afk", start_second, end_second)


def add_auto_afk_fill_segment_to_hour(hour: dict[str, Any], start_second: int, end_second: int) -> None:
    _add_fill_segment(hour, "auto-afk", start_second, end_second)


def add_idle_seconds_to_hour(hour: dict[str, Any], seconds: int) -> None:
    if seconds <= 0:
        return

    idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds") + (seconds * MICROSECONDS_PER_SECOND)
    hour["idleMicroseconds"] = idle_microseconds
    hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
    _append_available_seconds(hour.setdefault("fillSegments", []), "idle", seconds)


def _trim_idle_after_second(hour: dict[str, Any], boundary_second: int) -> int:
    boundary_second = max(0, min(3600, int(boundary_second)))
    trimmed_segments = []
    removed_seconds = 0

    for segment in _sanitize_fill_segments(hour.get("fillSegments", [])):
        if segment["kind"] != "idle":
            trimmed_segments.append(segment)
            continue

        start_second = int(segment["startSecond"])
        end_second = int(segment["endSecond"])

        if end_second <= boundary_second:
            trimmed_segments.append(segment)
            continue

        if start_second < boundary_second:
            trimmed_segments.append({**segment, "endSecond": boundary_second})
            removed_seconds += end_second - boundary_second
        else:
            removed_seconds += end_second - start_second

    if removed_seconds <= 0:
        hour["fillSegments"] = trimmed_segments
        return 0

    idle_microseconds = max(
        0,
        time_microseconds(hour, "idleSeconds", "idleMicroseconds") - (removed_seconds * MICROSECONDS_PER_SECOND),
    )
    hour["idleMicroseconds"] = idle_microseconds
    hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
    hour["fillSegments"] = trimmed_segments
    _clamp_synthetic_idle_counters(hour)
    return removed_seconds


def normalize_fill_segments_in_hour(hour: dict[str, Any]) -> None:
    afk_segments = _segments_for_kind(hour, "afk")
    auto_afk_segments = _segments_for_kind(hour, "auto-afk")
    hour["fillSegments"] = [
        segment
        for segment in _sanitize_fill_segments(hour.get("fillSegments", []))
        if segment["kind"] not in {"afk", "auto-afk"}
    ]
    hour["fillSegments"].extend(afk_segments)
    hour["fillSegments"].extend(auto_afk_segments)


def apply_overtime_start_boundary(hourly_activity: list[dict[str, Any]], overtime_started_at: dt.datetime, time_zone_id: str) -> None:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    local_started_at = overtime_started_at.astimezone(zone)
    hour_index = local_started_at.hour

    if hour_index < 0 or hour_index >= len(hourly_activity):
        return

    start_second = _second_of_hour(local_started_at)

    if start_second <= 0 or start_second >= 3600:
        return

    hour = hourly_activity[hour_index]

    if (
        time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") <= 0
        and int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) <= 0
    ):
        return

    current_boundary = _clamp_second(hour.get(INTERNAL_OVERTIME_START_SECOND))
    hour[INTERNAL_OVERTIME_START_SECOND] = start_second if current_boundary is None else min(current_boundary, start_second)
    _trim_idle_after_second(hour, start_second)

    visible_totals = _totals_from_segments(normalized_fill_segments({**hour, INTERNAL_OVERTIME_START_SECOND: None}))
    pre_overtime_seconds = (
        int(visible_totals.get("active", 0))
        + int(visible_totals.get("afk", 0))
        + int(visible_totals.get("meeting", 0))
        + int(visible_totals.get("idle", 0))
    )
    missing_pre_overtime_seconds = max(0, start_second - pre_overtime_seconds)

    if missing_pre_overtime_seconds > 0:
        add_idle_seconds_to_hour(hour, missing_pre_overtime_seconds)
        hour["overtimeBoundaryIdleSeconds"] = int(hour.get("overtimeBoundaryIdleSeconds", 0)) + missing_pre_overtime_seconds
        visual_overtime_seconds = int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0))
        hour[INTERNAL_OVERTIME_FILL_SECONDS] = max(0, visual_overtime_seconds - missing_pre_overtime_seconds)
        remove_visual_missed_seconds(hour, missing_pre_overtime_seconds)

    post_boundary_seconds = 3600 - start_second
    visible_overtime_seconds = (
        time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds")
        + int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0))
    )
    missing_post_overtime_seconds = max(0, post_boundary_seconds - visible_overtime_seconds)

    if missing_post_overtime_seconds > 0:
        hour[INTERNAL_OVERTIME_FILL_SECONDS] = int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) + missing_post_overtime_seconds
        remove_visual_missed_seconds(hour, missing_post_overtime_seconds)


def apply_overtime_start_boundaries(
    hourly_by_author: dict[str, dict[str, Any]],
    sessions: list[dict[str, Any]],
    *,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
) -> None:
    for session in sessions:
        if str(session.get("reminderAction") or "") != "overtime":
            continue

        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not day_date or not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        overtime_started_at = _coerce_datetime(session.get("lastOfflineAt"))
        hourly_author = hourly_by_author.get(raw_author)

        if not overtime_started_at or not hourly_author:
            continue

        apply_overtime_start_boundary(hourly_author.get("hourlyActivity", []), overtime_started_at, time_zone_id)


def apply_first_post_offline_overtime_boundaries(
    hourly_by_author: dict[str, dict[str, Any]],
    overtime_reports_by_key: dict[tuple[str, str], list[dt.datetime]],
    overtime_starts_by_key: dict[tuple[str, str], list[dt.datetime]],
    *,
    time_zone_id_for_author: Any,
) -> None:
    for (raw_author, report_date), starts in overtime_starts_by_key.items():
        hourly_author = hourly_by_author.get(raw_author)
        reports = sorted(overtime_reports_by_key.get((raw_author, report_date), []))

        if not hourly_author or not starts or not reports:
            continue

        ordered_starts = sorted(starts)

        for index, overtime_start in enumerate(ordered_starts):
            next_start = ordered_starts[index + 1] if index + 1 < len(ordered_starts) else None
            first_report = next(
                (
                    report
                    for report in reports
                    if report >= overtime_start and (next_start is None or report < next_start)
                ),
                None,
            )

            if first_report is None:
                continue

            if first_report.hour == overtime_start.hour or is_night_overtime_hour(first_report.hour):
                continue

            time_zone_id = time_zone_id_for_author(raw_author, None)
            apply_overtime_start_boundary(hourly_author.get("hourlyActivity", []), first_report, time_zone_id)


def apply_night_overtime_hour_fills(hourly_by_author: dict[str, dict[str, Any]]) -> None:
    for hourly_author in hourly_by_author.values():
        for hour in hourly_author.get("hourlyActivity", []):
            hour_index = int(hour.get("hour", 0))

            if not is_night_overtime_hour(hour_index):
                continue

            if time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") <= 0:
                continue

            if int(hour.get(INTERNAL_MISSED_END_SECONDS, 0)) > 0:
                continue

            fill_visual_overtime_hour(hour, preserve_visual_missed=True)


def apply_night_overtime_missed_end(
    hourly_by_author: dict[str, dict[str, Any]],
    latest_report_by_author_date: dict[tuple[str, str], dt.datetime],
    *,
    time_zone_id_for_author: Any,
) -> None:
    for (raw_author, _day_date), latest_report_at in latest_report_by_author_date.items():
        time_zone_id = time_zone_id_for_author(raw_author, None)
        local_latest_report_at = _to_local_datetime(latest_report_at, time_zone_id)

        if not is_night_overtime_hour(local_latest_report_at.hour):
            continue

        hourly_author = hourly_by_author.get(raw_author)

        if not hourly_author:
            continue

        hourly_activity = hourly_author.get("hourlyActivity", [])
        hour_index = local_latest_report_at.hour

        if hour_index < 0 or hour_index >= len(hourly_activity):
            continue

        hour = hourly_activity[hour_index]

        if time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") <= 0:
            continue

        if int(hour.get(INTERNAL_MISSED_END_SECONDS, 0)) > 0:
            continue

        fill_visual_overtime_hour(hour)


def apply_visual_missed_end_fallbacks(
    hourly_by_author: dict[str, dict[str, Any]],
    sessions: list[dict[str, Any]],
    latest_report_by_author_date: dict[tuple[str, str], dt.datetime] | None = None,
    *,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
) -> None:
    latest_report_by_author_date = latest_report_by_author_date or {}

    for session in sessions:
        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not day_date or not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        offline_at = _coerce_datetime(session.get("lastOfflineAt"))
        hourly_author = hourly_by_author.get(raw_author)

        if not offline_at or not hourly_author:
            continue

        hourly_activity = hourly_author.get("hourlyActivity", [])

        if any(int(hour.get(INTERNAL_MISSED_END_SECONDS, 0)) > 0 for hour in hourly_activity):
            continue

        local_offline_at = _to_local_datetime(offline_at, time_zone_id)
        offline_hour_index = local_offline_at.hour

        if offline_hour_index < 0 or offline_hour_index >= len(hourly_activity):
            continue

        if visual_hour_available_seconds(hourly_activity[offline_hour_index]) > 0:
            continue

        latest_report_at = latest_report_by_author_date.get((raw_author, day_date))
        latest_report_hour_index = None

        if latest_report_at and latest_report_at > offline_at:
            latest_report_hour_index = _to_local_datetime(latest_report_at, time_zone_id).hour

        fallback_hour_index = (latest_report_hour_index if latest_report_hour_index is not None else offline_hour_index) + 1

        if fallback_hour_index >= len(hourly_activity):
            continue

        if is_night_overtime_hour(fallback_hour_index):
            continue

        fallback_hour = hourly_activity[fallback_hour_index]

        if visual_hour_occupied_seconds(fallback_hour) > 0 or int(fallback_hour.get("missedSeconds", 0)) > 0:
            continue

        add_visual_missed_seconds(hourly_activity, fallback_hour_index, 3600, INTERNAL_MISSED_END_SECONDS)


def transfer_summary_idle_to_auto_break(
    hourly_activity: list[dict[str, Any]],
    remaining_seconds: int,
    auto_break_start_second_by_hour: dict[int, int] | None = None,
) -> int:
    if remaining_seconds <= 0:
        return 0

    transferred_seconds = 0

    for hour in sorted(hourly_activity, key=lambda item: int(item.get("hour", 0))):
        _disable_regular_afk_for_auto_break_hour(hour)
        if transferred_seconds >= remaining_seconds:
            continue

        hour_index = int(hour.get("hour", 0))
        auto_break_start_second = (
            max(0, min(3600, int(auto_break_start_second_by_hour.get(hour_index, 0))))
            if auto_break_start_second_by_hour is not None
            else 0
        )

        if auto_break_start_second >= 3600:
            continue

        idle_seconds = max(0, int(hour.get("idleSeconds", 0)))
        synthetic_idle_seconds = (
            max(0, int(hour.get("telegramToFirstActivityIdleSeconds", 0)))
            + max(0, int(hour.get("workdayHourGapIdleSeconds", 0)))
            + max(0, int(hour.get("pluginHourGapIdleSeconds", 0)))
            + max(0, int(hour.get("offlineIdleGapSeconds", 0)))
            + max(0, int(hour.get("overtimeBoundaryIdleSeconds", 0)))
        )
        convertible_idle_seconds = max(0, idle_seconds - synthetic_idle_seconds)

        if convertible_idle_seconds <= 0:
            continue

        boundary_available_seconds = 3600 - auto_break_start_second
        requested_seconds = min(
            convertible_idle_seconds,
            boundary_available_seconds,
            remaining_seconds - transferred_seconds,
        )
        move_seconds = _convert_visible_idle_segments_to_auto_break(
            hour,
            requested_seconds,
            auto_break_start_second,
        )

        if move_seconds <= 0:
            continue

        idle_microseconds = max(
            0,
            time_microseconds(hour, "idleSeconds", "idleMicroseconds") - (move_seconds * MICROSECONDS_PER_SECOND),
        )
        hour["idleMicroseconds"] = idle_microseconds
        hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
        _clamp_synthetic_idle_counters(hour)
        hour["breakSeconds"] = int(hour.get("breakSeconds", 0)) + move_seconds
        hour["autoBreakSeconds"] = int(hour.get("autoBreakSeconds", 0)) + move_seconds
        if auto_break_start_second > 0:
            current_start_second = _clamp_second(hour.get(INTERNAL_AUTO_BREAK_START_SECOND))
            hour[INTERNAL_AUTO_BREAK_START_SECOND] = (
                min(current_start_second, auto_break_start_second)
                if current_start_second is not None
                else auto_break_start_second
            )
        normalize_fill_segments_in_hour(hour)
        transferred_seconds += move_seconds

    return transferred_seconds


def _convert_visible_idle_segments_to_auto_break(hour: dict[str, Any], requested_seconds: int, start_second: int) -> int:
    requested_seconds = max(0, int(requested_seconds))
    start_second = max(0, min(3600, int(start_second)))

    if requested_seconds <= 0 or start_second >= 3600:
        return 0

    visible_segments = normalized_fill_segments(hour)
    converted_seconds = 0
    converted_segments: list[dict[str, Any]] = []

    for segment in visible_segments:
        if converted_seconds >= requested_seconds:
            break

        if segment["kind"] != "idle":
            continue

        segment_start = max(start_second, int(segment["startSecond"]))
        segment_end = int(segment["endSecond"])
        available_seconds = segment_end - segment_start

        if available_seconds <= 0:
            continue

        move_seconds = min(available_seconds, requested_seconds - converted_seconds)
        converted_segments.append(
            {
                "kind": "auto-afk",
                "startSecond": segment_start,
                "endSecond": segment_start + move_seconds,
            }
        )
        converted_seconds += move_seconds

    if converted_seconds <= 0:
        return 0

    converted_timeline = [False] * 3600
    for segment in converted_segments:
        for second in range(segment["startSecond"], segment["endSecond"]):
            converted_timeline[second] = True

    timeline: list[str | None] = [None] * 3600
    for segment in visible_segments:
        kind = segment["kind"]
        for second in range(segment["startSecond"], segment["endSecond"]):
            if converted_timeline[second]:
                timeline[second] = "auto-afk"
            elif kind in {"idle", "telegram-idle"}:
                timeline[second] = "idle"
            elif kind != "auto-afk":
                timeline[second] = kind

    hour["fillSegments"] = _segments_from_timeline(timeline)
    return converted_seconds


def convert_hourly_to_vacation_overtime(hourly_activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []

    for source_hour in hourly_activity:
        hour = dict(source_hour)
        active_seconds = int(hour.get("activeSeconds", 0))
        meeting_seconds = int(hour.get("meetingSeconds", 0))
        overtime_microseconds = time_microseconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds")
        overtime_microseconds += (active_seconds + meeting_seconds) * MICROSECONDS_PER_SECOND

        hour["activeSeconds"] = 0
        hour["activeMicroseconds"] = 0
        hour["idleSeconds"] = 0
        hour["idleMicroseconds"] = 0
        hour["breakSeconds"] = 0
        hour["autoBreakSeconds"] = 0
        hour["meetingSeconds"] = 0
        hour["missedSeconds"] = 0
        hour[INTERNAL_OVERTIME_FILL_SECONDS] = 0
        hour[INTERNAL_OVERTIME_START_SECOND] = None
        hour[INTERNAL_MISSED_START_SECONDS] = 0
        hour[INTERNAL_MISSED_END_SECONDS] = 0
        hour["fillSegments"] = [
            segment
            for segment in _sanitize_fill_segments(hour.get("fillSegments", []))
            if segment["kind"] == "overtime"
        ]

        if overtime_microseconds > 0:
            hour["overtimeActiveMicroseconds"] = overtime_microseconds
            hour["overtimeActiveSeconds"] = seconds_from_microseconds(overtime_microseconds)
            if not _segments_for_kind(hour, "overtime"):
                _append_available_seconds(hour.setdefault("fillSegments", []), "overtime", hour["overtimeActiveSeconds"])
            fill_visual_overtime_hour(hour)
        else:
            hour["overtimeActiveSeconds"] = 0
            hour["overtimeActiveMicroseconds"] = 0

        converted.append(hour)

    return converted


def apply_visual_missed_hours(
    hourly_by_author: dict[str, dict[str, Any]],
    sessions: list[dict[str, Any]],
    latest_report_by_author_date: dict[tuple[str, str], dt.datetime],
    *,
    include_start: bool = True,
    include_end: bool = True,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
    display_name_for_author: Any,
) -> None:
    for session in sessions:
        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not day_date or not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        started_at = _coerce_datetime(session.get("startedAt"))
        ended_at = _coerce_datetime(session.get("lastOfflineAt"))
        latest_report_at = latest_report_by_author_date.get((raw_author, day_date))
        latest_signal_at = (latest_report_at or ended_at) if ended_at else None

        if not started_at and not latest_signal_at:
            continue

        hourly_author = hourly_by_author.get(raw_author)

        if not hourly_author:
            hourly_author = {
                "author": display_name_for_author(raw_author),
                "rawAuthor": raw_author,
                "timeZoneId": session.get("timeZoneId"),
                "timeZoneDisplayName": session.get("timeZoneDisplayName"),
                "hourlyActivity": empty_hourly_activity(),
            }
            hourly_by_author[raw_author] = hourly_author

        hourly_activity = hourly_author.get("hourlyActivity", [])
        if include_start:
            add_visual_missed_start(hourly_activity, started_at, time_zone_id)
        if include_end:
            add_visual_missed_end(
                hourly_activity,
                latest_signal_at,
                time_zone_id,
                fill_to_hour=latest_report_at is not None,
                offline_at=ended_at,
            )


def add_visual_missed_start(
    hourly_activity: list[dict[str, Any]],
    started_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    if not started_at:
        return

    local_start = _to_local_datetime(started_at, time_zone_id)

    if is_night_overtime_hour(local_start.hour):
        return

    hour_start = local_start.replace(minute=0, second=0, microsecond=0)
    missed_seconds = max(0, int((local_start - hour_start).total_seconds()))
    add_visual_missed_seconds(hourly_activity, local_start.hour, missed_seconds, INTERNAL_MISSED_START_SECONDS)
    trim_visual_idle_overflow(hourly_activity, local_start.hour)


def add_visual_missed_end(
    hourly_activity: list[dict[str, Any]],
    ended_at: dt.datetime | None,
    time_zone_id: str,
    fill_to_hour: bool = False,
    offline_at: dt.datetime | None = None,
) -> None:
    if not ended_at:
        return

    local_end = _to_local_datetime(ended_at, time_zone_id)

    if fill_to_hour:
        target_hour = visual_missed_end_hour(
            hourly_activity,
            local_end.hour,
            _to_local_datetime(offline_at, time_zone_id) if offline_at else None,
        )
        missed_seconds = visual_hour_available_seconds(target_hour)
        target_hour_index = int(target_hour.get("hour", local_end.hour))
        if is_night_overtime_hour(target_hour_index):
            hour_end = local_end.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            missed_seconds = max(0, int((hour_end - local_end).total_seconds())) if target_hour_index == local_end.hour else 3600
        if missed_seconds <= 0:
            fallback_hour_index = target_hour_index + 1
            if fallback_hour_index < len(hourly_activity):
                fallback_hour = hourly_activity[fallback_hour_index]
                if visual_hour_occupied_seconds(fallback_hour) <= 0 and int(fallback_hour.get("missedSeconds", 0)) <= 0:
                    target_hour_index = fallback_hour_index
                    missed_seconds = 3600
    else:
        hour_end = local_end.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        missed_seconds = max(0, int((hour_end - local_end).total_seconds()))
        target_hour_index = local_end.hour

    if is_night_overtime_hour(target_hour_index):
        target_hour = hourly_activity[target_hour_index]
        if time_seconds(target_hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") > 0:
            fill_visual_overtime_hour(target_hour)
        return

    add_visual_missed_seconds(hourly_activity, target_hour_index, missed_seconds, INTERNAL_MISSED_END_SECONDS)
    trim_visual_idle_overflow(hourly_activity, target_hour_index)


def trim_visual_idle_overflow(hourly_activity: list[dict[str, Any]], hour_index: int) -> None:
    if hour_index < 0 or hour_index >= len(hourly_activity):
        return

    hour = hourly_activity[hour_index]
    occupied_seconds = visual_hour_occupied_seconds(hour) + int(hour.get("missedSeconds", 0))
    overflow_seconds = max(0, occupied_seconds - 3600)

    if overflow_seconds <= 0:
        return

    idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds")
    overflow_microseconds = overflow_seconds * MICROSECONDS_PER_SECOND
    hour["idleMicroseconds"] = max(0, idle_microseconds - overflow_microseconds)
    hour["idleSeconds"] = seconds_from_microseconds(hour["idleMicroseconds"])


def apply_visual_overtime_hour_gaps(
    hourly_by_author: dict[str, dict[str, Any]],
    reports: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    *,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
) -> None:
    overtime_reports_by_key: dict[tuple[str, str], list[dt.datetime]] = {}
    overtime_starts_by_key: dict[tuple[str, str], list[dt.datetime]] = {}

    for session in sessions:
        if str(session.get("reminderAction") or "") != "overtime":
            continue

        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not day_date or not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        overtime_started_at = _coerce_datetime(session.get("lastOfflineAt"))

        if not overtime_started_at:
            continue

        overtime_starts_by_key.setdefault((raw_author, day_date), []).append(
            _to_local_datetime(overtime_started_at, time_zone_id)
        )

    for report in reports:
        if report.get("source") in {"telegram", "discord", "status"} or report.get("reportType") in {"telegram", "meeting", "status"}:
            continue

        if time_microseconds(report, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") <= 0:
            continue

        raw_author = str(report.get("author") or "Unknown User")
        report_date = str(report.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, report.get("timeZoneId"))

        if not report_date or not is_date_in_scope(report_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, report_date):
            continue

        occurred_at = (
            _coerce_datetime(report.get("recordedAt"))
            or _coerce_datetime(report.get("lastRecordedAt"))
            or _coerce_datetime(report.get("receivedAt"))
            or _coerce_datetime(report.get("lastReceivedAt"))
        )

        if not occurred_at:
            continue

        overtime_reports_by_key.setdefault((raw_author, report_date), []).append(_to_local_datetime(occurred_at, time_zone_id))

    apply_first_post_offline_overtime_boundaries(
        hourly_by_author,
        overtime_reports_by_key,
        overtime_starts_by_key,
        time_zone_id_for_author=time_zone_id_for_author,
    )

    for (raw_author, _report_date), reports_for_author in overtime_reports_by_key.items():
        hourly_author = hourly_by_author.get(raw_author)

        if not hourly_author:
            continue

        hourly_activity = hourly_author.get("hourlyActivity", [])
        ordered_reports = sorted(reports_for_author)
        overtime_starts = sorted(overtime_starts_by_key.get((raw_author, _report_date), []))

        for report_session in overtime_report_visual_sessions(ordered_reports, overtime_starts):
            session_reports = report_session["reports"]
            fill_overtime_hours_bracketed_by_reports(hourly_activity, session_reports)
            fill_overtime_hours_between_overtime_buckets(
                hourly_activity,
                start_hour=report_session["startHour"],
                end_hour=report_session["endHour"],
            )
        fill_normal_to_overtime_transition_hours(hourly_activity)


def overtime_report_visual_sessions(reports: list[dt.datetime], overtime_starts: list[dt.datetime]) -> list[dict[str, Any]]:
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_overtime_starts = sorted(overtime_starts)

    for report in sorted(reports):
        session_key, start_hour, end_hour = overtime_report_visual_session_bounds(report, ordered_overtime_starts)
        session = sessions.setdefault(session_key, {"reports": [], "startHour": start_hour, "endHour": end_hour})
        session["reports"].append(report)
        session["startHour"] = min(int(session["startHour"]), start_hour)
        session["endHour"] = max(int(session["endHour"]), end_hour)

    return sorted(
        sessions.values(),
        key=lambda item: (int(item["startHour"]), item["reports"][0] if item["reports"] else dt.datetime.min),
    )


def overtime_report_visual_session_bounds(
    report: dt.datetime,
    overtime_starts: list[dt.datetime],
) -> tuple[tuple[str, str], int, int]:
    report_hour = int(report.hour)

    if is_night_overtime_hour(report_hour):
        return ("night", ""), NIGHT_OVERTIME_START_HOUR, NIGHT_OVERTIME_END_HOUR - 1

    matching_start = None
    for overtime_start in overtime_starts:
        if overtime_start <= report:
            matching_start = overtime_start
        else:
            break

    if matching_start is not None:
        start_hour = min(int(matching_start.hour), max(NIGHT_OVERTIME_END_HOUR, report_hour - 1))
        return ("post-offline", matching_start.isoformat()), start_hour, report_hour

    return ("normal", ""), max(NIGHT_OVERTIME_END_HOUR, report_hour - 1), report_hour


def apply_plugin_hour_idle_gaps(
    authors_by_raw: dict[str, dict[str, Any]],
    hourly_by_author: dict[str, dict[str, Any]],
    latest_report_by_author_date: dict[tuple[str, str], dt.datetime],
    sessions: list[dict[str, Any]],
    *,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
) -> None:
    session_start_by_key: dict[tuple[str, str], dt.datetime] = {}

    for session in sessions:
        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        started_at = _coerce_datetime(session.get("startedAt"))

        if not raw_author or not day_date or not started_at:
            continue

        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        local_started_at = _to_local_datetime(started_at, time_zone_id)
        key = (raw_author, day_date)
        current = session_start_by_key.get(key)

        if not current or local_started_at < current:
            session_start_by_key[key] = local_started_at

    for (raw_author, day_date), latest_report_at in latest_report_by_author_date.items():
        time_zone_id = time_zone_id_for_author(raw_author, None)

        if not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        author_row = authors_by_raw.get(raw_author)
        hourly_author = hourly_by_author.get(raw_author)

        if not author_row or not hourly_author:
            continue

        local_latest_report_at = _to_local_datetime(latest_report_at, time_zone_id)
        hourly_activity = hourly_author.get("hourlyActivity", [])

        if not hourly_activity:
            continue

        session_started_at = session_start_by_key.get((raw_author, day_date))
        start_hour = 0

        if session_started_at:
            start_hour = max(0, min(len(hourly_activity), session_started_at.hour))

        first_accounted_hour = None
        for hour_index in range(start_hour, len(hourly_activity)):
            hour = hourly_activity[hour_index]
            regular_accounted_seconds = (
                time_seconds(hour, "activeSeconds", "activeMicroseconds")
                + time_seconds(hour, "idleSeconds", "idleMicroseconds")
                + int(hour.get("breakSeconds", 0))
                + int(hour.get("autoBreakSeconds", 0))
                + int(hour.get("meetingSeconds", 0))
                + int(hour.get("telegramToFirstActivityIdleSeconds", 0))
                + int(hour.get("missedSeconds", 0))
            )
            if session_started_at and hour_index == session_started_at.hour:
                regular_accounted_seconds = max(regular_accounted_seconds, _second_of_hour(session_started_at))
            if regular_accounted_seconds > 0:
                first_accounted_hour = hour_index
                break

        if first_accounted_hour is None:
            continue

        for hour_index in range(first_accounted_hour, min(local_latest_report_at.hour, len(hourly_activity))):
            if is_night_overtime_hour(hour_index):
                continue

            hour = hourly_activity[hour_index]
            gap_start_second = 0

            if session_started_at and hour_index == session_started_at.hour:
                gap_start_second = _second_of_hour(session_started_at)

            for gap_start, gap_end in _empty_second_ranges(hour, gap_start_second, 3600):
                idle_seconds = gap_end - gap_start

                if idle_seconds <= 0:
                    continue

                idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds") + (
                    idle_seconds * MICROSECONDS_PER_SECOND
                )
                hour["idleMicroseconds"] = idle_microseconds
                hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
                hour["pluginHourGapIdleSeconds"] = int(hour.get("pluginHourGapIdleSeconds", 0)) + idle_seconds
                _add_fill_segment(hour, "idle", gap_start, gap_end)


def apply_offline_idle_gaps(
    authors_by_raw: dict[str, dict[str, Any]],
    hourly_by_author: dict[str, dict[str, Any]],
    sessions: list[dict[str, Any]],
    latest_report_by_author_date: dict[tuple[str, str], dt.datetime],
    *,
    default_plugin_work_window_seconds: int,
    time_zone_id_for_author: Any,
    is_date_in_scope: Any,
    is_vacation_day: Any,
) -> None:
    for session in sessions:
        raw_author = str(session.get("rawAuthor") or "Unknown User")
        day_date = str(session.get("date") or "")
        time_zone_id = time_zone_id_for_author(raw_author, session.get("timeZoneId"))

        if not day_date or not is_date_in_scope(day_date, raw_author, time_zone_id):
            continue

        if is_vacation_day(raw_author, day_date):
            continue

        latest_report_at = latest_report_by_author_date.get((raw_author, day_date))
        ended_at = _coerce_datetime(session.get("lastOfflineAt"))

        if not latest_report_at or not ended_at or ended_at <= latest_report_at:
            continue

        author_row = authors_by_raw.get(raw_author)
        hourly_author = hourly_by_author.get(raw_author)

        if not author_row or not hourly_author:
            continue

        gap_seconds = max(0, int((ended_at - latest_report_at).total_seconds()))
        remaining_plugin_seconds = max(0, default_plugin_work_window_seconds - int(author_row.get("pluginDaySeconds", 0)))
        idle_seconds = min(gap_seconds, remaining_plugin_seconds)

        if idle_seconds <= 0:
            continue

        idle_end = latest_report_at + dt.timedelta(seconds=idle_seconds)
        hourly_activity = empty_hourly_activity()
        add_idle_interval_to_buckets(hourly_activity, latest_report_at, idle_end, time_zone_id)
        for hour in hourly_activity:
            if is_night_overtime_hour(int(hour.get("hour", 0))):
                hour["idleSeconds"] = 0
                hour["idleMicroseconds"] = 0
                hour["fillSegments"] = [segment for segment in _sanitize_fill_segments(hour.get("fillSegments", [])) if segment["kind"] != "idle"]

            idle_gap_seconds = int(hour.get("idleSeconds", 0))
            if idle_gap_seconds > 0:
                hour["offlineIdleGapSeconds"] = idle_gap_seconds
        merge_hourly_activity(hourly_author["hourlyActivity"], hourly_activity)


def apply_workday_idle_fill(
    hourly_activity: list[dict[str, Any]],
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
    has_activity_signal: bool,
) -> int:
    if not has_activity_signal or not started_at or not ended_at or ended_at <= started_at:
        return 0

    target_by_hour = {int(item.get("hour", 0)): item for item in hourly_activity}
    added_seconds = 0

    for hour_index, start_second, end_second, _seconds in split_interval_by_hour(started_at, ended_at, time_zone_id):
        if is_night_overtime_hour(hour_index):
            continue

        hour = target_by_hour.get(hour_index)

        if not hour:
            continue

        for gap_start, gap_end in _empty_second_ranges(hour, start_second, end_second):
            idle_seconds = gap_end - gap_start

            if idle_seconds <= 0:
                continue

            idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds") + (idle_seconds * MICROSECONDS_PER_SECOND)
            hour["idleMicroseconds"] = idle_microseconds
            hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
            hour["workdayHourGapIdleSeconds"] = int(hour.get("workdayHourGapIdleSeconds", 0)) + idle_seconds
            added_seconds += idle_seconds
            _add_fill_segment(hour, "idle", gap_start, gap_end)

    return added_seconds


def apply_visible_workday_idle_reconciliation(
    hourly_activity: list[dict[str, Any]],
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
    has_activity_signal: bool,
) -> int:
    if not has_activity_signal or not started_at or not ended_at or ended_at <= started_at:
        return 0

    target_by_hour = {int(item.get("hour", 0)): item for item in hourly_activity}
    added_seconds = 0

    for hour_index, start_second, end_second, _seconds in split_interval_by_hour(started_at, ended_at, time_zone_id):
        if is_night_overtime_hour(hour_index):
            continue

        hour = target_by_hour.get(hour_index)

        if not hour:
            continue

        public_segments = public_hour(hour).get("fillSegments", [])
        if not any(segment.get("kind") in {"afk", "auto-afk", "meeting"} for segment in public_segments):
            continue

        for gap_start, gap_end in _visible_empty_second_ranges(hour, start_second, end_second):
            idle_seconds = gap_end - gap_start

            if idle_seconds <= 0:
                continue

            idle_microseconds = time_microseconds(hour, "idleSeconds", "idleMicroseconds") + (idle_seconds * MICROSECONDS_PER_SECOND)
            hour["idleMicroseconds"] = idle_microseconds
            hour["idleSeconds"] = seconds_from_microseconds(idle_microseconds)
            hour["workdayHourGapIdleSeconds"] = int(hour.get("workdayHourGapIdleSeconds", 0)) + idle_seconds
            added_seconds += idle_seconds
            _add_fill_segment(hour, "idle", gap_start, gap_end)

    return added_seconds


def hourly_activity_has_workday_signal(hourly_activity: list[dict[str, Any]]) -> bool:
    for hour in hourly_activity:
        if (
            time_seconds(hour, "activeSeconds", "activeMicroseconds") > 0
            or int(hour.get("meetingSeconds", 0)) > 0
            or int(hour.get("breakSeconds", 0)) > 0
        ):
            return True

        visible_plugin_idle = max(
            0,
            time_seconds(hour, "idleSeconds", "idleMicroseconds")
            - int(hour.get("telegramToFirstActivityIdleSeconds", 0))
            - int(hour.get("workdayHourGapIdleSeconds", 0)),
        )
        if visible_plugin_idle > 0:
            return True

        for segment in _sanitize_fill_segments(hour.get("fillSegments", [])):
            if segment["kind"] in {"active", "afk", "auto-afk", "meeting"}:
                return True

    return False


def _empty_second_ranges(hour: dict[str, Any], start_second: int, end_second: int) -> list[tuple[int, int]]:
    start_second = max(0, min(3600, int(start_second)))
    end_second = max(0, min(3600, int(end_second)))

    if end_second <= start_second:
        return []

    occupied = [False] * 3600
    for segment in _raw_occupied_segments(hour):
        for second in range(max(0, int(segment["startSecond"])), min(3600, int(segment["endSecond"]))):
            occupied[second] = True

    ranges = []
    cursor = start_second

    while cursor < end_second:
        while cursor < end_second and occupied[cursor]:
            cursor += 1

        if cursor >= end_second:
            break

        gap_start = cursor
        while cursor < end_second and not occupied[cursor]:
            cursor += 1

        ranges.append((gap_start, cursor))

    return ranges


def _visible_empty_second_ranges(hour: dict[str, Any], start_second: int, end_second: int) -> list[tuple[int, int]]:
    start_second = max(0, min(3600, int(start_second)))
    end_second = max(0, min(3600, int(end_second)))

    if end_second <= start_second:
        return []

    occupied = [False] * 3600
    for segment in public_hour(hour).get("fillSegments", []):
        for second in range(max(0, int(segment["startSecond"])), min(3600, int(segment["endSecond"]))):
            occupied[second] = True

    ranges = []
    cursor = start_second

    while cursor < end_second:
        while cursor < end_second and occupied[cursor]:
            cursor += 1

        if cursor >= end_second:
            break

        gap_start = cursor
        while cursor < end_second and not occupied[cursor]:
            cursor += 1

        ranges.append((gap_start, cursor))

    return ranges


def _raw_occupied_segments(hour: dict[str, Any]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    cursor = 0
    cursor = _append_stacked_seconds(generated, "missed", int(hour.get(INTERNAL_MISSED_START_SECONDS, 0)), cursor)
    missed_end_seconds = int(hour.get(INTERNAL_MISSED_END_SECONDS, 0))
    _append_stacked_seconds(generated, "missed", missed_end_seconds, 3600 - missed_end_seconds)

    for kind, seconds_key, microseconds_key in (
        ("active", "activeSeconds", "activeMicroseconds"),
        ("idle", "idleSeconds", "idleMicroseconds"),
        ("overtime", "overtimeActiveSeconds", "overtimeActiveMicroseconds"),
    ):
        positioned_seconds = _segments_total_seconds(_segments_for_kind(hour, kind))
        _append_available_seconds(generated, kind, max(0, time_seconds(hour, seconds_key, microseconds_key) - positioned_seconds))

    meeting_positioned_seconds = _segments_total_seconds(_segments_for_kind(hour, "meeting"))
    _append_available_seconds(generated, "meeting", max(0, int(hour.get("meetingSeconds", 0)) - meeting_positioned_seconds))
    afk_positioned_seconds = _segments_total_seconds(_segments_for_kind(hour, "afk"))
    _append_available_seconds(
        generated,
        "afk",
        max(0, int(hour.get("breakSeconds", 0)) - int(hour.get("autoBreakSeconds", 0)) - afk_positioned_seconds),
    )
    auto_afk_positioned_seconds = _segments_total_seconds(_segments_for_kind(hour, "auto-afk"))
    _append_available_seconds(generated, "auto-afk", max(0, int(hour.get("autoBreakSeconds", 0)) - auto_afk_positioned_seconds))

    generated.extend(_sanitize_fill_segments(hour.get("fillSegments", [])))
    return normalize_hour_fill(generated, apply_stack_rules=False)


def _to_local_datetime(value: dt.datetime, time_zone_id: str | None) -> dt.datetime:
    try:
        zone = ZoneInfo(time_zone_id or "UTC")
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return value.astimezone(zone)


def _disable_regular_afk_for_auto_break_hour(hour: dict[str, Any]) -> None:
    auto_break_seconds = max(0, int(hour.get("autoBreakSeconds", 0)))
    hour["breakSeconds"] = auto_break_seconds
    hour["fillSegments"] = [
        segment for segment in _sanitize_fill_segments(hour.get("fillSegments", [])) if segment["kind"] != "afk"
    ]


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
        auto_break_seconds = max(0, int(source_hour.get("autoBreakSeconds", 0)))
        raw_idle_seconds = time_seconds(source_hour, "idleSeconds", "idleMicroseconds")
        requested_break_seconds = max(0, int(break_hour.get("breakSeconds", 0)))
        consumed_break_seconds = max(0, int(consumed_hour.get("breakSeconds", 0)))
        available_break_seconds = max(0, requested_break_seconds - consumed_break_seconds)
        source_break_segments = _segments_for_kind(break_hour, "afk")
        has_any_source_overlap = break_hour.get(INTERNAL_HAS_SOURCE_BREAK_OVERLAP)
        if (
            consumed_buckets is not None
            and has_any_source_overlap is not False
            and source_break_segments
            and not _source_hour_overlaps_break(source_hour, source_break_segments)
        ):
            break_seconds = 0
        else:
            break_seconds = min(available_break_seconds, 3600)
        idle_seconds = min(max(0, raw_idle_seconds - break_seconds), max(0, 3600 - active_seconds - overtime_seconds - break_seconds))
        break_segments = segments_after_consumed_seconds(
            source_break_segments,
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
            "autoBreakSeconds": auto_break_seconds,
            "meetingSeconds": int(source_hour.get("meetingSeconds", 0)),
            "overtimeActiveSeconds": overtime_seconds,
            INTERNAL_OVERTIME_FILL_SECONDS: int(source_hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)),
            "missedSeconds": int(source_hour.get("missedSeconds", 0)),
            INTERNAL_MISSED_START_SECONDS: int(source_hour.get(INTERNAL_MISSED_START_SECONDS, 0)),
            INTERNAL_MISSED_END_SECONDS: int(source_hour.get(INTERNAL_MISSED_END_SECONDS, 0)),
            "telegramToFirstActivityIdleSeconds": int(source_hour.get("telegramToFirstActivityIdleSeconds", 0)),
            "workdayHourGapIdleSeconds": int(source_hour.get("workdayHourGapIdleSeconds", 0)),
            "pluginHourGapIdleSeconds": int(source_hour.get("pluginHourGapIdleSeconds", 0)),
            "offlineIdleGapSeconds": int(source_hour.get("offlineIdleGapSeconds", 0)),
            "overtimeBoundaryIdleSeconds": int(source_hour.get("overtimeBoundaryIdleSeconds", 0)),
            "fillSegments": _segments_for_kind(source_hour, "active")
            + _segments_for_kind(source_hour, "overtime")
            + _segments_for_kind(source_hour, "auto-afk")
            + break_segments
            + _segments_for_kind(source_hour, "meeting")
            + _segments_for_kind(source_hour, "idle")
            + _segments_for_kind(source_hour, "missed"),
        }
        hourly_activity.append(result)

    return hourly_activity


def mark_break_source_overlaps(break_buckets: dict[tuple[str, str], list[dict[str, Any]]], daily_items: list[dict[str, Any]]) -> None:
    source_hours_by_author_date: dict[tuple[str, str], list[dict[int, dict[str, Any]]]] = {}

    for item in daily_items:
        raw_author = str(item.get("author") or "Unknown User")
        item_date = str(item.get("date") or "")

        if not item_date:
            continue

        source_hours_by_author_date.setdefault((raw_author, item_date), []).append(
            {int(hour.get("hour", 0)): hour for hour in item.get("hourlyActivity", [])}
        )

    for author_date_key, author_break_buckets in break_buckets.items():
        source_hour_maps = source_hours_by_author_date.get(author_date_key, [])

        for break_hour in author_break_buckets:
            source_break_segments = _segments_for_kind(break_hour, "afk")

            if not source_break_segments:
                continue

            hour = int(break_hour.get("hour", 0))
            break_hour[INTERNAL_HAS_SOURCE_BREAK_OVERLAP] = any(
                _source_hour_overlaps_break(source_hour_map.get(hour, {}), source_break_segments)
                for source_hour_map in source_hour_maps
            )


def _source_hour_overlaps_break(source_hour: dict[str, Any], break_segments: list[dict[str, Any]]) -> bool:
    if not break_segments:
        return True

    source_segments = [
        segment
        for segment in _raw_occupied_segments(source_hour)
        if segment["kind"] in {"active", "idle", "overtime", "overtime-fill", "meeting", "auto-afk"}
    ]

    for source_segment in source_segments:
        for break_segment in break_segments:
            if int(source_segment["startSecond"]) < int(break_segment["endSecond"]) and int(break_segment["startSecond"]) < int(
                source_segment["endSecond"]
            ):
                return True

    return False


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
        auto_break_seconds = int(source_hour.get("autoBreakSeconds", 0))
        requested_meeting_seconds = max(0, int((meeting_by_hour.get(hour, {}) or {}).get("meetingSeconds", 0)))
        meeting_capacity_seconds = min(requested_meeting_seconds, max(0, 3600 - break_seconds))
        consumed_meeting_seconds = max(0, int(consumed_hour.get("meetingSeconds", 0)))
        meeting_seconds = max(0, meeting_capacity_seconds - consumed_meeting_seconds)
        meeting_overlap_seconds = meeting_capacity_seconds
        raw_idle_seconds = max(0, int(source_hour.get("idleSeconds", 0)))
        idle_seconds = max(0, raw_idle_seconds - meeting_overlap_seconds)
        meeting_overlap_seconds = max(0, meeting_overlap_seconds - raw_idle_seconds)
        raw_active_seconds = max(0, int(source_hour.get("activeSeconds", 0)))
        active_seconds = max(0, raw_active_seconds - meeting_overlap_seconds)
        meeting_overlap_seconds = max(0, meeting_overlap_seconds - raw_active_seconds)
        raw_overtime_seconds = max(0, int(source_hour.get("overtimeActiveSeconds", 0)))
        overtime_seconds = max(0, raw_overtime_seconds - meeting_overlap_seconds)
        visual_seconds = idle_seconds + active_seconds + overtime_seconds

        if visual_seconds > 3600 - meeting_seconds:
            overflow_seconds = visual_seconds - max(0, 3600 - meeting_seconds)
            overtime_seconds = max(0, overtime_seconds - overflow_seconds)
            overflow_seconds = max(0, overflow_seconds - raw_overtime_seconds)
            active_seconds = max(0, active_seconds - overflow_seconds)

        consumed_hour["meetingSeconds"] = consumed_meeting_seconds + meeting_seconds
        meeting_segments = segments_after_consumed_seconds(
            _segments_for_kind((meeting_by_hour.get(hour, {}) or {}), "meeting"),
            consumed_meeting_seconds,
            meeting_seconds,
        )

        hourly_activity.append(
            {
                "hour": hour,
                "activeSeconds": active_seconds,
                "idleSeconds": idle_seconds,
                "breakSeconds": break_seconds,
                "autoBreakSeconds": auto_break_seconds,
                "meetingSeconds": meeting_seconds,
                "overtimeActiveSeconds": overtime_seconds,
                INTERNAL_OVERTIME_FILL_SECONDS: int(source_hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)),
                "missedSeconds": int(source_hour.get("missedSeconds", 0)),
                INTERNAL_MISSED_START_SECONDS: int(source_hour.get(INTERNAL_MISSED_START_SECONDS, 0)),
                INTERNAL_MISSED_END_SECONDS: int(source_hour.get(INTERNAL_MISSED_END_SECONDS, 0)),
                "telegramToFirstActivityIdleSeconds": int(source_hour.get("telegramToFirstActivityIdleSeconds", 0)),
                "workdayHourGapIdleSeconds": int(source_hour.get("workdayHourGapIdleSeconds", 0)),
                "pluginHourGapIdleSeconds": int(source_hour.get("pluginHourGapIdleSeconds", 0)),
                "offlineIdleGapSeconds": int(source_hour.get("offlineIdleGapSeconds", 0)),
                "overtimeBoundaryIdleSeconds": int(source_hour.get("overtimeBoundaryIdleSeconds", 0)),
                "fillSegments": _segments_for_kind(source_hour, "active")
                + _segments_for_kind(source_hour, "overtime")
                + _segments_for_kind(source_hour, "afk")
                + _segments_for_kind(source_hour, "auto-afk")
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
            idle_reduction = min(int(target_hour.get("idleSeconds", 0)), added_meeting_seconds)
            if idle_reduction > 0:
                target_hour["idleSeconds"] = int(target_hour.get("idleSeconds", 0)) - idle_reduction
                adjustment["idleSeconds"] += idle_reduction

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


def fill_overtime_hours_between_overtime_buckets(
    hourly_activity: list[dict[str, Any]],
    *,
    start_hour: int | None = None,
    end_hour: int | None = None,
) -> None:
    overtime_hour_indexes = [
        index
        for index, hour in enumerate(hourly_activity)
        if (start_hour is None or index >= start_hour)
        and (end_hour is None or index <= end_hour)
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


def fill_visual_overtime_hour(hour: dict[str, Any], replace_visual_idle: bool = False, preserve_visual_missed: bool = False) -> None:
    visual_idle_seconds = max(0, int(hour.get("idleSeconds", 0))) if replace_visual_idle else 0
    overtime_seconds = visual_hour_available_seconds(hour) + visual_idle_seconds

    if preserve_visual_missed:
        overtime_seconds = max(0, overtime_seconds - int(hour.get("missedSeconds", 0)))

    if overtime_seconds <= 0:
        return

    if visual_idle_seconds > 0:
        hour["idleSeconds"] = 0
        hour["idleMicroseconds"] = 0

    hour[INTERNAL_OVERTIME_FILL_SECONDS] = int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) + overtime_seconds
    _append_available_seconds(hour.setdefault("fillSegments", []), "overtime-fill", overtime_seconds)

    if not preserve_visual_missed:
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
            start_second = _second_of_hour(cursor)
            end_second = _second_of_hour(segment_end, hour_end)
            seconds = max(0, end_second - start_second)
            target[cursor.hour][seconds_key] = int(target[cursor.hour].get(seconds_key, 0)) + seconds
            _add_fill_segment(target[cursor.hour], kind, start_second, end_second)

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

    for segment in normalize_hour_fill(segments, apply_stack_rules=False):
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


def _telegram_idle_segments(hour: dict[str, Any]) -> list[dict[str, Any]]:
    target_seconds = min(
        time_seconds(hour, "idleSeconds", "idleMicroseconds"),
        max(0, int(hour.get("telegramToFirstActivityIdleSeconds", 0))),
    )

    if target_seconds <= 0:
        return []

    source_segments = _segments_for_kind(hour, "idle")

    if not source_segments:
        return [{"kind": "telegram-idle", "startSecond": 0, "endSecond": min(3600, target_seconds)}]

    telegram_segments: list[dict[str, Any]] = []
    remaining_seconds = target_seconds

    for segment in source_segments:
        if remaining_seconds <= 0:
            break

        start_second = int(segment["startSecond"])
        end_second = int(segment["endSecond"])
        segment_seconds = min(remaining_seconds, end_second - start_second)
        if segment_seconds <= 0:
            continue

        telegram_segments.append(
            {"kind": "telegram-idle", "startSecond": start_second, "endSecond": start_second + segment_seconds}
        )
        remaining_seconds -= segment_seconds

    return telegram_segments


def _visible_generic_idle_seconds(hour: dict[str, Any], telegram_idle_seconds: int) -> int:
    idle_seconds = time_seconds(hour, "idleSeconds", "idleMicroseconds")
    generic_idle_seconds = max(0, idle_seconds - max(0, int(telegram_idle_seconds)))

    if telegram_idle_seconds <= 0 or generic_idle_seconds <= 0:
        return generic_idle_seconds

    synthetic_after_telegram_seconds = (
        max(0, int(hour.get("offlineIdleGapSeconds", 0)))
        + max(0, int(hour.get("overtimeBoundaryIdleSeconds", 0)))
    )

    return max(0, generic_idle_seconds - synthetic_after_telegram_seconds)


def _stacked_segments(kind: str, seconds: int, cursor: int) -> list[dict[str, Any]]:
    seconds = max(0, int(seconds))
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


def _segments_total_seconds(segments: list[dict[str, Any]]) -> int:
    return sum(int(segment["endSecond"]) - int(segment["startSecond"]) for segment in segments)


def _segments_from_timeline(timeline: list[str | None]) -> list[dict[str, Any]]:
    segments = []
    cursor = 0

    while cursor < min(3600, len(timeline)):
        kind = timeline[cursor]

        if kind is None:
            cursor += 1
            continue

        end_second = cursor + 1
        while end_second < min(3600, len(timeline)) and timeline[end_second] == kind:
            end_second += 1

        segments.append({"kind": kind, "startSecond": cursor, "endSecond": end_second})
        cursor = end_second

    return segments


def _allowed_kinds_for_hour(hour: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()

    if time_seconds(hour, "activeSeconds", "activeMicroseconds") > 0:
        allowed.add("active")
    if time_seconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds") > 0 or int(hour.get(INTERNAL_OVERTIME_FILL_SECONDS, 0)) > 0:
        allowed.add("overtime")
        allowed.add("overtime-fill")
    if int(hour.get("breakSeconds", 0)) > 0:
        allowed.add("afk")
    if int(hour.get("autoBreakSeconds", 0)) > 0:
        allowed.add("auto-afk")
    if int(hour.get("meetingSeconds", 0)) > 0:
        allowed.add("meeting")
    if time_seconds(hour, "idleSeconds", "idleMicroseconds") > 0:
        allowed.add("idle")
    if int(hour.get("telegramToFirstActivityIdleSeconds", 0)) > 0:
        allowed.add("telegram-idle")
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
