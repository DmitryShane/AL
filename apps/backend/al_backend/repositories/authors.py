from __future__ import annotations

import datetime as dt

from ..activity_math import *
from ..author_avatar_cache import remove_author_avatar_cache_file
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin

_BULK_ACTIVITY_PRESET_DAY_SPAN = {"1d": 0, "2d": 1, "3d": 2, "week": 6, "month": 29}


def utc_inclusive_range_for_bulk_activity_preset(preset: str) -> tuple[str, str]:
    today = dt.datetime.now(dt.UTC).date()
    days_back = _BULK_ACTIVITY_PRESET_DAY_SPAN[preset]
    start = (today - dt.timedelta(days=days_back)).isoformat()
    return start, today.isoformat()


class AuthorRepository(MongoComposableMixin):
    def list_authors(self) -> list[str]:
        alias_sources = {item.get("sourceRawAuthor") for item in composed(self).author_aliases()}
        authors = set()

        for author in self.db.activity_snapshots.distinct("author"):
            if author:
                authors.add(composed(self).resolve_author_alias(author))

        for author in self.db.daily_author_activity.distinct("author"):
            if author:
                authors.add(composed(self).resolve_author_alias(author))

        for author in self.db.raw_activity_events.distinct("author"):
            if author:
                authors.add(composed(self).resolve_author_alias(author))

        for author in self.db.author_profiles.distinct("rawAuthor"):
            if author and author not in alias_sources:
                authors.add(author)

        return sorted(authors)

    def author_profiles(self) -> list[dict[str, Any]]:
        known_authors = self.list_authors()
        raw_devices = {
            str(item.get("rawAuthor") or "")
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1})
            if item.get("rawAuthor")
        }
        profiles = composed(self)._profiles_by_raw_author()
        profiles_by_normalized_key = {_normalize_author(k): v for k, v in profiles.items() if k}
        device_identities = {
            str(item.get("rawAuthor") or ""): item
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1, "deviceIdHash": 1})
            if item.get("rawAuthor")
        }
        result = []

        for raw_author in known_authors:
            if raw_author in raw_devices:
                continue

            profile = profiles.get(raw_author)
            if profile is None:
                profile = profiles_by_normalized_key.get(_normalize_author(raw_author), {})
            author_activity = self.db.daily_author_activity.find_one(
                {"author": raw_author},
                {"_id": 0, "authorEmail": 1, "timeZoneId": 1, "timeZoneDisplayName": 1},
                sort=[("lastReceivedAt", DESCENDING)],
            )
            device_identity = device_identities.get(raw_author)
            device_id = ""

            if device_identity:
                latest_device_batch = self.db.raw_event_batches.find_one(
                    {"author": raw_author, "source": "dev"},
                    {"_id": 0, "deviceId": 1},
                    sort=[("receivedAt", DESCENDING)],
                )
                latest_device_event = self.db.raw_activity_events.find_one(
                    {"author": raw_author, "source": "dev"},
                    {"_id": 0, "deviceId": 1},
                    sort=[("receivedAt", DESCENDING)],
                )
                device_id = str(
                    (latest_device_batch or {}).get("deviceId")
                    or (latest_device_event or {}).get("deviceId")
                    or device_identity.get("deviceIdHash")
                    or ""
                )

            gh_ui = _github_username_ui_default(raw_author, profile)
            gh_fetch = _github_username_for_avatar_fetch(raw_author, profile)
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
                    "githubUsername": gh_ui,
                    "avatarUrl": _cached_author_avatar_api_url(raw_author, gh_fetch, profile if isinstance(profile, dict) else None),
                    "deviceId": device_id,
                }
            )

        return result

    def update_author_email(self, raw_author: str, author_email: str | None) -> None:
        raw_author = composed(self).resolve_author_alias(_normalize_author(raw_author))
        normalized_email = (author_email or "").strip()

        if not raw_author or not normalized_email:
            return

        if "@" not in normalized_email:
            return

        existing_by_email = self.db.author_profiles.find_one(
            {"authorEmail": normalized_email},
            {"_id": 0, "rawAuthor": 1},
        )

        if existing_by_email:
            existing_author = composed(self).resolve_author_alias(existing_by_email.get("rawAuthor"))

            if existing_author and existing_author != raw_author:
                self.db.report_security_events.insert_one(
                    {
                        "eventType": "author_email_conflict",
                        "source": "",
                        "pluginVersion": "",
                        "author": raw_author,
                        "message": f"Author email is already assigned to {existing_author}.",
                        "createdAt": dt.datetime.now(dt.UTC),
                    }
                )
                self.db.author_profiles.update_one(
                    {"rawAuthor": existing_author},
                    {"$set": {"authorEmail": normalized_email, "updatedAt": dt.datetime.now(dt.UTC)}},
                )
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

    def touch_last_raw_report_received_at(self, raw_author: str, received_at: dt.datetime) -> None:
        """Bump author profile time used for reports_stopped / merged lastReceivedAt.

        Call only when ingest produced accounting time deltas (typically after inserting
        event-driven report_rows, or snapshot ingest). Routine heartbeat-only batches that
        insert no report rows must not invoke this helper.
        """
        raw_author = _normalize_author(raw_author)

        if not raw_author or raw_author == "Unknown User":
            return

        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$max": {"lastRawReportReceivedAt": received_at},
                "$set": {"updatedAt": dt.datetime.now(dt.UTC)},
                "$setOnInsert": {
                    "rawAuthor": raw_author,
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
        github_username: str | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        raw_author = _normalize_author(raw_author)
        existing_row = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author},
            {
                "_id": 0,
                "autoBreakEnabled": 1,
                "autoBreakEffectiveDate": 1,
                "timeZoneId": 1,
                "githubUsername": 1,
                "github_username": 1,
                "avatarRefreshedAt": 1,
                "avatarMimeType": 1,
                "pluginEnabled": 1,
            },
        )
        existing_profile: dict[str, Any] = dict(existing_row) if existing_row else {}

        if existing_row is None:
            prior_plugin_enabled = None
        else:
            prior_plugin_enabled = existing_row.get("pluginEnabled", True)
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

        if prior_plugin_enabled is False and plugin_enabled:
            update["pluginIngestResumedAtUtc"] = now
        normalized_time_zone = _valid_time_zone_id(time_zone_id)

        if normalized_time_zone:
            update["timeZoneId"] = normalized_time_zone

        normalized_github = _normalize_github_username(github_username)

        if normalized_github:
            update["githubUsername"] = normalized_github

        existing_auto_break_enabled = bool(existing_profile.get("autoBreakEnabled", False))

        if auto_break_enabled:
            if existing_auto_break_enabled and existing_profile.get("autoBreakEffectiveDate"):
                update["autoBreakEffectiveDate"] = existing_profile.get("autoBreakEffectiveDate")
            else:
                update["autoBreakEffectiveDate"] = _next_author_local_date(
                    normalized_time_zone or existing_profile.get("timeZoneId")
                )

        operation: dict[str, Any] = {"$set": update}

        if not normalized_github:
            operation.setdefault("$unset", {})["githubUsername"] = ""
            operation.setdefault("$unset", {})["avatarRefreshedAt"] = ""
            operation.setdefault("$unset", {})["avatarMimeType"] = ""

        if normalized_telegram:
            update["telegramUsername"] = normalized_telegram
        else:
            operation.setdefault("$unset", {})["telegramUsername"] = ""

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

        prev_gh = _github_login_from_profile_doc(existing_profile)
        if normalized_github != prev_gh:
            remove_author_avatar_cache_file(getattr(self, "avatar_cache_dir", None), raw_author)
            operation.setdefault("$unset", {})["avatarRefreshedAt"] = ""
            operation.setdefault("$unset", {})["avatarMimeType"] = ""

        self.db.author_profiles.update_one({"rawAuthor": raw_author}, operation, upsert=True)
        composed(self).invalidate_activity_summary_cache()
        merged_profile = {**existing_profile, **update}
        if operation.get("$unset", {}).get("avatarRefreshedAt") == "":
            merged_profile.pop("avatarRefreshedAt", None)
            merged_profile.pop("avatarMimeType", None)
        profile_out = {k: v for k, v in update.items() if k != "updatedAt"}
        profile_out["avatarUrl"] = _cached_author_avatar_api_url(raw_author, normalized_github, merged_profile)

        if not normalized_github:
            remove_author_avatar_cache_file(getattr(self, "avatar_cache_dir", None), raw_author)

        return {"ok": True, "profile": profile_out}

    def delete_author_data(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        author_keys = composed(self).author_alias_keys(normalized_author)
        author_query = {"author": {"$in": author_keys}}
        raw_author_query = {"rawAuthor": {"$in": author_keys}}
        profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0, "telegramUsername": 1}) or {}
        raw_report_ids = set()

        for snapshot in self.db.activity_snapshots.find(author_query, {"rawReportId": 1}):
            if snapshot.get("rawReportId"):
                raw_report_ids.add(snapshot["rawReportId"])

        for batch in self.db.raw_event_batches.find(author_query, {"rawReportId": 1}):
            if batch.get("rawReportId"):
                raw_report_ids.add(batch["rawReportId"])

        aggregate_session_state_deleted = 0

        for author_key in author_keys:
            state_key_pattern = f"(^|\\|){re.escape(author_key)}\\|"
            aggregate_session_state_deleted += self.db.aggregate_session_state.delete_many(
                {"_id": {"$regex": state_key_pattern}}
            ).deleted_count

        counts = {
            "rawReports": self.db.raw_reports.delete_many({"_id": {"$in": list(raw_report_ids)}}).deleted_count if raw_report_ids else 0,
            "activitySnapshots": self.db.activity_snapshots.delete_many(author_query).deleted_count,
            "rawEventBatches": self.db.raw_event_batches.delete_many(author_query).deleted_count,
            "rawActivityEvents": self.db.raw_activity_events.delete_many(author_query).deleted_count,
            "reportRows": self.db.report_rows.delete_many(author_query).deleted_count,
            "dailyAuthorActivity": self.db.daily_author_activity.delete_many(author_query).deleted_count,
            "aggregateSessionState": aggregate_session_state_deleted,
            "reportSecurityEvents": self.db.report_security_events.delete_many(author_query).deleted_count,
            "reportRefreshRequests": self.db.report_refresh_requests.delete_many(author_query).deleted_count,
            "manualReportExpectations": self.db.manual_report_expectations.delete_many(author_query).deleted_count,
            "breakEvents": self.db.break_events.delete_many(raw_author_query).deleted_count,
            "breakSessions": self.db.break_sessions.delete_many(raw_author_query).deleted_count,
            "breakIntervals": self.db.break_intervals.delete_many(raw_author_query).deleted_count,
            "daySessions": self.db.day_sessions.delete_many(raw_author_query).deleted_count,
            "telegramDayReminders": self.db.telegram_day_reminders.delete_many(raw_author_query).deleted_count,
            "telegramOnlinePrompts": self.db.telegram_online_prompts.delete_many(raw_author_query).deleted_count,
            "telegramMeetingAutoAfkNotifications": self.db.telegram_meeting_auto_afk_notifications.delete_many(raw_author_query).deleted_count,
            "meetingEvents": self.db.meeting_events.delete_many(raw_author_query).deleted_count,
            "meetingSessions": self.db.meeting_sessions.delete_many(raw_author_query).deleted_count,
            "meetingIntervals": self.db.meeting_intervals.delete_many(raw_author_query).deleted_count,
            "meetingSummaries": self.db.meeting_summaries.delete_many({"participantNames": {"$in": author_keys}}).deleted_count,
            "reportChallenges": self.db.report_challenges.delete_many(author_query).deleted_count,
        }

        telegram_username = profile.get("telegramUsername")

        if telegram_username:
            counts["breakEvents"] += self.db.break_events.delete_many({"telegramUsername": telegram_username}).deleted_count
            counts["breakSessions"] += self.db.break_sessions.delete_many({"telegramUsername": telegram_username}).deleted_count

        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "author": normalized_author, "authorKeys": author_keys, "deleted": counts}

    def delete_author_data_for_date_range(self, raw_author: str, start_date: str, end_date: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        try:
            dt.date.fromisoformat(start_date)
            dt.date.fromisoformat(end_date)
        except ValueError:
            return {"ok": False, "error": "Invalid date format, expected YYYY-MM-DD"}

        if start_date > end_date:
            return {"ok": False, "error": "startDate must be <= endDate"}

        profile = (
            self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0, "telegramUsername": 1, "timeZoneId": 1}) or {}
        )
        author_keys = composed(self).author_alias_keys(normalized_author)
        author_filter = {"author": {"$in": author_keys}}
        raw_author_filter = {"rawAuthor": {"$in": author_keys}}
        author_tz = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        date_filter = {"date": {"$gte": start_date, "$lte": end_date}}

        event_query = {**author_filter, **date_filter}
        batch_ids: set[str] = set()

        for event in self.db.raw_activity_events.find(event_query, {"_id": 0, "batchId": 1}):
            batch_id = str(event.get("batchId") or "").strip()

            if batch_id:
                batch_ids.add(batch_id)

        counts: dict[str, int] = {}
        counts["rawActivityEvents"] = self.db.raw_activity_events.delete_many(event_query).deleted_count

        deleted_batches = 0
        deleted_raw_reports = 0

        for batch_id in sorted(batch_ids):
            if self.db.raw_activity_events.count_documents({"batchId": batch_id}) > 0:
                continue

            batch = self.db.raw_event_batches.find_one({"batchId": batch_id}, {"_id": 0})

            if batch:
                self.db.raw_event_batches.delete_many({"batchId": batch_id})
                deleted_batches += 1
                raw_report_id = batch.get("rawReportId")

                if raw_report_id is not None:
                    self.db.raw_reports.delete_many({"_id": raw_report_id})
                    deleted_raw_reports += 1

        counts["rawEventBatches"] = deleted_batches
        counts["rawReports"] = deleted_raw_reports
        counts["activitySnapshots"] = self.db.activity_snapshots.delete_many({**author_filter, **date_filter}).deleted_count
        counts["reportRows"] = self.db.report_rows.delete_many({**author_filter, **date_filter}).deleted_count
        counts["dailyAuthorActivity"] = self.db.daily_author_activity.delete_many({**author_filter, **date_filter}).deleted_count

        raw_author_query = {**raw_author_filter, **date_filter}
        counts["breakEvents"] = self.db.break_events.delete_many(raw_author_query).deleted_count
        counts["breakIntervals"] = self.db.break_intervals.delete_many(raw_author_query).deleted_count
        counts["breakSessions"] = self.db.break_sessions.delete_many(raw_author_query).deleted_count
        counts["daySessions"] = self.db.day_sessions.delete_many(raw_author_query).deleted_count
        counts["telegramDayReminders"] = self.db.telegram_day_reminders.delete_many(raw_author_query).deleted_count
        counts["telegramOnlinePrompts"] = self.db.telegram_online_prompts.delete_many(raw_author_query).deleted_count
        counts["telegramBreakActivityPrompts"] = self.db.telegram_break_activity_prompts.delete_many(raw_author_query).deleted_count
        counts["telegramDuplicateAfkPrompts"] = self.db.telegram_duplicate_afk_prompts.delete_many(raw_author_query).deleted_count
        counts["telegramMeetingAutoAfkNotifications"] = self.db.telegram_meeting_auto_afk_notifications.delete_many(
            raw_author_query
        ).deleted_count
        counts["meetingEvents"] = self.db.meeting_events.delete_many(raw_author_query).deleted_count
        counts["meetingIntervals"] = self.db.meeting_intervals.delete_many(raw_author_query).deleted_count
        counts["calendarMarks"] = self.db.calendar_marks.delete_many(raw_author_query).deleted_count
        counts["statusEvents"] = self.db.status_events.delete_many(raw_author_query).deleted_count

        telegram_username = profile.get("telegramUsername")

        if telegram_username:
            telegram_query = {"telegramUsername": telegram_username, **date_filter}
            counts["breakEvents"] += self.db.break_events.delete_many(telegram_query).deleted_count
            counts["breakSessions"] += self.db.break_sessions.delete_many(telegram_query).deleted_count

        meeting_sessions_deleted = 0

        for session in list(self.db.meeting_sessions.find(raw_author_filter, {"_id": 0})):
            discord_uid = session.get("discordUserId")

            if not discord_uid:
                continue

            session_date_value = str(session.get("date") or "").strip()
            session_tz = _valid_time_zone_id(session.get("timeZoneId")) or author_tz

            if session_date_value:
                in_range = start_date <= session_date_value <= end_date
            else:
                started_at = _coerce_datetime(session.get("startedAt"))

                if not started_at:
                    continue

                session_date_value = _telegram_event_date(started_at, session_tz)
                in_range = start_date <= session_date_value <= end_date

            if in_range:
                self.db.meeting_sessions.delete_one({"discordUserId": discord_uid})
                meeting_sessions_deleted += 1

        counts["meetingSessions"] = meeting_sessions_deleted

        meeting_summaries_deleted = 0

        for summary in list(self.db.meeting_summaries.find({"participantNames": {"$in": author_keys}}, {"_id": 0})):
            started_at = _coerce_datetime(summary.get("startedAt"))

            if not started_at:
                continue

            summary_date = _telegram_event_date(started_at, author_tz)

            if start_date <= summary_date <= end_date:
                summary_id = summary.get("summaryId")

                if summary_id:
                    self.db.meeting_summaries.delete_one({"summaryId": summary_id})
                    meeting_summaries_deleted += 1

        counts["meetingSummaries"] = meeting_summaries_deleted

        rebuild_result = composed(self).rebuild_aggregates_for_dates(
            start_date=start_date,
            end_date=end_date,
            authors=author_keys,
        )

        return {
            "ok": True,
            "author": normalized_author,
            "authorKeys": author_keys,
            "startDate": start_date,
            "endDate": end_date,
            "deleted": counts,
            "rebuildDeletedReportRows": rebuild_result.get("deletedReportRows", 0),
        }

    def bulk_delete_activity_all_authors_for_range(self, start_date: str, end_date: str) -> dict[str, Any]:
        authors = self.list_authors()
        failures: list[dict[str, Any]] = []

        for author in authors:
            result = self.delete_author_data_for_date_range(author, start_date, end_date)

            if not result.get("ok"):
                failures.append({"author": author, "error": result.get("error", "Unknown error")})

        return {
            "ok": len(failures) == 0,
            "startDate": start_date,
            "endDate": end_date,
            "authorsProcessed": len(authors),
            "failures": failures,
        }

    def wipe_all_authors_activity_data(self) -> dict[str, Any]:
        authors = self.list_authors()
        failures: list[dict[str, Any]] = []

        for author in authors:
            result = self.delete_author_data(author)

            if not result.get("ok"):
                failures.append({"author": author, "error": result.get("error", "Unknown error")})

        return {
            "ok": len(failures) == 0,
            "authorsProcessed": len(authors),
            "failures": failures,
        }

    def delete_author_profile(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        remove_author_avatar_cache_file(getattr(self, "avatar_cache_dir", None), normalized_author)

        data_result = self.delete_author_data(normalized_author)
        counts = dict(data_result.get("deleted", {}))
        counts["authorProfiles"] = self.db.author_profiles.delete_many({"rawAuthor": normalized_author}).deleted_count
        counts["calendarMarks"] = self.db.calendar_marks.delete_many({"rawAuthor": normalized_author}).deleted_count

        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "author": normalized_author, "deleted": counts}

