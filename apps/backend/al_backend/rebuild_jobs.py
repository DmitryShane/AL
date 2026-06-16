from __future__ import annotations

import datetime as dt
from typing import Any


REBUILD_JOB_STALE_AFTER = dt.timedelta(minutes=5)
STALE_REBUILD_JOB_ERROR = "Rebuild job was marked stale after backend restart or timeout. Start the rebuild again."


def rebuild_job_stale_cutoff(now: dt.datetime | None = None) -> dt.datetime:
    current = now or dt.datetime.now(dt.UTC)
    return current - REBUILD_JOB_STALE_AFTER


def _coerce_utc(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)

        return value.astimezone(dt.UTC)

    return None


def is_rebuild_job_stale(job: dict[str, Any], now: dt.datetime | None = None) -> bool:
    cutoff = rebuild_job_stale_cutoff(now)
    heartbeat = _coerce_utc(job.get("updatedAt")) or _coerce_utc(job.get("createdAt"))

    if heartbeat is None:
        return True

    return heartbeat < cutoff


def stale_rebuild_job_query(now: dt.datetime | None = None) -> dict[str, Any]:
    cutoff = rebuild_job_stale_cutoff(now)
    return {
        "status": "running",
        "$or": [
            {"updatedAt": {"$lt": cutoff}},
            {"updatedAt": {"$exists": False}, "createdAt": {"$lt": cutoff}},
            {"updatedAt": {"$exists": False}, "createdAt": {"$exists": False}},
        ],
    }


def mark_stale_rebuild_jobs_failed(service: Any, now: dt.datetime | None = None) -> int:
    current = now or dt.datetime.now(dt.UTC)
    result = service.db.aggregate_rebuild_jobs.update_many(
        stale_rebuild_job_query(current),
        {
            "$set": {
                "status": "failed",
                "progress": 0,
                "updatedAt": current,
                "finishedAt": current,
                "error": STALE_REBUILD_JOB_ERROR,
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)
