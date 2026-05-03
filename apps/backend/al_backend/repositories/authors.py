from __future__ import annotations

from ..activity_math import *


class AuthorRepositoryMixin:
    def list_authors(self) -> list[str]:
        alias_sources = {item.get("sourceRawAuthor") for item in self.author_aliases()}
        authors = set()

        for author in self.db.activity_snapshots.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.daily_author_activity.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.raw_activity_events.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.author_profiles.distinct("rawAuthor"):
            if author and author not in alias_sources:
                authors.add(author)

        return sorted(authors)

    def author_profiles(self) -> list[dict[str, Any]]:
        known_authors = self.list_authors()
        profiles = self._profiles_by_raw_author()
        result = []

        for raw_author in known_authors:
            profile = profiles.get(raw_author, {})
            author_activity = self.db.daily_author_activity.find_one(
                {"author": raw_author},
                {"_id": 0, "authorEmail": 1, "timeZoneId": 1, "timeZoneDisplayName": 1},
                sort=[("lastReceivedAt", DESCENDING)],
            )
            result.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or (author_activity or {}).get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "telegramPrivateChatId": profile.get("telegramPrivateChatId"),
                    "discordUserId": profile.get("discordUserId", ""),
                    "discordUsername": profile.get("discordUsername", ""),
                    "pluginEnabled": profile.get("pluginEnabled", True),
                    "autoBreakEnabled": profile.get("autoBreakEnabled", False),
                    "autoBreakEffectiveDate": profile.get("autoBreakEffectiveDate", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "timeZoneId": profile.get("timeZoneId") or (author_activity or {}).get("timeZoneId", ""),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName")
                    or (author_activity or {}).get("timeZoneDisplayName", ""),
                }
            )

        return result

    def update_author_email(self, raw_author: str, author_email: str | None) -> None:
        raw_author = _normalize_author(raw_author)
        normalized_email = (author_email or "").strip()

        if not raw_author or not normalized_email:
            return

        if "@" not in normalized_email:
            return

        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$set": {
                    "rawAuthor": raw_author,
                    "authorEmail": normalized_email,
                    "updatedAt": dt.datetime.now(dt.UTC),
                },
                "$setOnInsert": {
                    "displayName": raw_author,
                    "team": "",
                    "pluginEnabled": True,
                },
            },
            upsert=True,
        )

    def save_telegram_private_chat(self, telegram_username: str, chat_id: int) -> dict[str, Any]:
        normalized_username = _normalize_telegram_username(telegram_username)

        if not normalized_username:
            return {"ok": False, "error": "Telegram username is required"}

        result = self.db.author_profiles.update_one(
            {"telegramUsername": normalized_username},
            {
                "$set": {
                    "telegramPrivateChatId": int(chat_id),
                    "telegramPrivateChatUpdatedAt": dt.datetime.now(dt.UTC),
                }
            },
        )
        return {"ok": True, "matched": getattr(result, "matched_count", 0)}

    def update_author_time_zone(
        self, raw_author: str, time_zone_id: Any, time_zone_display_name: Any | None = None
    ) -> None:
        raw_author = _normalize_author(raw_author)
        normalized_time_zone = _author_configured_time_zone_id(raw_author) or _valid_time_zone_id(time_zone_id)

        if not raw_author or not normalized_time_zone:
            return

        display_name = str(time_zone_display_name or "").strip() or normalized_time_zone
        current = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author}, {"_id": 0, "timeZoneId": 1, "timeZoneDisplayName": 1}
        ) or {}
        previous_time_zone = _valid_time_zone_id(current.get("timeZoneId"))
        previous_display_name = str(current.get("timeZoneDisplayName") or "").strip()
        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$set": {
                    "rawAuthor": raw_author,
                    "timeZoneId": normalized_time_zone,
                    "timeZoneDisplayName": display_name,
                    "updatedAt": dt.datetime.now(dt.UTC),
                },
                "$setOnInsert": {
                    "displayName": raw_author,
                    "team": "",
                    "pluginEnabled": True,
                },
            },
            upsert=True,
        )

        if previous_time_zone != normalized_time_zone or previous_display_name != display_name:
            self._rebucket_author_telegram_time_zone(raw_author, normalized_time_zone, display_name)

    def _rebucket_author_telegram_time_zone(self, raw_author: str, time_zone_id: str, time_zone_display_name: str) -> None:
        for event in self.db.break_events.find({"rawAuthor": raw_author}, {"_id": 1, "timestamp": 1}):
            event_time = _coerce_datetime(event.get("timestamp"))

            if event_time:
                self.db.break_events.update_one(
                    _document_identity_query(event),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )

        for collection_name, time_field in (
            ("day_sessions", "startedAt"),
            ("break_sessions", "startedAt"),
            ("break_intervals", "startedAt"),
        ):
            collection = getattr(self.db, collection_name)

            for item in collection.find({"rawAuthor": raw_author}, {"_id": 1, time_field: 1}):
                event_time = _coerce_datetime(item.get(time_field))

                if event_time:
                    collection.update_one(
                        _document_identity_query(item),
                        {
                            "$set": {
                                "date": _telegram_event_date(event_time, time_zone_id),
                                "timeZoneId": time_zone_id,
                                "timeZoneDisplayName": time_zone_display_name,
                            }
                        },
                    )

        for row in self.db.report_rows.find({"source": "telegram", "author": raw_author}, {"_id": 1, "recordedAt": 1}):
            event_time = _coerce_datetime(row.get("recordedAt"))

            if event_time:
                self.db.report_rows.update_one(
                    _document_identity_query(row),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )

    def upsert_author_profile(
        self,
        raw_author: str,
        display_name: str | None,
        team: str | None,
        telegram_username: str | None,
        discord_user_id: str | None = None,
        discord_username: str | None = None,
        plugin_enabled: bool = True,
        auto_break_enabled: bool = False,
        author_color: str | None = None,
        time_zone_id: str | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        raw_author = _normalize_author(raw_author)
        existing_profile = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author}, {"_id": 0, "autoBreakEnabled": 1, "autoBreakEffectiveDate": 1, "timeZoneId": 1}
        ) or {}
        normalized_telegram = _normalize_telegram_username(telegram_username)
        normalized_discord_user_id = _normalize_discord_user_id(discord_user_id)
        normalized_discord_username = str(discord_username or "").strip()
        update = {
            "rawAuthor": raw_author,
            "displayName": (display_name or raw_author).strip(),
            "team": (team or "").strip(),
            "pluginEnabled": plugin_enabled,
            "autoBreakEnabled": auto_break_enabled,
            "authorColor": _valid_color(author_color) or _author_color(raw_author),
            "updatedAt": now,
        }
        normalized_time_zone = _valid_time_zone_id(time_zone_id)

        if normalized_time_zone:
            update["timeZoneId"] = normalized_time_zone

        existing_auto_break_enabled = bool(existing_profile.get("autoBreakEnabled", False))

        if auto_break_enabled:
            if existing_auto_break_enabled and existing_profile.get("autoBreakEffectiveDate"):
                update["autoBreakEffectiveDate"] = existing_profile.get("autoBreakEffectiveDate")
            else:
                update["autoBreakEffectiveDate"] = _next_author_local_date(
                    normalized_time_zone or existing_profile.get("timeZoneId")
                )

        operation: dict[str, Any] = {"$set": update}

        if normalized_telegram:
            update["telegramUsername"] = normalized_telegram
        else:
            operation["$unset"] = {"telegramUsername": ""}

        if normalized_discord_user_id:
            update["discordUserId"] = normalized_discord_user_id
        else:
            operation.setdefault("$unset", {})["discordUserId"] = ""

        if normalized_discord_username:
            update["discordUsername"] = normalized_discord_username
        else:
            operation.setdefault("$unset", {})["discordUsername"] = ""

        if not auto_break_enabled:
            operation.setdefault("$unset", {})["autoBreakEffectiveDate"] = ""

        self.db.author_profiles.update_one({"rawAuthor": raw_author}, operation, upsert=True)
        return {"ok": True, "profile": {k: v for k, v in update.items() if k != "updatedAt"}}

    def delete_author_data(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0, "telegramUsername": 1}) or {}
        raw_report_ids = set()

        for snapshot in self.db.activity_snapshots.find({"author": normalized_author}, {"rawReportId": 1}):
            if snapshot.get("rawReportId"):
                raw_report_ids.add(snapshot["rawReportId"])

        for batch in self.db.raw_event_batches.find({"author": normalized_author}, {"rawReportId": 1}):
            if batch.get("rawReportId"):
                raw_report_ids.add(batch["rawReportId"])

        state_key_pattern = f"(^|\\|){re.escape(normalized_author)}\\|"
        counts = {
            "rawReports": self.db.raw_reports.delete_many({"_id": {"$in": list(raw_report_ids)}}).deleted_count if raw_report_ids else 0,
            "activitySnapshots": self.db.activity_snapshots.delete_many({"author": normalized_author}).deleted_count,
            "rawEventBatches": self.db.raw_event_batches.delete_many({"author": normalized_author}).deleted_count,
            "rawActivityEvents": self.db.raw_activity_events.delete_many({"author": normalized_author}).deleted_count,
            "reportRows": self.db.report_rows.delete_many({"author": normalized_author}).deleted_count,
            "dailyAuthorActivity": self.db.daily_author_activity.delete_many({"author": normalized_author}).deleted_count,
            "aggregateSessionState": self.db.aggregate_session_state.delete_many({"_id": {"$regex": state_key_pattern}}).deleted_count,
            "reportSecurityEvents": self.db.report_security_events.delete_many({"author": normalized_author}).deleted_count,
            "reportRefreshRequests": self.db.report_refresh_requests.delete_many({"author": normalized_author}).deleted_count,
            "manualReportExpectations": self.db.manual_report_expectations.delete_many({"author": normalized_author}).deleted_count,
            "breakEvents": self.db.break_events.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "breakSessions": self.db.break_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "breakIntervals": self.db.break_intervals.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "daySessions": self.db.day_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "telegramDayReminders": self.db.telegram_day_reminders.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "telegramOnlinePrompts": self.db.telegram_online_prompts.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "telegramMeetingAutoAfkNotifications": self.db.telegram_meeting_auto_afk_notifications.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingEvents": self.db.meeting_events.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingSessions": self.db.meeting_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingIntervals": self.db.meeting_intervals.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingSummaries": self.db.meeting_summaries.delete_many({"participantNames": normalized_author}).deleted_count,
            "reportChallenges": self.db.report_challenges.delete_many({"author": normalized_author}).deleted_count,
        }

        telegram_username = profile.get("telegramUsername")

        if telegram_username:
            counts["breakEvents"] += self.db.break_events.delete_many({"telegramUsername": telegram_username}).deleted_count
            counts["breakSessions"] += self.db.break_sessions.delete_many({"telegramUsername": telegram_username}).deleted_count

        return {"ok": True, "author": normalized_author, "deleted": counts}

    def delete_author_profile(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        data_result = self.delete_author_data(normalized_author)
        counts = dict(data_result.get("deleted", {}))
        counts["authorProfiles"] = self.db.author_profiles.delete_many({"rawAuthor": normalized_author}).deleted_count
        counts["intervalSettings"] = self.db.interval_settings.delete_many({"kind": "author", "author": normalized_author}).deleted_count
        counts["calendarMarks"] = self.db.calendar_marks.delete_many({"rawAuthor": normalized_author}).deleted_count

        return {"ok": True, "author": normalized_author, "deleted": counts}


