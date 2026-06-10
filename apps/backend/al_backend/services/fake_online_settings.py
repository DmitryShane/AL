from __future__ import annotations

import random
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from ..activity_math import *
from ..backend_composable_host import composed
from .settings_time import WEEKDAY_VALUES, parse_settings_time_minutes


DEFAULT_FAKE_ONLINE_SETTINGS = {
    "enabled": False,
    "daysOfWeek": [],
    "startTime": "10:00",
    "endTime": "12:00",
    "delayMinSeconds": 5,
    "delayMaxSeconds": 60,
}


def _fake_online_settings_row(profile: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    raw_author = str(profile.get("rawAuthor") or saved.get("rawAuthor") or "")
    telegram_username = _normalize_telegram_username(profile.get("telegramUsername") or saved.get("telegramUsername"))
    return {
        **DEFAULT_FAKE_ONLINE_SETTINGS,
        **{key: saved.get(key) for key in DEFAULT_FAKE_ONLINE_SETTINGS if key in saved},
        "rawAuthor": raw_author,
        "displayName": profile.get("displayName") or raw_author,
        "authorEmail": profile.get("authorEmail") or "",
        "telegramUsername": telegram_username,
        "timeZoneId": profile.get("timeZoneId") or saved.get("timeZoneId") or "UTC",
        "timeZoneDisplayName": profile.get("timeZoneDisplayName") or profile.get("timeZoneId") or saved.get("timeZoneId") or "UTC",
        "canEnable": bool(telegram_username),
    }


class FakeOnlineSettingsMixin:
    def fake_online_settings(self) -> dict[str, Any]:
        settings_by_author = {
            str(item.get("rawAuthor") or ""): item
            for item in self.db.fake_online_settings.find({}, {"_id": 0})
        }
        profiles_by_author = {str(profile.get("rawAuthor") or ""): profile for profile in self.author_profiles()}
        rows: list[dict[str, Any]] = []
        available_profiles: list[dict[str, Any]] = []

        for raw_author, saved in settings_by_author.items():
            profile = profiles_by_author.get(raw_author)

            if not profile:
                continue

            rows.append(_fake_online_settings_row(profile, saved))

        configured_authors = {row["rawAuthor"] for row in rows}
        for raw_author, profile in profiles_by_author.items():
            if raw_author in configured_authors:
                continue

            telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
            available_profiles.append(
                {
                    "rawAuthor": raw_author,
                    "displayName": profile.get("displayName") or raw_author,
                    "authorEmail": profile.get("authorEmail") or "",
                    "telegramUsername": telegram_username,
                    "canEnable": bool(telegram_username),
                }
            )

        return {"settings": rows, "availableProfiles": available_profiles}

    def _fake_online_default_row_for_author(self, raw_author: str) -> dict[str, Any] | None:
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0})

        if not profile:
            return None

        return _fake_online_settings_row(profile, {})

    def delete_fake_online_settings(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)
        self.db.fake_online_settings.delete_one({"rawAuthor": normalized_author})
        self.db.fake_online_attempts.update_many(
            {"rawAuthor": normalized_author, "status": {"$in": ["pending", "claimed"]}},
            {
                "$set": {
                    "status": "closed",
                    "closeAction": "removed_from_fake_online",
                    "closedAt": dt.datetime.now(dt.UTC),
                    "updatedAt": dt.datetime.now(dt.UTC),
                }
            },
        )
        return {"ok": True, **self.fake_online_settings()}

    def upsert_fake_online_settings(
        self,
        raw_author: str,
        enabled: bool,
        days_of_week: list[int],
        start_time: str,
        end_time: str,
        delay_min_seconds: int,
        delay_max_seconds: int,
    ) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)
        profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0})

        if not profile:
            return {"ok": False, "error": "Author profile not found"}

        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
        if not telegram_username:
            return {"ok": False, "error": "Author profile must have a Telegram username"}

        normalized_days = sorted({int(day) for day in days_of_week if int(day) in WEEKDAY_VALUES})
        if enabled and not normalized_days:
            return {"ok": False, "error": "Select at least one weekday"}

        if parse_settings_time_minutes(start_time) >= parse_settings_time_minutes(end_time):
            return {"ok": False, "error": "Start time must be before end time"}

        min_delay = max(0, int(delay_min_seconds))
        max_delay = max(0, int(delay_max_seconds))
        if min_delay > max_delay:
            return {"ok": False, "error": "Minimum delay must be less than or equal to maximum delay"}

        now = dt.datetime.now(dt.UTC)
        self.db.fake_online_settings.update_one(
            {"rawAuthor": normalized_author},
            {
                "$set": {
                    "rawAuthor": normalized_author,
                    "enabled": bool(enabled),
                    "daysOfWeek": normalized_days,
                    "startTime": start_time,
                    "endTime": end_time,
                    "delayMinSeconds": min_delay,
                    "delayMaxSeconds": max_delay,
                    "telegramUsername": telegram_username,
                    "timeZoneId": profile.get("timeZoneId") or "UTC",
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        return {"ok": True, **self.fake_online_settings()}

    def claim_due_fake_online_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        due: list[dict[str, Any]] = []

        for setting in list(self.db.fake_online_settings.find({"enabled": True}, {"_id": 0})):
            attempt = self._ensure_today_fake_online_attempt(setting, now)
            if not attempt or attempt.get("status") != "pending":
                continue

            scheduled_at = _coerce_datetime(attempt.get("scheduledPromptAt"))
            if not scheduled_at or scheduled_at > now:
                continue

            raw_author = str(attempt.get("rawAuthor") or "")
            local_date = str(attempt.get("localDate") or "")
            if composed(self).should_suppress_vacation_prompt(raw_author, local_date):
                self.db.fake_online_attempts.update_one(
                    {"attemptId": attempt.get("attemptId")},
                    {"$set": {"status": "closed", "closeAction": "skipped_vacation_day", "closedAt": now, "updatedAt": now}},
                )
                continue

            if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": local_date}, {"_id": 1}):
                self.db.fake_online_attempts.update_one(
                    {"attemptId": attempt.get("attemptId")},
                    {"$set": {"status": "closed", "closeAction": "skipped_day_already_open", "closedAt": now, "updatedAt": now}},
                )
                continue

            prompt = self._create_fake_online_prompt_for_attempt(attempt, now)
            if not prompt:
                continue

            self.db.fake_online_attempts.update_one(
                {"attemptId": attempt.get("attemptId")},
                {
                    "$set": {
                        "status": "claimed",
                        "telegramPromptId": prompt["reminderId"],
                        "lastClaimedAt": now,
                        "updatedAt": now,
                    }
                },
            )
            due.append(prompt)

        return due

    def _ensure_today_fake_online_attempt(self, setting: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
        raw_author = str(setting.get("rawAuthor") or "")
        if not raw_author:
            return None

        profile = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author},
            {"_id": 0, "telegramUsername": 1, "timeZoneId": 1},
        ) or {}
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername") or setting.get("telegramUsername"))
        if not telegram_username:
            return None

        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId") or setting.get("timeZoneId")) or "UTC"
        local_now = now.astimezone(ZoneInfo(time_zone_id))
        local_date = local_now.date().isoformat()
        existing = self.db.fake_online_attempts.find_one({"rawAuthor": raw_author, "localDate": local_date}, {"_id": 0})
        if existing:
            return existing

        days = {int(day) for day in setting.get("daysOfWeek", []) if int(day) in WEEKDAY_VALUES}
        if local_now.weekday() not in days:
            return None

        if composed(self).should_suppress_vacation_prompt(raw_author, local_date):
            return None

        start_minutes = parse_settings_time_minutes(str(setting.get("startTime") or DEFAULT_FAKE_ONLINE_SETTINGS["startTime"]))
        end_minutes = parse_settings_time_minutes(str(setting.get("endTime") or DEFAULT_FAKE_ONLINE_SETTINGS["endTime"]))
        if start_minutes >= end_minutes:
            return None

        start_local = local_now.replace(hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0)
        end_local = local_now.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0)

        window_seconds = max(0, int((end_local - start_local).total_seconds()))
        scheduled_local = start_local + dt.timedelta(seconds=random.randint(0, window_seconds))
        delay_min = max(0, int(setting.get("delayMinSeconds", DEFAULT_FAKE_ONLINE_SETTINGS["delayMinSeconds"])))
        delay_max = max(delay_min, int(setting.get("delayMaxSeconds", DEFAULT_FAKE_ONLINE_SETTINGS["delayMaxSeconds"])))
        now_utc = dt.datetime.now(dt.UTC)
        attempt = {
            "attemptId": _new_id(),
            "rawAuthor": raw_author,
            "localDate": local_date,
            "telegramUsername": telegram_username,
            "timeZoneId": time_zone_id,
            "scheduledPromptAt": scheduled_local.astimezone(dt.UTC),
            "autoConfirmDelaySeconds": random.randint(delay_min, delay_max),
            "status": "pending",
            "createdAt": now_utc,
            "updatedAt": now_utc,
        }
        self.db.fake_online_attempts.insert_one(attempt)
        return {k: v for k, v in attempt.items() if k != "_id"}

    def _create_fake_online_prompt_for_attempt(self, attempt: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
        raw_author = str(attempt.get("rawAuthor") or "")
        local_date = str(attempt.get("localDate") or "")
        reminder_id = _new_id()
        first_report_received_at = _coerce_datetime(attempt.get("scheduledPromptAt")) + dt.timedelta(minutes=1)
        try:
            self.db.telegram_online_prompts.insert_one(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "date": local_date,
                    "telegramUsername": str(attempt.get("telegramUsername") or ""),
                    "firstReportReceivedAt": first_report_received_at,
                    "status": "claimed",
                    "source": "fake_online",
                    "fakeOnlineAttemptId": attempt.get("attemptId"),
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
        except DuplicateKeyError:
            self.db.fake_online_attempts.update_one(
                {"attemptId": attempt.get("attemptId")},
                {"$set": {"status": "closed", "closeAction": "skipped_existing_online_prompt", "closedAt": now, "updatedAt": now}},
            )
            return None

        return {
            "reminderId": reminder_id,
            "rawAuthor": raw_author,
            "telegramUsername": str(attempt.get("telegramUsername") or ""),
            "date": local_date,
            "firstReportReceivedAt": first_report_received_at.isoformat(),
            "fakeOnlineAttemptId": str(attempt.get("attemptId") or ""),
            "autoConfirmDelaySeconds": int(attempt.get("autoConfirmDelaySeconds") or 0),
        }
