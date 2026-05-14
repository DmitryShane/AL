from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from ..api_security import require_discord_bot_secret, require_permission, require_server_stats_permission
from ..container import BackendServices
from ..dependencies import get_settings_service
from ..models import ActivitySnapshotsRemakeIn, AvatarSettingsIn, DiscordSettingsIn, IntervalSettingsIn


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
    limit_days: int = Query(30, alias="limitDays", ge=1, le=120),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.activity_snapshot_materialization_status(limit_days=limit_days)


@router.post("/api/v1/settings/activity-snapshots/remake")
def remake_activity_snapshots(
    remake_in: ActivitySnapshotsRemakeIn,
    background_tasks: BackgroundTasks,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    try:
        result = service.remake_activity_day_summary_snapshots_for_range(remake_in.start_date, remake_in.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    claimed = service.claim_next_activity_author_day_snapshot()
    if claimed.get("claimed"):
        background_tasks.add_task(
            service.materialize_claimed_activity_author_day_snapshot,
            claimed["date"],
            claimed["rawAuthor"],
            claimed["snapshotVersion"],
        )
    return {
        **result,
        "claimed": claimed,
        "status": service.activity_snapshot_materialization_status(limit_days=max(30, min(120, len(result["dates"]) + 7))),
    }


@router.post("/api/v1/settings/activity-snapshots/remake-all")
def remake_all_activity_snapshots(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    result = service.remake_all_activity_day_summary_snapshots()
    service.start_activity_snapshot_background_drain()
    return {
        **result,
        "status": service.activity_snapshot_materialization_status(limit_days=120),
    }


@router.delete("/api/v1/settings/activity-snapshots/old-versions")
def cleanup_old_activity_snapshots(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_settings_service),
) -> dict:
    return service.cleanup_old_activity_day_summary_snapshot_versions()


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
