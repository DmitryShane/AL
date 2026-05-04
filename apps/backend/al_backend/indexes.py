from __future__ import annotations

from .activity_math import *


class IndexManager:
    def __init__(self, db):
        self.db = db

    def ensure_indexes(self) -> None:
        self.db.raw_reports.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.raw_event_batches.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.raw_activity_events.create_index("eventId", unique=True)
        self.db.raw_activity_events.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("projectId", ASCENDING), ("sessionId", ASCENDING), ("occurredAtUtc", ASCENDING)]
        )
        self.db.report_challenges.create_index("challengeId", unique=True)
        self.db.report_challenges.create_index("expiresAt")
        self.db.report_security_events.create_index([("createdAt", DESCENDING)])
        self.db.report_security_events.create_index([("author", ASCENDING), ("createdAt", DESCENDING)])
        self.db.activity_snapshots.create_index([("source", ASCENDING), ("author", ASCENDING), ("date", ASCENDING)])
        self.db.activity_snapshots.create_index([("sessionId", ASCENDING), ("date", ASCENDING), ("recordedAt", DESCENDING)])
        self.db.report_rows.create_index([("receivedAt", DESCENDING)])
        self.db.report_rows.create_index([("date", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.report_rows.create_index([("author", ASCENDING), ("date", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.report_rows.create_index([("author", ASCENDING), ("date", ASCENDING), ("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.report_rows.create_index([("date", ASCENDING), ("author", ASCENDING), ("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.report_rows.create_index([("source", ASCENDING), ("author", ASCENDING), ("sessionId", ASCENDING), ("date", ASCENDING)])
        self.db.activity_summary_cache.create_index("cacheKey", unique=True)
        self.db.activity_summary_cache.create_index("expiresAt", expireAfterSeconds=0)
        self.db.activity_summary_cache.create_index("dateMode")
        self.db.status_events.create_index(
            [("rawAuthor", ASCENDING), ("date", ASCENDING), ("statusEventType", ASCENDING), ("transitionAt", ASCENDING)],
            unique=True,
        )
        self.db.status_events.create_index([("rawAuthor", ASCENDING), ("transitionAt", DESCENDING)])
        self.db.status_states.create_index("rawAuthor", unique=True)
        self.db.daily_author_activity.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("projectId", ASCENDING), ("date", ASCENDING)],
            unique=True,
        )
        self.db.daily_author_activity.create_index([("date", ASCENDING), ("author", ASCENDING), ("source", ASCENDING)])
        self.db.daily_author_activity.create_index([("author", ASCENDING), ("date", ASCENDING)])
        self.db.aggregate_day_state.create_index([("date", ASCENDING), ("author", ASCENDING), ("stateId", ASCENDING)], unique=True)
        self.db.aggregate_day_state.create_index([("author", ASCENDING), ("date", ASCENDING)])
        self.db.author_profiles.create_index("rawAuthor", unique=True)
        self.db.author_aliases.create_index("sourceRawAuthor", unique=True)
        self.db.author_aliases.create_index("targetRawAuthor")
        self.db.author_profiles.create_index("telegramUsername", unique=True, sparse=True)
        self.db.author_profiles.create_index("discordUserId", unique=True, sparse=True)
        self.db.break_events.create_index([("telegramUsername", ASCENDING), ("timestamp", DESCENDING)])
        self.db.break_events.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING), ("eventType", ASCENDING), ("timestamp", DESCENDING)])
        self.db.break_sessions.create_index("telegramUsername", unique=True)
        self.db.meeting_events.create_index([("discordUserId", ASCENDING), ("timestamp", DESCENDING)])
        self.db.meeting_sessions.create_index("discordUserId", unique=True)
        self.db.meeting_sessions.create_index([("rawAuthor", ASCENDING), ("startedAt", ASCENDING)])
        self.db.meeting_intervals.create_index([("rawAuthor", ASCENDING), ("startedAt", ASCENDING), ("endedAt", ASCENDING)])
        self.db.telegram_meeting_auto_afk_notifications.create_index("reminderId", unique=True)
        self.db.telegram_meeting_auto_afk_notifications.create_index("autoAfkEventId", unique=True)
        self.db.meeting_recordings.create_index("recordingId", unique=True)
        self.db.meeting_summaries.create_index("summaryId", unique=True)
        self.db.meeting_summaries.create_index("recordingId", unique=True)
        self.db.meeting_summaries.create_index([("status", ASCENDING), ("createdAt", ASCENDING)])
        self.db.telegram_day_reminders.create_index("reminderId", unique=True)
        self.db.telegram_day_reminders.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.telegram_online_prompts.create_index("reminderId", unique=True)
        for spec in list(self.db.telegram_online_prompts.list_indexes()):
            key = spec.get("key")

            if dict(key or {}) == {"rawAuthor": 1, "date": 1} and spec.get("name") != "telegram_online_prompts_open_day_unique":
                try:
                    self.db.telegram_online_prompts.drop_index(spec["name"])
                except Exception:
                    pass

        self.db.telegram_online_prompts.create_index(
            [("rawAuthor", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="telegram_online_prompts_open_day_unique",
            partialFilterExpression={"status": {"$in": ["pending", "claimed", "sent"]}},
        )
        self.db.telegram_break_activity_prompts.create_index("reminderId", unique=True)
        self.db.telegram_break_activity_prompts.create_index([("rawAuthor", ASCENDING), ("breakStartedAt", ASCENDING)], unique=True)
        self.db.telegram_duplicate_afk_prompts.create_index("reminderId", unique=True)
        self.db.interval_settings.create_index("kind", unique=True)
        self.db.interval_settings.create_index("author", unique=True, sparse=True)
        self.db.system_settings.create_index("kind", unique=True)
        self.db.report_refresh_requests.create_index("author", unique=True)
        self.db.report_refresh_requests.create_index("requestedAt")
        self.db.manual_report_expectations.create_index("author", unique=True)
        self.db.calendar_marks.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.calendar_marks.create_index("date")
        self.db.calendar_reasons.create_index("id", unique=True)
        self.db.day_sessions.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)])
        self.db.day_sessions.create_index("date")
        self.db.site_users.create_index("email", unique=True)
        self.db.site_sessions.create_index("tokenHash", unique=True)
        self.db.site_sessions.create_index("expiresAt", expireAfterSeconds=0)
