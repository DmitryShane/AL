from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ReportIn(ApiModel):
    source: str = Field(min_length=1, examples=["ual"])
    plugin_version: str = Field(default="0.1.0", alias="pluginVersion")
    encrypted_packet: str = Field(alias="encryptedPacket")


class PluginConfig(ApiModel):
    source: str
    author: str
    project_id: str = Field(alias="projectId")
    enabled: bool
    send_interval_seconds: int = Field(alias="sendIntervalSeconds")


class IntervalSettingsIn(ApiModel):
    default_send_interval_seconds: int | None = Field(default=None, alias="defaultSendIntervalSeconds", ge=30)
    author: str | None = None
    author_send_interval_seconds: int | None = Field(default=None, alias="authorSendIntervalSeconds", ge=30)


class AuthorProfileIn(ApiModel):
    raw_author: str = Field(alias="rawAuthor", min_length=1)
    display_name: str | None = Field(default=None, alias="displayName")
    team: str | None = None
    telegram_username: str | None = Field(default=None, alias="telegramUsername")


class BreakEventIn(ApiModel):
    telegram_username: str = Field(alias="telegramUsername", min_length=1)
    event_type: str = Field(alias="eventType", pattern="^(afk|online|offline)$")
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
