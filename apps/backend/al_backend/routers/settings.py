from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from ..api_security import require_discord_bot_secret, require_permission, require_server_stats_permission
from ..container import BackendServices
from ..dependencies import get_settings_service
from ..models import AvatarSettingsIn, DiscordSettingsIn, IntervalSettingsIn


router = APIRouter()


@router.get("/api/v1/settings/server-stats")
def server_stats(
    _: dict = Depends(require_server_stats_permission),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.get_server_stats()


@router.post("/api/v1/settings/server-reboot")
def server_reboot(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.reboot_server()


@router.get("/api/v1/settings/openai-stats")
def openai_stats(
    background_tasks: BackgroundTasks,
    refresh: str | None = Query(None),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.get_openai_stats(refresh=refresh, background_tasks=background_tasks)


@router.get("/api/v1/settings/activity-snapshots")
def activity_snapshots_status(
    background_tasks: BackgroundTasks,
    limit_days: int = Query(30, alias="limitDays", ge=1, le=120),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    background_tasks.add_task(service.materialize_activity_author_day_summary_snapshots_locked, limit=1)
    return service.activity_snapshot_materialization_status(limit_days=limit_days)


@router.put("/api/v1/settings/avatars")
def update_avatar_settings(
    settings_in: AvatarSettingsIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.upsert_avatar_settings(settings_in.refresh_cadence)


@router.put("/api/v1/settings/intervals")
def update_intervals(
    settings_in: IntervalSettingsIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.upsert_interval_settings(
        default_send_interval_seconds=settings_in.default_send_interval_seconds,
        device_send_interval_seconds=settings_in.device_send_interval_seconds,
        idle_threshold_seconds=settings_in.idle_threshold_seconds,
        device_idle_threshold_seconds=settings_in.device_idle_threshold_seconds,
        plugin_ingest_enabled=settings_in.plugin_ingest_enabled,
        telegram_online_prompt_delay_minutes=settings_in.telegram_online_prompt_delay_minutes,
    )


@router.put("/api/v1/settings/discord")
def update_discord_settings(
    settings_in: DiscordSettingsIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=settings_in.meeting_auto_afk_timeout_seconds,
        meeting_summaries_enabled=settings_in.meeting_summaries_enabled,
        meeting_summary_min_participants=settings_in.meeting_summary_min_participants,
        meeting_summary_min_duration_seconds=settings_in.meeting_summary_min_duration_seconds,
        meeting_summary_language=settings_in.meeting_summary_language,
        meeting_summary_recipient=settings_in.meeting_summary_recipient,
        meeting_audio_retention_seconds=settings_in.meeting_audio_retention_seconds,
        meeting_summary_prompt=settings_in.meeting_summary_prompt,
        meeting_summary_telegram_template=settings_in.meeting_summary_telegram_template,
    )


@router.get("/api/v1/discord/settings")
def discord_settings(request: Request, service: BackendServices = Depends(get_settings_service)) -> dict:
    require_discord_bot_secret(request)
    return service.get_discord_settings()
