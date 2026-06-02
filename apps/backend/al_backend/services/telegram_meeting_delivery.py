from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from ..activity_math import dt, _isoformat_or_none, _new_id, _normalize_author, _normalize_telegram_username, _valid_time_zone_id
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


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
        days = {int(day) for day in settings.get("daysOfWeek", [0, 1, 2, 3, 4])}
        if local_now.weekday() not in days or local_now < scheduled_local:
            return []

        existing = self.db.telegram_meeting_notifications.find_one({"date": local_date}, {"_id": 0})
        if existing:
            if existing.get("status") != "pending":
                return []

            reminder_id = str(existing.get("reminderId") or "")
            if not reminder_id:
                return []

            self.db.telegram_meeting_notifications.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            return [self._telegram_meeting_notification_payload(existing)]

        mention_authors = self._meeting_notification_mention_authors(settings.get("authorRawAuthors", []), local_date)
        reminder_id = _new_id()

        if not mention_authors:
            self.db.telegram_meeting_notifications.update_one(
                {"date": local_date},
                {
                    "$set": {
                        "date": local_date,
                        "status": "closed",
                        "closeAction": "empty_mention_list",
                        "closedAt": now,
                        "updatedAt": now,
                    },
                    "$setOnInsert": {
                        "reminderId": reminder_id,
                        "createdAt": now,
                    },
                },
                upsert=True,
            )
            return []

        doc = {
            "reminderId": reminder_id,
            "date": local_date,
            "time": settings.get("time"),
            "timeZoneId": time_zone_id,
            "scheduledAt": scheduled_local.astimezone(dt.UTC),
            "mentionAuthors": mention_authors,
            "status": "claimed",
            "lastClaimedAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        self.db.telegram_meeting_notifications.update_one(
            {"date": local_date},
            {"$setOnInsert": doc},
            upsert=True,
        )
        saved = self.db.telegram_meeting_notifications.find_one({"date": local_date}, {"_id": 0}) or doc
        if saved.get("reminderId") != reminder_id or saved.get("status") != "claimed":
            return []

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

            mention_authors.append(
                {
                    "rawAuthor": normalized_author,
                    "displayName": profile.get("displayName") or normalized_author,
                    "telegramUsername": telegram_username,
                }
            )

        return mention_authors

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
