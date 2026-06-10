from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class DiscordSettingsMixin:
    def upsert_discord_settings(self, meeting_auto_afk_timeout_seconds: int) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        current = self.get_discord_settings()
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": current["meetingSummariesEnabled"],
                    "meetingSummaryMinParticipants": current["meetingSummaryMinParticipants"],
                    "meetingSummaryMinDurationSeconds": current["meetingSummaryMinDurationSeconds"],
                    "meetingSummaryLanguage": current["meetingSummaryLanguage"],
                    "meetingSummaryRecipient": current["meetingSummaryRecipient"],
                    "meetingAudioRetentionSeconds": current["meetingAudioRetentionSeconds"],
                    "meetingSummaryPrompt": current["meetingSummaryPrompt"],
                    "meetingSummaryTelegramTemplate": current["meetingSummaryTelegramTemplate"],
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()

    def get_discord_settings(self) -> dict[str, Any]:
        settings = self.db.system_settings.find_one({"kind": "discord"}) or {}
        return {
            "meetingAutoAfkTimeoutSeconds": int(
                settings.get("meetingAutoAfkTimeoutSeconds", DEFAULT_DISCORD_MEETING_AUTO_AFK_TIMEOUT_SECONDS)
            ),
            "meetingSummariesEnabled": bool(settings.get("meetingSummariesEnabled", False)),
            "meetingSummaryMinParticipants": int(settings.get("meetingSummaryMinParticipants", 2)),
            "meetingSummaryMinDurationSeconds": int(settings.get("meetingSummaryMinDurationSeconds", 120)),
            "meetingSummaryLanguage": str(settings.get("meetingSummaryLanguage") or "English"),
            "meetingSummaryRecipient": str(settings.get("meetingSummaryRecipient") or "work_chat"),
            "meetingAudioRetentionSeconds": int(settings.get("meetingAudioRetentionSeconds", 0)),
            "meetingSummaryPrompt": str(
                settings.get("meetingSummaryPrompt") or settings.get("meetingAudioEditPrompt") or DEFAULT_MEETING_SUMMARY_PROMPT
            ),
            "meetingSummaryTelegramTemplate": str(
                settings.get("meetingSummaryTelegramTemplate") or DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE
            ),
        }
