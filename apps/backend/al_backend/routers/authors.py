from __future__ import annotations

import datetime as dt
import traceback
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..api_security import require_permission
from ..author_avatar_cache import ensure_author_avatar_cached
from ..repositories.authors import utc_inclusive_range_for_bulk_activity_preset
from ..container import BackendServices
from ..dependencies import get_author_service
from ..models import AuthorAliasIn, AuthorProfileIn, BulkAuthorsActivityDeleteIn, DeviceProfileAliasIn, FullActivityRebuildIn


router = APIRouter()

REBUILD_PHASES = [
    "Clearing derived data",
    "Clearing scoped derived data",
    "Rebuilding snapshots",
    "Rebuilding raw activity events",
    "Rebuilding event batches",
    "Rebuilding Telegram activity",
    "Rebuilding Discord meetings",
    "Rebuilding status events",
    "Capturing day state",
]


def _active_rebuild_job(service: BackendServices) -> dict | None:
    return service.db.aggregate_rebuild_jobs.find_one({"status": "running"}, {"_id": 0})


def _rebuild_job_doc(job_id: str, label: str, scope: str) -> dict:
    now = dt.datetime.now(dt.UTC)
    return {
        "jobId": job_id,
        "label": label,
        "scope": scope,
        "status": "running",
        "phase": "Queued",
        "progress": 1,
        "createdAt": now,
        "updatedAt": now,
    }


def _rebuild_progress_percent(phase: str, current: int, total: int) -> int:
    phase_index = REBUILD_PHASES.index(phase) if phase in REBUILD_PHASES else 0
    phase_fraction = 1.0 if total <= 0 else max(0.0, min(1.0, current / total))
    return max(1, min(95, int(((phase_index + phase_fraction) / len(REBUILD_PHASES)) * 95)))


def _update_rebuild_job_progress(service: BackendServices, job_id: str, phase: str, current: int, total: int) -> None:
    service.db.aggregate_rebuild_jobs.update_one(
        {"jobId": job_id},
        {
            "$set": {
                "phase": phase,
                "progress": _rebuild_progress_percent(phase, current, total),
                "current": current,
                "total": total,
                "updatedAt": dt.datetime.now(dt.UTC),
            }
        },
    )


