import datetime as dt

import pytest
from fastapi import BackgroundTasks, HTTPException

from al_backend.rebuild_jobs import STALE_REBUILD_JOB_ERROR, mark_stale_rebuild_jobs_failed
from al_backend.routers.authors import _active_rebuild_job, rebuild_author_activity
from tests.fakes import fake_repository


def test_fresh_running_rebuild_job_blocks_new_rebuild():
    repo = fake_repository()
    now = dt.datetime.now(dt.UTC)
    repo.db.aggregate_rebuild_jobs.insert_one(
        {
            "jobId": "fresh-job",
            "label": "Rebuild Future Artist",
            "scope": "author",
            "status": "running",
            "phase": "Rebuilding raw activity events",
            "progress": 34,
            "createdAt": now,
            "updatedAt": now,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        rebuild_author_activity(
            "Future Artist",
            BackgroundTasks(),
            start_date="2026-06-16",
            end_date="2026-06-16",
            service=repo,
        )

    assert exc_info.value.status_code == 409
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "fresh-job"})["status"] == "running"


def test_stale_running_rebuild_job_is_failed_and_new_rebuild_can_start():
    repo = fake_repository()
    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    repo.db.aggregate_rebuild_jobs.insert_one(
        {
            "jobId": "stale-job",
            "label": "Rebuild Future Artist",
            "scope": "author",
            "status": "running",
            "phase": "Rebuilding raw activity events",
            "progress": 37,
            "createdAt": old,
            "updatedAt": old,
        }
    )

    result = rebuild_author_activity(
        "Future Artist",
        BackgroundTasks(),
        start_date="2026-06-16",
        end_date="2026-06-16",
        service=repo,
    )

    stale_job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": "stale-job"})
    new_job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": result["jobId"]})

    assert result["ok"] is True
    assert stale_job["status"] == "failed"
    assert stale_job["progress"] == 0
    assert stale_job["error"] == STALE_REBUILD_JOB_ERROR
    assert stale_job["finishedAt"] is not None
    assert stale_job["updatedAt"] > old
    assert new_job["status"] == "running"


def test_running_rebuild_job_without_updated_at_uses_created_at_for_stale_check():
    repo = fake_repository()
    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    repo.db.aggregate_rebuild_jobs.insert_one(
        {
            "jobId": "old-created-job",
            "label": "Rebuild Future Artist",
            "scope": "author",
            "status": "running",
            "phase": "Queued",
            "progress": 1,
            "createdAt": old,
        }
    )

    active_job = _active_rebuild_job(repo)
    stale_job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": "old-created-job"})

    assert active_job is None
    assert stale_job["status"] == "failed"
    assert stale_job["error"] == STALE_REBUILD_JOB_ERROR


def test_startup_cleanup_marks_only_stale_running_jobs_failed():
    repo = fake_repository()
    now = dt.datetime(2026, 6, 16, 14, 45, tzinfo=dt.UTC)
    old = now - dt.timedelta(minutes=10)
    fresh = now - dt.timedelta(minutes=1)
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "stale", "status": "running", "createdAt": old, "updatedAt": old, "progress": 35})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "fresh", "status": "running", "createdAt": fresh, "updatedAt": fresh, "progress": 35})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "completed", "status": "completed", "createdAt": old, "updatedAt": old, "progress": 100})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "failed", "status": "failed", "createdAt": old, "updatedAt": old, "progress": 0})

    modified_count = mark_stale_rebuild_jobs_failed(repo, now)

    assert modified_count == 1
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "stale"})["status"] == "failed"
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "fresh"})["status"] == "running"
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "completed"})["status"] == "completed"
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "failed"})["status"] == "failed"
