from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from ..activity_math import (
    TELEGRAM_DAY_REMINDER_SECONDS,
    dt,
    _coerce_datetime,
    _telegram_event_date,
    _valid_time_zone_id,
)
from ..mongo_composable import MongoComposableMixin
from ..overtime_rules import NIGHT_OVERTIME_END_HOUR, NIGHT_OVERTIME_START_HOUR


class BreakSessionService(MongoComposableMixin):
    def _break_session_expiry(self, session: dict[str, Any]) -> dt.datetime | None:
        stored_expiry = _coerce_datetime(session.get("expiresAt"))

        if stored_expiry:
            return stored_expiry

        started_at = _coerce_datetime(session.get("startedAt"))

        if not started_at:
            return None

        raw_author = str(session.get("rawAuthor") or "")
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        session_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
        zone = ZoneInfo(time_zone_id)
        local_date = dt.date.fromisoformat(session_date)
        next_midnight = dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time.min, zone).astimezone(dt.UTC)
        day_session = self.db.day_sessions.find_one(
            {"rawAuthor": raw_author, "date": session_date},
            {"_id": 0, "startedAt": 1},
        )
        day_started_at = _coerce_datetime((day_session or {}).get("startedAt"))

        if not day_started_at:
            return next_midnight

        return min(day_started_at + dt.timedelta(seconds=TELEGRAM_DAY_REMINDER_SECONDS), next_midnight)

    def _close_break_session(
        self,
        normalized_telegram: str,
        raw_author: str,
        event_time: dt.datetime,
        *,
        close_reason: str | None = None,
    ) -> dict[str, Any]:
        session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

        if not session:
            return {}

        expires_at = self._break_session_expiry(session)
        ended_at = min(event_time, expires_at) if expires_at else event_time
        claimed_session = self.db.break_sessions.find_one_and_delete(
            {"telegramUsername": normalized_telegram, "startedAt": session.get("startedAt")}
        )

        if not claimed_session:
            return {}

        started_at = _coerce_datetime(claimed_session["startedAt"]) or event_time
        time_zone_id = _valid_time_zone_id(claimed_session.get("timeZoneId")) or "UTC"
        break_segments = _non_night_break_segments(started_at, ended_at, time_zone_id)
        break_seconds = sum(segment["breakSeconds"] for segment in break_segments)
        raw_break_seconds = max(0, int((ended_at - started_at).total_seconds()))
        ignored_break_seconds = max(0, raw_break_seconds - break_seconds)
        expired_seconds = max(0, int((event_time - ended_at).total_seconds()))

        if expired_seconds and not _non_night_break_segments(ended_at, event_time, time_zone_id):
            ignored_break_seconds += expired_seconds

        effective_close_reason = close_reason or ("workday_end" if expires_at and event_time >= expires_at else None)

        now = dt.datetime.now(dt.UTC)
        for segment in break_segments:
            interval = {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "startedAt": segment["startedAt"],
                "endedAt": segment["endedAt"],
                "date": segment["date"],
                "timeZoneId": time_zone_id,
                "breakSeconds": segment["breakSeconds"],
            }

            if effective_close_reason:
                interval["metadata"] = {"reason": effective_close_reason}

            self.db.break_intervals.insert_one(interval)
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
