from __future__ import annotations

from typing import Any

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
    author: str | None = None
    author_send_interval_seconds: int | None = Field(default=None, alias="authorSendIntervalSeconds", ge=30)


class LoginIn(ApiModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class SiteUserIn(ApiModel):
    email: str = Field(min_length=3)
    password: str | None = Field(default=None, min_length=8)
    display_name: str | None = Field(default=None, alias="displayName")
    role: str = Field(default="viewer", pattern="^(admin|editor|viewer)$")
    active: bool = True


class AuthorProfileIn(ApiModel):
    raw_author: str = Field(alias="rawAuthor", min_length=1)
    display_name: str | None = Field(default=None, alias="displayName")
    team: str | None = None
    telegram_username: str | None = Field(default=None, alias="telegramUsername")
    plugin_enabled: bool = Field(default=True, alias="pluginEnabled")
    author_color: str | None = Field(default=None, alias="authorColor")


class AuthorAliasIn(ApiModel):
    source_raw_author: str = Field(alias="sourceRawAuthor", min_length=1)
    target_raw_author: str = Field(alias="targetRawAuthor", min_length=1)


class CalendarMarkIn(ApiModel):
    authors: list[str] = Field(min_length=1)
    dates: list[str] = Field(min_length=1)
    reason_id: str = Field(alias="reasonId", min_length=1)
    note: str = Field(min_length=1)


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


class TelegramReminderCloseIn(ApiModel):
    reminder_id: str = Field(alias="reminderId", min_length=1)
    action: str = Field(pattern="^(offline|overtime)$")
    timestamp: str | None = None


class SubmitReportResponse(ApiModel):
    ok: bool
    report_id: str = Field(alias="reportId")


class HealthResponse(ApiModel):
    ok: bool
    mongo: bool


class SummaryResponse(ApiModel):
    authors: list[str]
    reports: list[dict[str, Any]]
    interval_settings: dict[str, Any] = Field(alias="intervalSettings")
    activity_summary: dict[str, Any] = Field(alias="activitySummary")
