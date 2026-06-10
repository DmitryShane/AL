from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class MeetingSummarySettingsMixin:
    def upsert_discord_summary_settings(
        self,
        *,
        meeting_auto_afk_timeout_seconds: int,
        meeting_summaries_enabled: bool,
        meeting_summary_min_participants: int,
        meeting_summary_min_duration_seconds: int,
        meeting_summary_language: str,
        meeting_summary_recipient: str,
        meeting_audio_retention_seconds: int,
        meeting_summary_prompt: str,
        meeting_summary_telegram_template: str = "",
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": meeting_summaries_enabled,
                    "meetingSummaryMinParticipants": meeting_summary_min_participants,
                    "meetingSummaryMinDurationSeconds": meeting_summary_min_duration_seconds,
                    "meetingSummaryLanguage": meeting_summary_language.strip() or "English",
                    "meetingSummaryRecipient": meeting_summary_recipient.strip() or "work_chat",
                    "meetingAudioRetentionSeconds": meeting_audio_retention_seconds,
                    "meetingSummaryPrompt": meeting_summary_prompt.strip() or DEFAULT_MEETING_SUMMARY_PROMPT,
                    "meetingSummaryTelegramTemplate": meeting_summary_telegram_template.strip() or DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()