def _finish_rebuild_job(service: BackendServices, job_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    payload: dict = {
        "status": status,
        "progress": 100 if status == "completed" else 0,
        "updatedAt": dt.datetime.now(dt.UTC),
        "finishedAt": dt.datetime.now(dt.UTC),
    }

    if result is not None:
        payload["result"] = result

    if error is not None:
        payload["error"] = error

    service.db.aggregate_rebuild_jobs.update_one({"jobId": job_id}, {"$set": payload})


def _run_full_rebuild_job(service: BackendServices, job_id: str) -> None:
    try:
        service.rebuild_aggregates_if_needed(
            force=True,
            progress_callback=lambda phase, current, total: _update_rebuild_job_progress(service, job_id, phase, current, total),
        )
        _finish_rebuild_job(service, job_id, "completed", {"ok": True, "scope": "full"})
    except Exception as exc:
        _finish_rebuild_job(service, job_id, "failed", error=f"{exc}\n{traceback.format_exc()}")


def _run_scoped_rebuild_job(service: BackendServices, job_id: str, raw_author: str, start_date: str, end_date: str) -> None:
    try:
        result = service.rebuild_aggregates_for_dates(
            start_date=start_date,
            end_date=end_date,
            authors=[raw_author],
            progress_callback=lambda phase, current, total: _update_rebuild_job_progress(service, job_id, phase, current, total),
        )
        _finish_rebuild_job(service, job_id, "completed", result)
    except Exception as exc:
        _finish_rebuild_job(service, job_id, "failed", error=f"{exc}\n{traceback.format_exc()}")


@router.get("/api/v1/avatars/author")
def author_avatar_file(
    raw_author: str = Query(..., alias="rawAuthor", min_length=1),
    _: dict = Depends(require_permission("viewDashboard")),
    service: BackendServices = Depends(get_author_service),
) -> FileResponse:
    cadence = service.get_avatar_refresh_cadence()
    path, media_type = ensure_author_avatar_cached(
        service.db,
        service.avatar_cache_dir,
        raw_author,
        cadence=cadence,
        force=False,
    )

    if not path:
        raise HTTPException(status_code=404, detail="Avatar not available")

    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.post("/api/v1/authors/avatars/refresh-all")
def refresh_all_author_avatars(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.refresh_all_author_github_avatars()


@router.post("/api/v1/authors/activity/bulk-delete")
def bulk_delete_activity_all_authors(
    body: BulkAuthorsActivityDeleteIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    expected = "DELETE ALL ACTIVITY" if body.preset == "full" else "delete"

    if body.confirm_phrase.strip() != expected:
        raise HTTPException(status_code=400, detail="Confirmation phrase does not match")

    if body.preset == "full":
        return service.wipe_all_authors_activity_data()

    start_date, end_date = utc_inclusive_range_for_bulk_activity_preset(body.preset)
    return service.bulk_delete_activity_all_authors_for_range(start_date, end_date)


@router.post("/api/v1/authors/activity/rebuild")
def rebuild_activity_all_authors(
    body: FullActivityRebuildIn,
    background_tasks: BackgroundTasks,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    if body.confirm_phrase.strip() != "REBUILD ALL ACTIVITY":
        raise HTTPException(status_code=400, detail="Confirmation phrase does not match")

    active_job = _active_rebuild_job(service)

    if active_job:
        raise HTTPException(status_code=409, detail="Another rebuild is already running")

    job_id = uuid.uuid4().hex
    service.db.aggregate_rebuild_jobs.insert_one(_rebuild_job_doc(job_id, "Rebuild full DB", "full"))
    background_tasks.add_task(_run_full_rebuild_job, service, job_id)
    return {"ok": True, "jobId": job_id}


@router.get("/api/v1/authors/activity/rebuild/status")
def rebuild_activity_status(
    job_id: str | None = Query(None, alias="jobId"),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    query = {"jobId": job_id} if job_id else {}
    job = service.db.aggregate_rebuild_jobs.find_one(query, {"_id": 0}, sort=[("createdAt", -1)])

    if not job:
        return {"ok": True, "job": None}

    return {"ok": True, "job": job}


@router.post("/api/v1/authors/{raw_author}/activity/rebuild")
def rebuild_author_activity(
    raw_author: str,
    background_tasks: BackgroundTasks,
    start_date: str = Query(..., alias="startDate"),
    end_date: str = Query(..., alias="endDate"),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    active_job = _active_rebuild_job(service)

    if active_job:
        raise HTTPException(status_code=409, detail="Another rebuild is already running")

    dt.date.fromisoformat(start_date)
    dt.date.fromisoformat(end_date)
    job_id = uuid.uuid4().hex
    service.db.aggregate_rebuild_jobs.insert_one(_rebuild_job_doc(job_id, f"Rebuild {raw_author}", "author"))
    background_tasks.add_task(_run_scoped_rebuild_job, service, job_id, raw_author, start_date, end_date)
    return {"ok": True, "jobId": job_id}


@router.post("/api/v1/authors/{raw_author}/avatar/refresh")
def refresh_author_avatar(
    raw_author: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.refresh_author_github_avatar(raw_author)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Avatar refresh failed"))

    return result


@router.put("/api/v1/authors/profile")
def upsert_author_profile(
    profile: AuthorProfileIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.upsert_author_profile(
        raw_author=profile.raw_author,
        display_name=profile.display_name,
        team=profile.team,
        telegram_username=profile.telegram_username,
        discord_user_id=profile.discord_user_id,
        discord_username=profile.discord_username,
        plugin_enabled=profile.plugin_enabled,
        auto_break_enabled=profile.auto_break_enabled,
        author_color=profile.author_color,
        github_username=profile.github_username,
    )


@router.get("/api/v1/authors/profiles")
def author_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"profiles": service.author_profiles()}


@router.get("/api/v1/authors/aliases")
def author_aliases(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"aliases": service.author_aliases()}


@router.get("/api/v1/authors/device-profiles")
def device_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"deviceProfiles": service.device_profiles()}


@router.post("/api/v1/authors/device-profiles/migrate-author-profiles")
def migrate_device_author_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.migrate_device_author_profiles()


@router.put("/api/v1/authors/device-profiles/{raw_device}/alias")
def upsert_device_profile_alias(
    raw_device: str,
    alias: DeviceProfileAliasIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.upsert_device_profile_alias(raw_device, alias.target_raw_author)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Device alias save failed"))

    return result


@router.delete("/api/v1/authors/device-profiles/{raw_device}")
def delete_device_profile(
    raw_device: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.delete_device_profile(raw_device)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Device profile delete failed"))

    return result


@router.delete("/api/v1/authors/device-profiles")
def delete_all_device_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.delete_all_device_profiles()


@router.put("/api/v1/authors/aliases")
def upsert_author_alias(
    alias: AuthorAliasIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.upsert_author_alias(
        source_raw_author=alias.source_raw_author,
        target_raw_author=alias.target_raw_author,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Alias save failed"))

    return result


@router.delete("/api/v1/authors/aliases/{source_raw_author}")
def delete_author_alias(
    source_raw_author: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.delete_author_alias(source_raw_author=source_raw_author)


@router.delete("/api/v1/authors/{raw_author}/data")
def delete_author_data(
    raw_author: str,
    start_date: str | None = Query(None, alias="startDate"),
    end_date: str | None = Query(None, alias="endDate"),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    if start_date is None and end_date is None:
        return service.delete_author_data(raw_author=raw_author)

    if start_date is None or end_date is None:
        raise HTTPException(status_code=422, detail="Both startDate and endDate are required for ranged delete")

    result = service.delete_author_data_for_date_range(raw_author=raw_author, start_date=start_date, end_date=end_date)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Ranged delete failed"))

    return result


@router.delete("/api/v1/authors/{raw_author}/profile")
def delete_author_profile(
    raw_author: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.delete_author_profile(raw_author=raw_author)
