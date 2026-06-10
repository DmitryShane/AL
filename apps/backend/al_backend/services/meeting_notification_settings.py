from __future__ import annotations

from ..activity_math import *
from .settings_time import WEEKDAY_VALUES, parse_settings_time_minutes


DEFAULT_MEETING_NOTIFICATION_SETTINGS = {
    "enabled": False,
    "authorRawAuthors": [],
    "time": "10:00",
    "timeZoneId": "UTC",
    "daysOfWeek": [0, 1, 2, 3, 4],
}


class MeetingNotificationSettingsMixin:
    def get_meeting_notification_settings(self) -> dict[str, Any]:
        settings = self.db.system_settings.find_one({"kind": "meeting_notification"}, {"_id": 0}) or {}
        configured = bool(settings)
        time_value = str(settings.get("time") or DEFAULT_MEETING_NOTIFICATION_SETTINGS["time"])

        try:
            parse_settings_time_minutes(time_value)
        except ValueError:
            time_value = DEFAULT_MEETING_NOTIFICATION_SETTINGS["time"]

        time_zone_id = _valid_time_zone_id(settings.get("timeZoneId")) or DEFAULT_MEETING_NOTIFICATION_SETTINGS["timeZoneId"]
        days = sorted({int(day) for day in settings.get("daysOfWeek", DEFAULT_MEETING_NOTIFICATION_SETTINGS["daysOfWeek"]) if int(day) in WEEKDAY_VALUES})
        author_raw_authors = [
            _normalize_author(raw_author)
            for raw_author in settings.get("authorRawAuthors", DEFAULT_MEETING_NOTIFICATION_SETTINGS["authorRawAuthors"])
            if _normalize_author(raw_author)
        ]

        return {
            "configured": configured,
            "enabled": bool(settings.get("enabled", DEFAULT_MEETING_NOTIFICATION_SETTINGS["enabled"])),
            "authorRawAuthors": list(dict.fromkeys(author_raw_authors)),
            "time": time_value,
            "timeZoneId": time_zone_id,
            "daysOfWeek": days or list(DEFAULT_MEETING_NOTIFICATION_SETTINGS["daysOfWeek"]),
        }

    def upsert_meeting_notification_settings(
        self,
        *,
        enabled: bool,
        author_raw_authors: list[str],
        time: str,
        time_zone_id: str,
        days_of_week: list[int],
    ) -> dict[str, Any]:
        try:
            parse_settings_time_minutes(time)
        except ValueError as exc:
            raise ValueError("Time must use HH:mm format") from exc

        normalized_time_zone_id = _valid_time_zone_id(time_zone_id)
        if not normalized_time_zone_id:
            raise ValueError("Unknown timezone")

        normalized_days = sorted({int(day) for day in days_of_week if int(day) in WEEKDAY_VALUES})
        if enabled and not normalized_days:
            return {"ok": False, "error": "Select at least one weekday"}

        profiles_by_author = {str(profile.get("rawAuthor") or ""): profile for profile in self.author_profiles()}
        normalized_authors: list[str] = []

        for raw_author in author_raw_authors:
            normalized_author = _normalize_author(raw_author)
            if not normalized_author or normalized_author in normalized_authors:
                continue
            if normalized_author not in profiles_by_author:
                return {"ok": False, "error": f"Author profile not found: {normalized_author}"}
            normalized_authors.append(normalized_author)

        now = dt.datetime.now(dt.UTC)
        self.db.system_settings.update_one(
            {"kind": "meeting_notification"},
            {
                "$set": {
                    "kind": "meeting_notification",
                    "enabled": bool(enabled),
                    "authorRawAuthors": normalized_authors,
                    "time": time,
                    "timeZoneId": normalized_time_zone_id,
                    "daysOfWeek": normalized_days or list(DEFAULT_MEETING_NOTIFICATION_SETTINGS["daysOfWeek"]),
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        return {"ok": True, **self.get_meeting_notification_settings()}
