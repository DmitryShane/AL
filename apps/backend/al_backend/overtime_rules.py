from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NIGHT_OVERTIME_START_HOUR = 0
NIGHT_OVERTIME_END_HOUR = 6
WINDOWS_TIME_ZONE_IDS = {
    "FLE Standard Time": "Europe/Sofia",
    "FLE Daylight Time": "Europe/Sofia",
}

OvertimeWindow = tuple[dt.datetime, dt.datetime]


@dataclass(frozen=True)
class OvertimeRuleContext:
    vacation_overtime_window_for_event: Callable[[dict[str, Any]], OvertimeWindow | None]
    is_author_offline_after_latest_telegram_state: Callable[[str, str, dt.datetime], bool]
    day_session_for_author_date: Callable[[str, str], dict[str, Any] | None]


def overtime_window_for_event(event: dict[str, Any], context: OvertimeRuleContext) -> OvertimeWindow | None:
    raw_author, day_date, event_time = _event_author_date_time(event)

    if not raw_author or not day_date or not event_time:
        return None

    vacation_window = is_vacation_overtime_window(event, context)

    if vacation_window:
        return vacation_window

    night_window = is_night_overtime_window(event)

    if night_window:
        return night_window

    return is_telegram_overtime_window(event, context)


def overtime_window_for_interval(event: dict[str, Any], start: dt.datetime, end: dt.datetime, context: OvertimeRuleContext) -> OvertimeWindow | None:
    if end <= start:
        return None

    event_window = overtime_window_for_event(event, context)

    if event_window and _windows_overlap(start, end, event_window[0], event_window[1]):
        return event_window

    raw_author, day_date, _event_time = _event_author_date_time(event)

    if not raw_author or not day_date:
        return None

    probe = dict(event)
    probe["occurredAtUtc"] = start
    interval_start_window = is_night_overtime_window(probe)

    if interval_start_window and _windows_overlap(start, end, interval_start_window[0], interval_start_window[1]):
        return interval_start_window

    return None


def is_vacation_overtime_window(event: dict[str, Any], context: OvertimeRuleContext) -> OvertimeWindow | None:
    return context.vacation_overtime_window_for_event(event)


def is_night_overtime_window(event: dict[str, Any]) -> OvertimeWindow | None:
    raw_author, day_date, event_time = _event_author_date_time(event)

    if not raw_author or not day_date or not event_time:
        return None

    occurred_local = _parse_local_datetime(event.get("occurredAtLocal"))
    time_zone_id = _valid_time_zone_id(event.get("timeZoneId"))

    try:
        day = dt.date.fromisoformat(day_date)
    except ValueError:
        return None

    if time_zone_id:
        zone = ZoneInfo(time_zone_id)
        night_start_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), zone)
        night_end_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), zone)
    elif occurred_local and occurred_local.tzinfo:
        night_start_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), occurred_local.tzinfo)
        night_end_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), occurred_local.tzinfo)
    else:
        zone = ZoneInfo("UTC")
        night_start_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), zone)
        night_end_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), zone)

    night_start_at = night_start_local.astimezone(dt.UTC)
    night_end_at = night_end_local.astimezone(dt.UTC)

    if event_time < night_start_at or event_time >= night_end_at:
        return None

    return night_start_at, night_end_at


def is_telegram_overtime_window(event: dict[str, Any], context: OvertimeRuleContext) -> OvertimeWindow | None:
    raw_author, day_date, event_time = _event_author_date_time(event)

    if not raw_author or not day_date or not event_time:
        return None

    if not context.is_author_offline_after_latest_telegram_state(raw_author, day_date, event_time):
        return None

    day_session = context.day_session_for_author_date(raw_author, day_date) or {}
    overtime_started_at = _coerce_datetime(day_session.get("lastOfflineAt"))

    if not overtime_started_at:
        return None

    time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or _valid_time_zone_id(day_session.get("timeZoneId")) or "UTC"

    try:
        day = dt.date.fromisoformat(day_date)
        day_end_local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, ZoneInfo(time_zone_id))
    except ValueError:
        return None

    day_end_at = day_end_local.astimezone(dt.UTC)

    if event_time < overtime_started_at or event_time >= day_end_at:
        return None

    return overtime_started_at, day_end_at


def _event_author_date_time(event: dict[str, Any]) -> tuple[str, str, dt.datetime | None]:
    raw_author = str(event.get("author") or "Unknown User")
    day_date = str(event.get("date") or "")
    event_time = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAt"))
    return raw_author, day_date, event_time


def _windows_overlap(start: dt.datetime, end: dt.datetime, window_start: dt.datetime, window_end: dt.datetime) -> bool:
    return max(start, window_start) < min(end, window_end)


def _coerce_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)

        return value.astimezone(dt.UTC)

    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.UTC)

        return parsed.astimezone(dt.UTC)

    return None


def _parse_local_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value

    if isinstance(value, str) and value:
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return None


def _valid_time_zone_id(value: Any) -> str | None:
    normalized = str(value or "").strip()

    if not normalized:
        return None

    normalized = WINDOWS_TIME_ZONE_IDS.get(normalized, normalized)

    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return None

    return normalized
