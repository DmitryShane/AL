from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..activity_math import dt, _coerce_datetime, _isoformat_or_none, _new_id, _normalize_author, _normalize_telegram_username, _valid_time_zone_id
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


LOGGER = logging.getLogger(__name__)
MEETING_CLAIM_TIMEOUT = dt.timedelta(seconds=120)
MEETING_DELIVERY_WINDOW = dt.timedelta(minutes=15)


class TelegramMeetingDeliveryService(MongoComposableMixin):
    def claim_due_telegram_meeting_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        settings = composed(self).get_meeting_notification_settings()

        if not settings.get("enabled") or not settings.get("authorRawAuthors"):
            return []

        try:
            time_zone_id = _valid_time_zone_id(settings.get("timeZoneId")) or "UTC"
            local_now = now.astimezone(ZoneInfo(time_zone_id))
            hour_raw, minute_raw = str(settings.get("time") or "10:00").split(":", 1)
            scheduled_local = local_now.replace(
                hour=int(hour_raw),
                minute=int(minute_raw),
                second=0,
                microsecond=0,
            )
        except (ValueError, TypeError):
            return []

        local_date = local_now.date().isoformat()
        collection = self.db.telegram_meeting_notifications
        existing = collection.find_one({"date": local_date}, {"_id": 0})
        if not existing:
            days = {int(day) for day in settings.get("daysOfWeek", [0, 1, 2, 3, 4])}
            if local_now.weekday() not in days or local_now < scheduled_local:
                return []

            doc = {
                "reminderId": _new_id(),
                "date": local_date,
                "time": settings.get("time"),
                "timeZoneId": time_zone_id,
                "scheduledAt": scheduled_local.astimezone(dt.UTC),
                "status": "pending",
                "attemptCount": 0,
                "createdAt": now,
                "updatedAt": now,
            }
            try:
                collection.update_one({"date": local_date}, {"$setOnInsert": doc}, upsert=True)
            except DuplicateKeyError:
                # Another poller created this date between the read and the upsert.
                pass
            existing = collection.find_one({"date": local_date}, {"_id": 0})

        if not existing or existing.get("status") not in {"pending", "claimed"}:
            return []
        reminder_id = str(existing.get("reminderId") or "")
        if not reminder_id:
            return []

        scheduled_at = _coerce_datetime(existing.get("scheduledAt"))
        if scheduled_at is None:
            try:
                saved_zone = _valid_time_zone_id(existing.get("timeZoneId")) or time_zone_id
                saved_hour, saved_minute = str(existing.get("time") or settings.get("time") or "10:00").split(":", 1)
                scheduled_at = dt.datetime.combine(
                    dt.date.fromisoformat(existing["date"]),
                    dt.time(int(saved_hour), int(saved_minute)),
                    tzinfo=ZoneInfo(saved_zone),
                ).astimezone(dt.UTC)
            except (ValueError, TypeError, KeyError):
                return []
        if now < scheduled_at:
            return []

        # Compare the observed lease as well as status so concurrent readers cannot
        # both claim it, or close a lease that another poller has just renewed.
        lease_query = {
            "reminderId": reminder_id,
            "status": existing["status"],
            "lastClaimedAt": existing.get("lastClaimedAt"),
            "createdAt": existing.get("createdAt"),
        }
        close_fields: dict[str, Any] = {}
        if now >= scheduled_at + MEETING_DELIVERY_WINDOW:
            close_fields["closeAction"] = "delivery_window_expired"
        else:
            claimed_at = _coerce_datetime(existing.get("lastClaimedAt")) or _coerce_datetime(existing.get("createdAt"))
            if existing["status"] == "claimed" and claimed_at and now < claimed_at + MEETING_CLAIM_TIMEOUT:
                return []
            meeting_recording = self._meeting_recording_started_today(local_now, now)
            if meeting_recording:
                close_fields = {
                    "closeAction": "meeting_already_started",
                    "meetingRecordingId": meeting_recording.get("recordingId"),
                    "meetingStartedAt": meeting_recording.get("startedAt"),
                }

        mention_authors = []
        if not close_fields:
            mention_authors = self._meeting_notification_mention_authors(settings.get("authorRawAuthors", []), local_date)
            if not mention_authors:
                close_fields["closeAction"] = "empty_mention_list"

        if close_fields:
            closed = collection.find_one_and_update(
                lease_query,
                {"$set": {**close_fields, "status": "closed", "closedAt": now, "updatedAt": now}},
                return_document=ReturnDocument.AFTER,
            )
            if closed and close_fields["closeAction"] == "delivery_window_expired":
                LOGGER.info(
                    "Meeting invitation closed: reminderId=%s attemptCount=%s reason=delivery_window_expired",
                    reminder_id, closed.get("attemptCount", 0),
                )
            return []

        saved = collection.find_one_and_update(
            lease_query,
            {
                "$set": {
                    "status": "claimed", "lastClaimedAt": now, "updatedAt": now,
                    "mentionAuthors": mention_authors, "scheduledAt": scheduled_at,
                },
                "$inc": {"attemptCount": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not saved:
            return []
        if existing["status"] == "claimed":
            LOGGER.info(
                "Meeting invitation reclaimed: reminderId=%s attemptCount=%s reason=claim_timeout",
                reminder_id, saved["attemptCount"],
            )
        return [self._telegram_meeting_notification_payload(saved)]

    def _meeting_notification_mention_authors(self, raw_authors: list[str], local_date: str) -> list[dict[str, Any]]:
        mention_authors: list[dict[str, Any]] = []

        for raw_author in raw_authors:
            normalized_author = _normalize_author(raw_author)
            if not normalized_author:
                continue

            profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0}) or {}
            telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
            if not telegram_username:
                continue

            if self.db.calendar_marks.find_one({"rawAuthor": normalized_author, "date": local_date}, {"_id": 1}):
                continue

            day_session = self.db.day_sessions.find_one(
                {"rawAuthor": normalized_author, "date": local_date},
                {"_id": 0, "startedAt": 1, "lastOfflineAt": 1, "lastOnlineAt": 1},
            ) or {}
            started_at = _coerce_datetime(day_session.get("startedAt"))
            last_online_at = _coerce_datetime(day_session.get("lastOnlineAt"))
            last_offline_at = _coerce_datetime(day_session.get("lastOfflineAt"))
            if not started_at or not last_online_at:
                continue

            # An offline event closes the workday until the author explicitly comes online again.
            if last_offline_at and last_online_at <= last_offline_at:
                continue

            mention_authors.append(
                {
                    "rawAuthor": normalized_author,
                    "displayName": profile.get("displayName") or normalized_author,
                    "telegramUsername": telegram_username,
                }
            )

        return mention_authors

    def _meeting_recording_started_today(self, local_now: dt.datetime, now: dt.datetime) -> dict[str, Any] | None:
        local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.db.meeting_recordings.find_one(
            {
                "startedAt": {
                    "$gte": local_day_start.astimezone(dt.UTC),
                    "$lte": now,
                }
            },
            {"_id": 0, "recordingId": 1, "startedAt": 1},
            sort=[("startedAt", -1)],
        )

    def _telegram_meeting_notification_payload(self, doc: dict[str, Any]) -> dict[str, Any]:
        mention_authors = list(doc.get("mentionAuthors") or [])
        return {
            "reminderId": doc.get("reminderId"),
            "date": doc.get("date"),
            "time": doc.get("time"),
            "timeZoneId": doc.get("timeZoneId"),
            "mentionAuthors": mention_authors,
            "telegramUsernames": [str(author.get("telegramUsername") or "") for author in mention_authors if author.get("telegramUsername")],
            "displayNames": [str(author.get("displayName") or author.get("rawAuthor") or "") for author in mention_authors],
        }

    def mark_telegram_meeting_notification_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_meeting_notifications.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def claim_due_telegram_meeting_recording_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        notifications: list[dict[str, Any]] = []

        for doc in self.db.telegram_meeting_recording_notifications.find({"status": "pending"}, {"_id": 0}):
            reminder_id = str(doc.get("reminderId") or "")

            if not reminder_id:
                continue

            self.db.telegram_meeting_recording_notifications.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            notifications.append(
                {
                    "reminderId": reminder_id,
                    "recordingId": doc.get("recordingId"),
                    "kind": doc.get("kind"),
                    "participantNames": doc.get("participantNames", []),
                    "participantTelegramUsernames": doc.get("participantTelegramUsernames", []),
                    "startedAt": _isoformat_or_none(doc.get("startedAt")),
                    "endedAt": _isoformat_or_none(doc.get("endedAt")),
                    "durationSeconds": int(doc.get("durationSeconds", 0)),
                }
            )

        return notifications

    def mark_telegram_meeting_recording_notification_sent(
        self, reminder_id: str, message_id: int | None = None
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_meeting_recording_notifications.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def claim_due_telegram_meeting_auto_afk_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        notifications: list[dict[str, Any]] = []

        for doc in self.db.telegram_meeting_auto_afk_notifications.find({"status": "pending"}, {"_id": 0}):
            reminder_id = str(doc.get("reminderId") or "")

            if not reminder_id:
                continue

            self.db.telegram_meeting_auto_afk_notifications.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            notifications.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": doc.get("rawAuthor"),
                    "telegramUsername": doc.get("telegramUsername"),
                    "soloStartedAt": _isoformat_or_none(doc.get("soloStartedAt")),
                    "movedAt": _isoformat_or_none(doc.get("movedAt")),
                    "timeZoneId": doc.get("timeZoneId"),
                    "excludedSeconds": int(doc.get("excludedSeconds", 0)),
                    "thresholdSeconds": int(doc.get("thresholdSeconds") or doc.get("excludedSeconds") or 0),
                }
            )

        return notifications

    def mark_telegram_meeting_auto_afk_notification_sent(
        self, reminder_id: str, message_id: int | None = None
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_meeting_auto_afk_notifications.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def claim_due_telegram_meeting_summary_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        notifications: list[dict[str, Any]] = []

        for doc in self.db.meeting_summaries.find({"status": "pending"}, {"_id": 0}):
            summary_id = str(doc.get("summaryId") or "")

            if not summary_id:
                continue

            self.db.meeting_summaries.update_one(
                {"summaryId": summary_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            recording_id = str(doc.get("recordingId") or "")

            if recording_id:
                composed(self)._update_meeting_recording_pipeline_status(recording_id, "telegram_claimed", updated_at=now)

            discord_settings = composed(self).get_discord_settings()
            notifications.append(
                {
                    "summaryId": summary_id,
                    "recordingId": doc.get("recordingId"),
                    "participantNames": doc.get("participantNames", []),
                    "participantTelegramUsernames": doc.get("participantTelegramUsernames", []),
                    "startedAt": _isoformat_or_none(doc.get("startedAt")),
                    "endedAt": _isoformat_or_none(doc.get("endedAt")),
                    "durationSeconds": int(doc.get("durationSeconds", 0)),
                    "summary": doc.get("summary", ""),
                    "recipient": self._meeting_summary_recipient(),
                    "telegramTemplate": discord_settings["meetingSummaryTelegramTemplate"],
                }
            )

        return notifications

    def _meeting_summary_recipient(self) -> dict[str, Any]:
        recipient = composed(self).get_discord_settings()["meetingSummaryRecipient"]

        if recipient == "work_chat":
            return {"kind": "work_chat"}

        profile = self.db.author_profiles.find_one(
            {"rawAuthor": recipient}, {"_id": 0, "telegramPrivateChatId": 1, "telegramUsername": 1}
        )

        if profile and profile.get("telegramPrivateChatId"):
            return {
                "kind": "private",
                "rawAuthor": recipient,
                "telegramUsername": profile.get("telegramUsername", ""),
                "chatId": int(profile["telegramPrivateChatId"]),
            }

        return {"kind": "work_chat", "fallbackReason": "missing_private_chat"}

    def mark_telegram_meeting_summary_sent(self, summary_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        summary = self.db.meeting_summaries.find_one({"summaryId": summary_id}, {"_id": 0, "recordingId": 1})
        self.db.meeting_summaries.update_one(
            {"summaryId": summary_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )

        if summary and summary.get("recordingId"):
            composed(self)._update_meeting_recording_pipeline_status(str(summary["recordingId"]), "telegram_sent", updated_at=now)

        return {"ok": True}
