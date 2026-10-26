from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_author_service
from ..models import PublisherProfileIn

from .authors import _active_rebuild_job, _rebuild_job_doc, _update_rebuild_job_progress, _finish_rebuild_job
from ..services.activity_mutation_lock import activity_mutation_lock

router = APIRouter()


@router.get("/api/v1/publisher-profiles")
def publisher_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"publisherProfiles": service.publisher_profiles(), "deviceProfiles": service.device_profiles(), "authorProfiles": service.publisher_author_candidates(), "authorLinkJob": service.db.aggregate_rebuild_jobs.find_one({"scope": "publisherAuthors"}, {"_id": 0}, sort=[("createdAt", -1)])}


@router.put("/api/v1/publisher-profiles/{raw_author}")
def upsert_publisher_profile(
    raw_author: str,
    profile: PublisherProfileIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.upsert_publisher_profile(raw_author, profile.display_name, profile.team, profile.author_color)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher profile save failed"))

    return result


@router.post("/api/v1/publisher-profiles/{raw_author}/avatar")
async def upload_publisher_avatar(
    raw_author: str,
    avatar: UploadFile = File(...),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.save_publisher_avatar(raw_author, await avatar.read(), avatar.content_type or "")

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher avatar upload failed"))

    return result


@router.put("/api/v1/publisher-profiles/{raw_author}/devices/{raw_device}")
def link_publisher_device(
    raw_author: str,
    raw_device: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.link_publisher_device(raw_author, raw_device)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher device link failed"))

    return result


@router.delete("/api/v1/publisher-profiles/{raw_author}/devices/{raw_device}")
def unlink_publisher_device(
    raw_author: str,
    raw_device: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.unlink_publisher_device(raw_author, raw_device)


def _run_publisher_author_rebuild(service: BackendServices, job_id: str) -> None:
    with activity_mutation_lock:
        job = service.db.aggregate_rebuild_jobs.find_one({"jobId": job_id})
        previous_job_id = getattr(service, "_active_rebuild_job_id", None)
        service._active_rebuild_job_id = job_id
        try:
            result = service.set_publisher_author(job["publisher"], job["author"], job["linked"])
            if not result.get("ok"):
                raise ValueError(result["error"])
            authors = sorted(set(job["authors"]) | set(result["authors"]))
            dates = sorted(set(job["dates"]) | set(result["dates"]))
            if dates:
                result = service.rebuild_aggregates_for_dates(
                    dates[0], dates=dates, authors=authors,
                    progress_callback=lambda phase, current, total: _update_rebuild_job_progress(service, job_id, phase, current, total),
                )
            _finish_rebuild_job(service, job_id, "completed", result)
        except Exception as exc:
            _finish_rebuild_job(service, job_id, "failed", error=str(exc))
        finally:
            service._active_rebuild_job_id = previous_job_id


def _assign_publisher_author(service: BackendServices, tasks: BackgroundTasks, publisher: str, author: str, linked: bool) -> dict:
    if _active_rebuild_job(service):
        raise HTTPException(status_code=409, detail="Another rebuild is already running")
    with activity_mutation_lock:
        if _active_rebuild_job(service):
            raise HTTPException(status_code=409, detail="Another rebuild is already running")
        latest = service.db.aggregate_rebuild_jobs.find_one({"scope": "publisherAuthors"}, {"_id": 0}, sort=[("createdAt", -1)])
        if latest and latest.get("status") == "failed" and (latest.get("publisher"), latest.get("author"), latest.get("linked")) != (publisher, author, linked):
            raise HTTPException(status_code=409, detail="Retry the incomplete publisher update first")
        result = service.set_publisher_author(publisher, author, linked, apply=False)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result["error"])
        job_id = str(uuid4())
        job = _rebuild_job_doc(job_id, "Update publisher authors", "publisherAuthors")
        job.update(publisher=publisher, author=author, linked=linked, authors=result["authors"], dates=result["dates"])
        service.db.aggregate_rebuild_jobs.insert_one(job)
        tasks.add_task(_run_publisher_author_rebuild, service, job_id)
        return {"ok": True, "jobId": job_id}


@router.put("/api/v1/publisher-profiles/{publisher}/authors/{author}")
def link_publisher_author(publisher: str, author: str, background_tasks: BackgroundTasks,
                          _: dict = Depends(require_permission("manageSettings")),
                          service: BackendServices = Depends(get_author_service)) -> dict:
    return _assign_publisher_author(service, background_tasks, publisher, author, True)


@router.delete("/api/v1/publisher-profiles/{publisher}/authors/{author}")
def unlink_publisher_author(publisher: str, author: str, background_tasks: BackgroundTasks,
                            _: dict = Depends(require_permission("manageSettings")),
                            service: BackendServices = Depends(get_author_service)) -> dict:
    return _assign_publisher_author(service, background_tasks, publisher, author, False)


@router.post("/api/v1/publisher-profiles/author-jobs/{job_id}/retry")
def retry_publisher_author_job(job_id: str, background_tasks: BackgroundTasks,
                               _: dict = Depends(require_permission("manageSettings")),
                               service: BackendServices = Depends(get_author_service)) -> dict:
    job = service.db.aggregate_rebuild_jobs.find_one({"jobId": job_id, "scope": "publisherAuthors"})
    if not job or job.get("status") != "failed":
        raise HTTPException(status_code=400, detail="Failed publisher author job not found")
    return _assign_publisher_author(service, background_tasks, job["publisher"], job["author"], job["linked"])
