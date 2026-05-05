from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from ..activity_math import (
    MICROSECONDS_PER_SECOND,
    _author_time_zone_id,
    _coerce_datetime,
    _seconds_from_microseconds,
    _time_microseconds,
    _valid_time_zone_id,
)
from ..mongo_composable import MongoComposableMixin


VACATION_REASON_ID = "vacation"


class CalendarDayOverrideService(MongoComposableMixin):
    def vacation_mark_for_author_date(self, raw_author: str, day_date: str) -> dict[str, Any] | None:
        if not raw_author or not day_date:
            return None

        mark = self.db.calendar_marks.find_one(
            {"rawAuthor": raw_author, "date": day_date, "reasonId": VACATION_REASON_ID},
            {"_id": 0},
        )

        if not mark:
            return None

        return {
            "type": "vacation",
            "reasonId": VACATION_REASON_ID,
            "reasonLabel": "Vacation",
            "label": "Vacation",
            "date": day_date,
            "note": str(mark.get("note") or ""),
        }

    def is_vacation_day(self, raw_author: str, day_date: str) -> bool:
        return self.vacation_mark_for_author_date(raw_author, day_date) is not None

    def vacation_overtime_window_for_event(self, event: dict[str, Any]) -> tuple[dt.datetime, dt.datetime] | None:
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")
        event_time = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAt"))

        return self.vacation_overtime_window(raw_author, day_date, event_time, event.get("timeZoneId"))

    def vacation_overtime_window(
        self,
        raw_author: str,
        day_date: str,
        occurred_at: dt.datetime | None,
        time_zone_id: Any = None,
    ) -> tuple[dt.datetime, dt.datetime] | None:
        if not raw_author or not day_date or not occurred_at:
            return None

        if not self.is_vacation_day(raw_author, day_date):
            return None

        profiles = self._profiles_by_raw_author() if hasattr(self, "_profiles_by_raw_author") else {}
        zone_id = _valid_time_zone_id(time_zone_id) or _author_time_zone_id(raw_author, profiles, None)

        try:
            day = dt.date.fromisoformat(day_date)
            day_start_local = dt.datetime.combine(day, dt.time.min, ZoneInfo(zone_id))
            day_end_local = day_start_local + dt.timedelta(days=1)
        except ValueError:
            return None

        day_start_at = day_start_local.astimezone(dt.UTC)
        day_end_at = day_end_local.astimezone(dt.UTC)

        if occurred_at < day_start_at or occurred_at >= day_end_at:
            return None

        return day_start_at, day_end_at

    def should_suppress_vacation_prompt(self, raw_author: str, day_date: str) -> bool:
        return self.is_vacation_day(raw_author, day_date)

    def apply_vacation_mark_to_author(self, author: dict[str, Any], day_date: str) -> None:
        mark = self.vacation_mark_for_author_date(str(author.get("rawAuthor") or "Unknown User"), day_date)

        if mark:
            author["dayOverride"] = mark
            author["calendarDayMark"] = mark

    def convert_deltas_to_vacation_overtime(self, deltas: dict[str, Any]) -> dict[str, Any]:
        converted = dict(deltas)
        active_microseconds = _time_microseconds(converted, "activeDeltaSeconds", "activeDeltaMicroseconds")
        meeting_microseconds = int(converted.get("meetingMicroseconds", 0)) or int(converted.get("meetingSeconds", 0)) * MICROSECONDS_PER_SECOND
        overtime_microseconds = _time_microseconds(
            converted, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"
        )
        total_overtime_microseconds = overtime_microseconds + active_microseconds + meeting_microseconds

        converted["activeDeltaMicroseconds"] = 0
        converted["activeDeltaSeconds"] = 0
        converted["idleDeltaMicroseconds"] = 0
        converted["idleDeltaSeconds"] = 0
        converted["breakDeltaSeconds"] = 0
        converted["meetingMicroseconds"] = 0
        converted["meetingSeconds"] = 0
        converted["overtimeActiveDeltaMicroseconds"] = total_overtime_microseconds
        converted["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(total_overtime_microseconds)
        converted["hourlyActivityDelta"] = self.convert_hourly_to_vacation_overtime(converted.get("hourlyActivityDelta", []))

        if converted.get("activityCountDeltas"):
            converted["overtimeActivityCountDeltas"] = converted.get("overtimeActivityCountDeltas", []) + converted.get(
                "activityCountDeltas", []
            )
            converted["activityCountDeltas"] = []

        if converted.get("savedPrefabDeltas"):
            converted["overtimeSavedPrefabDeltas"] = converted.get("overtimeSavedPrefabDeltas", []) + converted.get(
                "savedPrefabDeltas", []
            )
            converted["savedPrefabDeltas"] = []

        return converted

    def convert_hourly_to_vacation_overtime(self, hourly_activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []

        for source_hour in hourly_activity:
            hour = dict(source_hour)
            active_seconds = int(hour.get("activeSeconds", 0))
            meeting_seconds = int(hour.get("meetingSeconds", 0))
            overtime_seconds = int(hour.get("overtimeActiveSeconds", 0))
            overtime_microseconds = _time_microseconds(hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds")
            overtime_microseconds += (active_seconds + meeting_seconds) * MICROSECONDS_PER_SECOND

            hour["activeSeconds"] = 0
            hour["activeMicroseconds"] = 0
            hour["idleSeconds"] = 0
            hour["idleMicroseconds"] = 0
            hour["breakSeconds"] = 0
            hour["meetingSeconds"] = 0
            hour["missedSeconds"] = 0
            hour["missedStartSeconds"] = 0
            hour["missedEndSeconds"] = 0

            if overtime_seconds > 0 or overtime_microseconds > 0:
                hour["overtimeActiveMicroseconds"] = overtime_microseconds
                hour["overtimeActiveSeconds"] = _seconds_from_microseconds(overtime_microseconds)
            else:
                hour["overtimeActiveSeconds"] = 0
                hour["overtimeActiveMicroseconds"] = 0

            converted.append(hour)

        return converted
