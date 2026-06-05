from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from ..activity_math import dt, _coerce_datetime, _telegram_event_date, _valid_time_zone_id
from ..mongo_composable import MongoComposableMixin
from ..overtime_rules import NIGHT_OVERTIME_END_HOUR, NIGHT_OVERTIME_START_HOUR


class BreakSessionService(MongoComposableMixin):
    def _close_break_session(self, normalized_telegram: str, raw_author: str, event_time: dt.datetime) -> dict[str, Any]:
        session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

        if not session:
            return {}

        started_at = _coerce_datetime(session["startedAt"]) or event_time
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        break_segments = _non_night_break_segments(started_at, event_time, time_zone_id)
        break_seconds = sum(segment["breakSeconds"] for segment in break_segments)
        raw_break_seconds = max(0, int((event_time - started_at).total_seconds()))
        ignored_break_seconds = max(0, raw_break_seconds - break_seconds)
        self.db.break_sessions.delete_one({"telegramUsername": normalized_telegram})

        now = dt.datetime.now(dt.UTC)
        for segment in break_segments:
            self.db.break_intervals.insert_one(
                {
                    "telegramUsername": normalized_telegram,
                    "rawAuthor": raw_author,
                    "startedAt": segment["startedAt"],
                    "endedAt": segment["endedAt"],
                    "date": segment["date"],
                    "timeZoneId": time_zone_id,
                    "breakSeconds": segment["breakSeconds"],
                }
            )
            self.db.daily_author_activity.update_many(
                {"author": raw_author, "date": segment["date"]},
                {"$inc": {"breakSeconds": segment["breakSeconds"]}, "$set": {"updatedAt": now}},
            )

        result = {"breakSeconds": break_seconds}

        if ignored_break_seconds:
            result["ignoredBreakSeconds"] = ignored_break_seconds
            result["ignoreReason"] = "night_overtime"

        return result


def _non_night_break_segments(started_at: dt.datetime, ended_at: dt.datetime, time_zone_id: str) -> list[dict[str, Any]]:
    if ended_at <= started_at:
        return []

    zone = ZoneInfo(time_zone_id)
    cursor_local = started_at.astimezone(zone)
    end_local = ended_at.astimezone(zone)
    segments: list[dict[str, Any]] = []

    while cursor_local < end_local:
        day = cursor_local.date()
        next_midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, zone)
        segment_end_local = min(end_local, next_midnight)
        night_start_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), zone)
        night_end_local = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), zone)

        for part_start_local, part_end_local in _subtract_window(cursor_local, segment_end_local, night_start_local, night_end_local):
            part_start = part_start_local.astimezone(dt.UTC)
            part_end = part_end_local.astimezone(dt.UTC)
            break_seconds = max(0, int((part_end - part_start).total_seconds()))

            if break_seconds <= 0:
                continue

            segments.append(
                {
                    "startedAt": part_start,
                    "endedAt": part_end,
                    "date": _telegram_event_date(part_start, time_zone_id),
                    "breakSeconds": break_seconds,
                }
            )

        cursor_local = segment_end_local

    return segments


def _subtract_window(
    start: dt.datetime,
    end: dt.datetime,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime]]:
    if end <= start:
        return []

    overlap_start = max(start, window_start)
    overlap_end = min(end, window_end)

    if overlap_start >= overlap_end:
        return [(start, end)]

    parts: list[tuple[dt.datetime, dt.datetime]] = []

    if start < overlap_start:
        parts.append((start, overlap_start))

    if overlap_end < end:
        parts.append((overlap_end, end))

    return parts
