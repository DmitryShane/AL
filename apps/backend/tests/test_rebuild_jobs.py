import datetime as dt

import pytest
from fastapi import BackgroundTasks, HTTPException

from al_backend.rebuild_jobs import (
    INTERRUPTED_REBUILD_JOB_ERROR,
    STALE_REBUILD_JOB_ERROR,
    mark_running_rebuild_jobs_interrupted,
    mark_stale_rebuild_jobs_failed,
)
from al_backend.routers.authors import _active_rebuild_job, rebuild_author_activity
from al_backend.services.raw_event_batching import RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE
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


def test_stale_cleanup_marks_only_stale_running_jobs_failed():
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


def test_startup_cleanup_marks_all_running_rebuild_jobs_failed():
    repo = fake_repository()
    now = dt.datetime(2026, 6, 16, 14, 45, tzinfo=dt.UTC)
    old = now - dt.timedelta(minutes=10)
    fresh = now - dt.timedelta(minutes=1)
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "stale", "status": "running", "createdAt": old, "updatedAt": old, "progress": 35})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "fresh", "status": "running", "createdAt": fresh, "updatedAt": fresh, "progress": 35})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "completed", "status": "completed", "createdAt": old, "updatedAt": old, "progress": 100})
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": "failed", "status": "failed", "createdAt": old, "updatedAt": old, "progress": 0})

    modified_count = mark_running_rebuild_jobs_interrupted(repo, now)

    assert modified_count == 2
    for job_id in ("stale", "fresh"):
        job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": job_id})
        assert job["status"] == "failed"
        assert job["progress"] == 0
        assert job["error"] == INTERRUPTED_REBUILD_JOB_ERROR
        assert job["updatedAt"] == now
        assert job["finishedAt"] == now
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "completed"})["status"] == "completed"
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": "failed"})["status"] == "failed"


def test_scoped_rebuild_reads_only_matching_raw_event_batches():
    repo = fake_repository()
    matching_received_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    other_received_at = dt.datetime(2026, 6, 16, 10, 1, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "matching-event",
            "batchId": "matching-batch",
            "author": "Future Artist",
            "date": "2026-06-16",
            "eventType": "focus",
            "occurredAtUtc": matching_received_at,
            "receivedAt": matching_received_at,
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "other-event",
            "batchId": "other-batch",
            "author": "Other Artist",
            "date": "2026-06-16",
            "eventType": "focus",
            "occurredAtUtc": other_received_at,
            "receivedAt": other_received_at,
        }
    )
    repo.db.raw_event_batches.insert_one({"batchId": "matching-batch", "author": "Future Artist", "receivedAt": matching_received_at})
    repo.db.raw_event_batches.insert_one({"batchId": "other-batch", "author": "Other Artist", "receivedAt": other_received_at})
    batch_find_queries = []
    original_batch_find = repo.db.raw_event_batches.find

    def recording_batch_find(query=None, projection=None):
        batch_find_queries.append(query or {})
        return original_batch_find(query, projection)

    repo.db.raw_event_batches.find = recording_batch_find
    repo._apply_raw_event_to_aggregates = lambda event: {}
    repo._build_event_batch_report_rows = lambda batch, delta_items, cutoff=None: []
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    assert batch_find_queries == [{"batchId": {"$in": ["matching-batch"]}}]


def test_rebuild_raw_events_uses_batch_accounting_and_reports_progress():
    repo = fake_repository()
    total_events = RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE + 2
    for index in range(total_events):
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"event-{index}",
                "batchId": f"batch-{index}",
                "author": "Future Artist",
                "date": "2026-06-16",
                "eventType": "focus",
                "occurredAtUtc": dt.datetime(2026, 6, 16, 10, 0, index % 60, tzinfo=dt.UTC),
                "receivedAt": dt.datetime(2026, 6, 16, 10, 0, index % 60, tzinfo=dt.UTC),
            }
        )

    begin_batch_sizes = []
    finish_count = 0
    progress_events = []

    def begin_batch(events):
        begin_batch_sizes.append(len(events))

    def finish_batch():
        nonlocal finish_count
        finish_count += 1

    repo._begin_raw_event_batch_accounting = begin_batch
    repo._finish_raw_event_batch_accounting = finish_batch
    repo._apply_raw_event_to_aggregates = lambda event: {}
    repo._build_event_batch_report_rows = lambda batch, delta_items, cutoff=None: []
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources(
        {"2026-06-16"},
        {"Future Artist"},
        progress_callback=lambda phase, current, total: progress_events.append((phase, current, total)),
    )

    assert begin_batch_sizes == [RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE, 2]
    assert finish_count == 2
    assert ("Rebuilding raw activity events", total_events, total_events) in progress_events


def test_rebuild_raw_event_batch_accounting_finishes_when_event_processing_fails():
    repo = fake_repository()
    for index in range(2):
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"event-{index}",
                "batchId": f"batch-{index}",
                "author": "Future Artist",
                "date": "2026-06-16",
                "eventType": "focus",
                "occurredAtUtc": dt.datetime(2026, 6, 16, 10, 0, index, tzinfo=dt.UTC),
                "receivedAt": dt.datetime(2026, 6, 16, 10, 0, index, tzinfo=dt.UTC),
            }
        )

    finish_count = 0

    def finish_batch():
        nonlocal finish_count
        finish_count += 1

    def fail_apply(event):
        raise RuntimeError("boom")

    repo._begin_raw_event_batch_accounting = lambda events: None
    repo._finish_raw_event_batch_accounting = finish_batch
    repo._apply_raw_event_to_aggregates = fail_apply
    repo._materialize_status_report_rows = lambda: None

    with pytest.raises(RuntimeError, match="boom"):
        repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    assert finish_count == 1
