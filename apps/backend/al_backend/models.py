from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ReportIn(ApiModel):
    source: str = Field(min_length=1, examples=["ual"])
    plugin_version: str = Field(default="0.1.0", alias="pluginVersion")
    challenge_id: str = Field(alias="challengeId", min_length=1)
    device_id: str | None = Field(default=None, alias="deviceId")
    encrypted_packet: str = Field(alias="encryptedPacket")


class ReportChallengeIn(ApiModel):
    source: str = Field(min_length=1, examples=["ual"])
    plugin_version: str = Field(default="0.1.0", alias="pluginVersion")
    author: str = Field(default="Unknown User")
    author_email: str | None = Field(default=None, alias="authorEmail")
    project_id: str | None = Field(default=None, alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    device_id: str | None = Field(default=None, alias="deviceId")


class ReportChallengeResponse(ApiModel):
    challenge_id: str = Field(alias="challengeId")
    public_modulus: str = Field(alias="publicModulus")
    public_exponent: str = Field(alias="publicExponent")
    expires_at: str = Field(alias="expiresAt")


class PluginConfig(ApiModel):
    source: str
    author: str
    project_id: str = Field(alias="projectId")
    enabled: bool
    send_interval_seconds: int = Field(alias="sendIntervalSeconds")
    submit_report_now: bool = Field(default=False, alias="submitReportNow")


class ReportRefreshRequest(ApiModel):
    author: str | None = None


class IntervalSettingsIn(ApiModel):
    default_send_interval_seconds: int | None = Field(default=None, alias="defaultSendIntervalSeconds", ge=30)
    device_send_interval_seconds: int | None = Field(default=None, alias="deviceSendIntervalSeconds", ge=1)
    idle_threshold_seconds: int | None = Field(default=None, alias="idleThresholdSeconds", ge=30)
    device_idle_threshold_seconds: int | None = Field(default=None, alias="deviceIdleThresholdSeconds", ge=1)
    plugin_ingest_enabled: bool | None = Field(default=None, alias="pluginIngestEnabled")
    telegram_online_prompt_delay_minutes: int | None = Field(
        default=None,
        alias="telegramOnlinePromptDelayMinutes",
        ge=1,
        le=1440,
    )


class DiscordSettingsIn(ApiModel):
    meeting_auto_afk_timeout_seconds: int = Field(alias="meetingAutoAfkTimeoutSeconds", ge=60)
    meeting_summaries_enabled: bool = Field(default=False, alias="meetingSummariesEnabled")
    meeting_summary_min_participants: int = Field(default=2, alias="meetingSummaryMinParticipants", ge=1)
    meeting_summary_min_duration_seconds: int = Field(default=120, alias="meetingSummaryMinDurationSeconds", ge=1)
    meeting_summary_language: str = Field(default="English", alias="meetingSummaryLanguage", min_length=2)
    meeting_summary_recipient: str = Field(default="work_chat", alias="meetingSummaryRecipient", min_length=1)
    meeting_audio_retention_seconds: int = Field(default=0, alias="meetingAudioRetentionSeconds", ge=0)
    meeting_summary_prompt: str = Field(default="", alias="meetingSummaryPrompt")
    meeting_summary_telegram_template: str = Field(default="", alias="meetingSummaryTelegramTemplate")


class FakeOnlineSettingsIn(ApiModel):
    enabled: bool = False
    days_of_week: list[int] = Field(default_factory=list, alias="daysOfWeek")
    start_time: str = Field(default="10:00", alias="startTime", pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(default="12:00", alias="endTime", pattern=r"^\d{2}:\d{2}$")
    delay_min_seconds: int = Field(default=5, alias="delayMinSeconds", ge=0, le=3600)
    delay_max_seconds: int = Field(default=60, alias="delayMaxSeconds", ge=0, le=3600)


class TelegramPrivateChatIn(ApiModel):
    telegram_username: str = Field(alias="telegramUsername", min_length=1)
    chat_id: int = Field(alias="chatId")


class LoginIn(ApiModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class SiteUserIn(ApiModel):
    email: str = Field(min_length=3)
    password: str | None = Field(default=None, min_length=8)
    display_name: str | None = Field(default=None, alias="displayName")
    role: str = Field(default="viewer", pattern="^(admin|editor|viewer)$")
    can_view_server_stats: bool = Field(default=False, alias="canViewServerStats")
    active: bool = True


class AuthorProfileIn(ApiModel):
    raw_author: str = Field(alias="rawAuthor", min_length=1)
    display_name: str | None = Field(default=None, alias="displayName")
    team: str | None = None
    profile_type: str | None = Field(default=None, alias="profileType")
    telegram_username: str | None = Field(default=None, alias="telegramUsername")
    discord_user_id: str | None = Field(default=None, alias="discordUserId")
    discord_username: str | None = Field(default=None, alias="discordUsername")
    plugin_enabled: bool = Field(default=True, alias="pluginEnabled")
    auto_break_enabled: bool = Field(default=False, alias="autoBreakEnabled")
    auto_break_effective_date: str | None = Field(default=None, alias="autoBreakEffectiveDate")
    author_color: str | None = Field(default=None, alias="authorColor")
    github_username: str | None = Field(default=None, alias="githubUsername")


class AvatarSettingsIn(ApiModel):
    refresh_cadence: str = Field(alias="refreshCadence", min_length=1)


class ActivitySnapshotsRemakeIn(ApiModel):
    start_date: str = Field(alias="startDate", min_length=10, max_length=10)
    end_date: str = Field(alias="endDate", min_length=10, max_length=10)


class BulkAuthorsActivityDeleteIn(ApiModel):
    preset: Literal["1d", "2d", "3d", "week", "month", "full"]
    confirm_phrase: str = Field(alias="confirmPhrase", min_length=1)


class FullActivityRebuildIn(ApiModel):
    confirm_phrase: str = Field(alias="confirmPhrase", min_length=1)


class AuthorAliasIn(ApiModel):
    source_raw_author: str = Field(alias="sourceRawAuthor", min_length=1)
    target_raw_author: str = Field(alias="targetRawAuthor", min_length=1)


class DeviceProfileAliasIn(ApiModel):
    target_raw_author: str = Field(alias="targetRawAuthor", min_length=1)


class PublisherProfileIn(ApiModel):
    display_name: str = Field(alias="displayName", min_length=1)
    team: str | None = None
    author_color: str | None = Field(default=None, alias="authorColor")


class CalendarMarkIn(ApiModel):
    authors: list[str] = Field(min_length=1)
    dates: list[str] = Field(min_length=1)
    reason_id: str = Field(alias="reasonId", min_length=1)
    note: str = ""


class CalendarMarksDeleteIn(ApiModel):
    authors: list[str] = Field(min_length=1)
    dates: list[str] = Field(min_length=1)


class CalendarReasonIn(ApiModel):
    id: str | None = None
    label: str = Field(min_length=1)


class BreakEventIn(ApiModel):
    telegram_username: str = Field(alias="telegramUsername", min_length=1)
    event_type: str = Field(alias="eventType", pattern="^(afk|online|offline)$")
    timestamp: str | None = None


class TelegramReminderSentIn(ApiModel):
    reminder_id: str = Field(alias="reminderId", min_length=1)
    message_id: int | None = Field(default=None, alias="messageId")
    kind: str = Field(
        default="day_end",
        alias="kind",
        pattern="^(day_end|online_prompt|break_activity_prompt|duplicate_afk_prompt|meeting_auto_afk|meeting_recording|meeting_summary)$",
    )


class TelegramReminderCloseIn(ApiModel):
    reminder_id: str = Field(alias="reminderId", min_length=1)
    action: str = Field(min_length=1)
    kind: str = Field(
        default="day_end",
        alias="kind",
        pattern="^(day_end|online_prompt|break_activity_prompt|duplicate_afk_prompt)$",
    )
    timestamp: str | None = None
    actor_telegram_username: str | None = Field(default=None, alias="actorTelegramUsername")


class DiscordVoiceEventIn(ApiModel):
    discord_user_id: str = Field(alias="discordUserId", min_length=1)
    discord_username: str | None = Field(default=None, alias="discordUsername")
    event_type: str = Field(alias="eventType", pattern="^(join|leave|reconcile)$")
    guild_id: str | None = Field(default=None, alias="guildId")
    channel_id: str | None = Field(default=None, alias="channelId")
    timestamp: str | None = None


class DiscordMeetingAutoAfkIn(ApiModel):
    discord_user_id: str = Field(alias="discordUserId", min_length=1)
    discord_username: str | None = Field(default=None, alias="discordUsername")
    guild_id: str | None = Field(default=None, alias="guildId")
    meeting_channel_id: str | None = Field(default=None, alias="meetingChannelId")
    afk_channel_id: str | None = Field(default=None, alias="afkChannelId")
    solo_started_at: str = Field(alias="soloStartedAt", min_length=1)
    moved_at: str | None = Field(default=None, alias="movedAt")
    threshold_seconds: int | None = Field(default=None, alias="thresholdSeconds", ge=1)


class DiscordMeetingRecordingStartIn(ApiModel):
    recording_id: str = Field(alias="recordingId", min_length=1)
    guild_id: str | None = Field(default=None, alias="guildId")
    channel_id: str | None = Field(default=None, alias="channelId")
    started_at: str = Field(alias="startedAt", min_length=1)
    participant_discord_user_ids: list[str] = Field(default_factory=list, alias="participantDiscordUserIds")
    participant_names: list[str] = Field(default_factory=list, alias="participantNames")


class DiscordMeetingRecordingFailIn(ApiModel):
    recording_id: str = Field(alias="recordingId", min_length=1)
    ended_at: str = Field(alias="endedAt", min_length=1)
    error: str = Field(min_length=1)


class DiscordMeetingRecordingStatusIn(ApiModel):
    recording_id: str = Field(alias="recordingId", min_length=1)
    status: str = Field(min_length=1)


class SubmitReportResponse(ApiModel):
    ok: bool
    report_id: str = Field(default="", alias="reportId")
    ignored: bool = False


class HealthResponse(ApiModel):
    ok: bool
    mongo: bool


class SummaryResponse(ApiModel):
    authors: list[str]
    reports: list[dict[str, Any]]
    interval_settings: dict[str, Any] = Field(alias="intervalSettings")
    discord_settings: dict[str, Any] = Field(alias="discordSettings")
    activity_summary: dict[str, Any] = Field(alias="activitySummary")
