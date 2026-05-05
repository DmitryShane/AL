from __future__ import annotations

from typing import Any

from ..activity_math import dt, _isoformat_or_none
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class DiscordMeetingNotificationsService(MongoComposableMixin):
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
