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
from al_backend.services import activity_raw_event_accounting as raw_accounting_module
from al_backend.services import activity_aggregation_rebuild as rebuild_module
from al_backend.services.raw_event_batching import RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE, REBUILD_CURSOR_BATCH_SIZE, REBUILD_RAW_FLUSH_BATCHES
from tests.fakes import fake_repository


def _normalize_docs(items):
    def normalize(value):
        if isinstance(value, dt.datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize(value[key]) for key in sorted(value)}
        return value

    return sorted(
        [normalize({key: value for key, value in item.items() if key != "_id"}) for item in items],
        key=lambda item: repr(item),
    )


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


def test_scoped_rebuild_compacts_high_frequency_editor_input_before_rebuild():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    base_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)

    for index in range(100):
        occurred_at = base_at + dt.timedelta(milliseconds=index * 100)
        repo.db.raw_activity_events.insert_one(
            {
                "_id": f"raw-editor-input-{index}",
                "eventId": f"editor-input-{index}",
                "batchId": "batch-1",
                "author": author,
                "date": day,
                "source": "ual",
                "eventType": "editor_input",
                "projectId": "bike-rush-2",
                "sessionId": "unity-session",
                "deviceId": "unity-device",
                "timeZoneId": "UTC",
                "occurredAtUtc": occurred_at,
                "occurredAtLocal": occurred_at.isoformat(),
                "receivedAt": occurred_at,
                "metadata": {"state": "KeyDown", "keyCode": str(index)},
            }
        )

    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "navigation-1",
            "batchId": "batch-1",
            "author": author,
            "date": day,
            "source": "ual",
            "eventType": "scene_view_navigation",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": base_at,
            "receivedAt": base_at,
        }
    )

    repo._rebuild_aggregates_from_sources = lambda *args, **kwargs: None
    result = repo.rebuild_aggregates_for_dates(day, dates=[day], authors=[author])
    editor_inputs = list(repo.db.raw_activity_events.find({"author": author, "date": day, "eventType": "editor_input"}))
    navigation = repo.db.raw_activity_events.find_one({"eventId": "navigation-1"})

    assert result["rawEventsBeforeCompaction"] == 101
    assert result["rawEventsAfterCompaction"] == 2
    assert result["compactedEvents"] == 99
    assert result["deletedEditorInputEvents"] == 100
    assert result["insertedCompactedEvents"] == 1
    assert len(editor_inputs) == 1
    assert editor_inputs[0]["metadata"]["coalescedFromRawEvents"] is True
    assert editor_inputs[0]["metadata"]["coalescedEventCount"] == 100
    assert editor_inputs[0]["metadata"]["keyCode"] == "99"
    assert editor_inputs[0].get("_id") not in {"raw-editor-input-0", "raw-editor-input-99"}
    assert navigation is not None


def test_editor_input_compaction_is_idempotent_and_keeps_group_boundaries():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    base_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)

    for batch_id in ("batch-a", "batch-b"):
        for index in range(2):
            occurred_at = base_at + dt.timedelta(seconds=index)
            repo.db.raw_activity_events.insert_one(
                {
                    "eventId": f"{batch_id}-{index}",
                    "batchId": batch_id,
                    "author": author,
                    "date": day,
                    "source": "ual",
                    "eventType": "editor_input",
                    "projectId": "bike-rush-2",
                    "sessionId": "unity-session",
                    "deviceId": "unity-device",
                    "timeZoneId": "UTC",
                    "occurredAtUtc": occurred_at,
                    "receivedAt": occurred_at,
                    "metadata": {"state": "KeyDown"},
                }
            )

    first = repo.compact_editor_input_events_for_rebuild({day}, {author})
    second = repo.compact_editor_input_events_for_rebuild({day}, {author})
    editor_inputs = list(repo.db.raw_activity_events.find({"author": author, "date": day, "eventType": "editor_input"}))

    assert first["insertedCompactedEvents"] == 2
    assert second["insertedCompactedEvents"] == 0
    assert len(editor_inputs) == 2
    assert sorted(event["batchId"] for event in editor_inputs) == ["batch-a", "batch-b"]
    assert all(event["metadata"]["coalescedEventCount"] == 2 for event in editor_inputs)


def test_events_dropped_compaction_is_idempotent_and_sums_dropped_count():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    base_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)

    for index in range(20):
        occurred_at = base_at + dt.timedelta(milliseconds=index)
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"events-dropped-{index}",
                "batchId": "batch-overflow",
                "author": author,
                "date": day,
                "source": "ual",
                "eventType": "events_dropped",
                "projectId": "bike-rush-2",
                "sessionId": "unity-session",
                "deviceId": "unity-device",
                "timeZoneId": "UTC",
                "occurredAtUtc": occurred_at,
                "occurredAtLocal": occurred_at.isoformat(),
                "receivedAt": occurred_at,
                "metadata": {"state": "pending_events_overflow", "clickCount": 2},
            }
        )

    first = repo.compact_events_dropped_for_rebuild({day}, {author})
    second = repo.compact_events_dropped_for_rebuild({day}, {author})
    dropped_events = list(repo.db.raw_activity_events.find({"author": author, "date": day, "eventType": "events_dropped"}))

    assert first["insertedCompactedEventsDropped"] == 1
    assert first["deletedEventsDropped"] == 20
    assert first["compactedEventsDropped"] == 19
    assert second["insertedCompactedEventsDropped"] == 0
    assert len(dropped_events) == 1
    assert dropped_events[0]["metadata"]["coalescedFromRawEvents"] is True
    assert dropped_events[0]["metadata"]["coalescedEventCount"] == 20
    assert dropped_events[0]["metadata"]["coalescedDroppedEventCount"] == 40
    assert dropped_events[0]["metadata"]["clickCount"] == 40


def test_rebuild_raw_events_uses_batch_accounting_and_reports_progress():
    repo = fake_repository()
    total_events = RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE + 2
    assert total_events < RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE * REBUILD_RAW_FLUSH_BATCHES
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

    assert begin_batch_sizes == [total_events]
    assert finish_count == 1
    assert ("Rebuilding raw activity events", total_events, total_events) in progress_events


def test_rebuild_hot_cursors_use_configured_batch_size():
    repo = fake_repository()
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "event-1",
            "batchId": "batch-1",
            "author": "Future Artist",
            "date": "2026-06-16",
            "eventType": "focus",
            "occurredAtUtc": event_at,
            "receivedAt": event_at,
        }
    )
    repo.db.raw_event_batches.insert_one({"batchId": "batch-1", "author": "Future Artist", "receivedAt": event_at})
    cursors = []
    original_raw_find = repo.db.raw_activity_events.find
    original_batch_find = repo.db.raw_event_batches.find

    def recording_raw_find(*args, **kwargs):
        cursor = original_raw_find(*args, **kwargs)
        cursors.append(cursor)
        return cursor

    def recording_batch_find(*args, **kwargs):
        cursor = original_batch_find(*args, **kwargs)
        cursors.append(cursor)
        return cursor

    repo.db.raw_activity_events.find = recording_raw_find
    repo.db.raw_event_batches.find = recording_batch_find
    repo._apply_raw_event_to_aggregates = lambda event: {}
    repo._build_event_batch_report_rows = lambda batch, delta_items, cutoff=None: []
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    assert any(cursor.batch_size_value == REBUILD_CURSOR_BATCH_SIZE for cursor in cursors)


def test_rebuild_raw_phase_records_timing_diagnostics():
    repo = fake_repository()
    job_id = "raw-diagnostics-job"
    repo._active_rebuild_job_id = job_id
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": job_id, "status": "running"})
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "event-1",
            "batchId": "batch-1",
            "author": "Future Artist",
            "date": "2026-06-16",
            "source": "ual",
            "eventType": "focus",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "receivedAt": event_at,
        }
    )
    repo.db.raw_event_batches.insert_one({"batchId": "batch-1", "author": "Future Artist", "source": "ual", "receivedAt": event_at})
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": job_id})
    assert job["rawFlushCount"] == 1
    assert job["rawStateWrites"] >= 1
    assert "rawEventAccounting" in job["rawTiming"]
    assert "stateFlush" in job["rawTiming"]
    assert job["rawEventsPerSecond"] > 0
    assert job["rawFastAccountingEnabled"] is True


def test_rebuild_raw_phase_records_slow_event_diagnostics(monkeypatch):
    repo = fake_repository()
    job_id = "raw-slow-event-job"
    repo._active_rebuild_job_id = job_id
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": job_id, "status": "running"})
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "event-1",
            "batchId": "batch-1",
            "author": "Future Artist",
            "date": "2026-06-16",
            "source": "ual",
            "eventType": "editor_input",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "receivedAt": event_at,
        }
    )
    repo.db.raw_event_batches.insert_one({"batchId": "batch-1", "author": "Future Artist", "source": "ual", "receivedAt": event_at})
    repo._materialize_status_report_rows = lambda: None
    monkeypatch.setattr(rebuild_module, "REBUILD_SLOW_EVENT_THRESHOLD_MS", 1)
    original_apply = repo._apply_raw_event_to_aggregates

    def slow_apply(event):
        import time

        time.sleep(0.002)
        return original_apply(event)

    repo._apply_raw_event_to_aggregates = slow_apply

    repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": job_id})
    assert job["status"] == "running"
    assert job["rawSlowEvents"]
    assert job["rawSlowEvents"][0]["eventType"] == "editor_input"
    assert job["rawSlowEvents"][0]["durationMs"] >= 1


def test_fast_raw_accounting_matches_legacy_rebuild_outputs(monkeypatch):
    author = "Denis Ostrovskiy"
    day = "2026-06-16"
    base_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)

    def seed(repo):
        repo.db.raw_event_batches.insert_one(
            {
                "batchId": "batch-1",
                "author": author,
                "source": "ual",
                "projectId": "bike-rush-2",
                "receivedAt": base_at + dt.timedelta(minutes=12),
            }
        )
        for index, (event_type, offset_seconds, metadata) in enumerate(
            [
                ("focus", 0, {}),
                ("editor_input", 60, {"coalescedEventCount": 3}),
                ("click", 120, {}),
                ("scene_view_navigation", 180, {"navigationDurationSeconds": 30, "firstNavigationAtUtc": (base_at + dt.timedelta(seconds=150)).isoformat()}),
                ("heartbeat", 900, {}),
            ],
            start=1,
        ):
            occurred_at = base_at + dt.timedelta(seconds=offset_seconds)
            repo.db.raw_activity_events.insert_one(
                {
                    "eventId": f"event-{index}",
                    "batchId": "batch-1",
                    "author": author,
                    "authorEmail": "vedamir.infinum@gmail.com",
                    "date": day,
                    "source": "ual",
                    "eventType": event_type,
                    "projectId": "bike-rush-2",
                    "sessionId": "unity-session",
                    "deviceId": "unity-device",
                    "timeZoneId": "UTC",
                    "occurredAtUtc": occurred_at,
                    "occurredAtLocal": occurred_at.isoformat(),
                    "receivedAt": occurred_at,
                    "metadata": metadata,
                }
            )

    def run_with_fast_flag(enabled):
        monkeypatch.setattr(raw_accounting_module, "REBUILD_FAST_ACCOUNTING_ENABLED", enabled)
        repo = fake_repository()
        seed(repo)
        repo._materialize_status_report_rows = lambda: None
        repo._rebuild_aggregates_from_sources({day}, {author})
        return {
            "report_rows": _normalize_docs(repo.db.report_rows.items),
            "daily_author_activity": _normalize_docs(repo.db.daily_author_activity.items),
            "aggregate_session_state": _normalize_docs(repo.db.aggregate_session_state.items),
        }

    legacy = run_with_fast_flag(False)
    fast = run_with_fast_flag(True)

    assert fast == legacy


def test_rebuild_interval_accumulator_matches_legacy_for_noop_heartbeat(monkeypatch):
    author = "Denis Ostrovskiy"
    day = "2026-06-16"
    base_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)

    def seed(repo):
        repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})
        repo.db.raw_event_batches.insert_one(
            {
                "batchId": "batch-1",
                "author": author,
                "source": "ual",
                "projectId": "bike-rush-2",
                "receivedAt": base_at + dt.timedelta(seconds=30),
            }
        )
        for index, (event_type, offset_seconds) in enumerate([("editor_input", 0), ("heartbeat", 30)], start=1):
            occurred_at = base_at + dt.timedelta(seconds=offset_seconds)
            repo.db.raw_activity_events.insert_one(
                {
                    "eventId": f"event-{index}",
                    "batchId": "batch-1",
                    "author": author,
                    "date": day,
                    "source": "ual",
                    "eventType": event_type,
                    "projectId": "bike-rush-2",
                    "sessionId": "unity-session",
                    "deviceId": "unity-device",
                    "timeZoneId": "UTC",
                    "occurredAtUtc": occurred_at,
                    "occurredAtLocal": occurred_at.isoformat(),
                    "receivedAt": occurred_at,
                }
            )

    def run_with_accumulator(enabled):
        monkeypatch.setattr(raw_accounting_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        monkeypatch.setattr(rebuild_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        repo = fake_repository()
        seed(repo)
        repo._materialize_status_report_rows = lambda: None
        repo._rebuild_aggregates_from_sources({day}, {author})
        return repo, {
            "report_rows": _normalize_docs(repo.db.report_rows.items),
            "daily_author_activity": _normalize_docs(repo.db.daily_author_activity.items),
            "aggregate_session_state": _normalize_docs(repo.db.aggregate_session_state.items),
        }

    legacy_repo, legacy = run_with_accumulator(False)
    fast_repo, fast = run_with_accumulator(True)

    assert fast == legacy
    assert fast_repo._last_rebuild_metrics["rawAccumulatorEventsByType"] == {"ual:heartbeat": 1}
    assert legacy_repo._last_rebuild_metrics["rawAccumulatorEvents"] == 0


def test_rebuild_interval_accumulator_kill_switch_routes_events_to_legacy(monkeypatch):
    monkeypatch.setattr(raw_accounting_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", False)
    monkeypatch.setattr(rebuild_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", False)
    repo = fake_repository()
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "overflow-1",
            "batchId": "batch-overflow",
            "author": "Future Artist",
            "date": "2026-06-16",
            "source": "ual",
            "eventType": "events_dropped",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "occurredAtLocal": event_at.isoformat(),
            "receivedAt": event_at,
            "metadata": {"state": "pending_events_overflow", "clickCount": 40, "coalescedEventCount": 20},
        }
    )
    repo.db.raw_event_batches.insert_one({"batchId": "batch-overflow", "author": "Future Artist", "source": "ual", "receivedAt": event_at})
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources({"2026-06-16"}, {"Future Artist"})

    assert repo._last_rebuild_metrics["rawIntervalAccumulatorEnabled"] is False
    assert repo._last_rebuild_metrics["rawAccumulatorEvents"] == 0
    assert repo._last_rebuild_metrics["rawLegacyFallbackEventsByType"] == {"ual:events_dropped": 1}


def test_fast_events_dropped_accounting_updates_state_without_activity_totals():
    repo = fake_repository()
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo._begin_raw_event_batch_accounting([])

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "eventId": "overflow-1",
            "batchId": "batch-overflow",
            "author": "Future Artist",
            "date": "2026-06-16",
            "source": "ual",
            "eventType": "events_dropped",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "occurredAtLocal": event_at.isoformat(),
            "receivedAt": event_at,
            "metadata": {"state": "pending_events_overflow", "clickCount": 40, "coalescedEventCount": 20},
        }
    )
    repo._finish_raw_event_batch_accounting()

    assert deltas["activeDeltaMicroseconds"] == 0
    assert deltas["idleDeltaMicroseconds"] == 0
    assert deltas["breakDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaMicroseconds"] == 0
    assert deltas["activityCountDeltas"] == []
    assert deltas["savedPrefabDeltas"] == []
    assert deltas["overtimeActivityCountDeltas"] == []
    assert deltas["overtimeSavedPrefabDeltas"] == []
    assert repo.db.daily_author_activity.items == []
    assert repo.db.aggregate_session_state.items


def test_coalesced_editor_input_preserves_activity_count():
    repo = fake_repository()
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "eventId": "coalesced-editor-input",
            "author": "Future Artist",
            "date": "2026-06-16",
            "source": "ual",
            "eventType": "editor_input",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "receivedAt": event_at,
            "metadata": {"coalescedFromRawEvents": True, "coalescedEventCount": 42},
        }
    )

    assert deltas["activityCountDeltas"] == [{"type": "editor_input", "count": 42}]


def test_rebuild_delta_store_spills_and_cleans_temp_collection():
    repo = fake_repository()
    store = rebuild_module._RebuildDeltaStore(repo, "token-1", threshold=1)
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    event = {
        "author": "Future Artist",
        "date": "2026-06-16",
        "source": "ual",
        "batchId": "batch-1",
        "occurredAtUtc": event_at,
        "receivedAt": event_at,
    }

    store.add("batch-1", event, {"activeDeltaSeconds": 1})
    store.add("batch-1", event, {"activeDeltaSeconds": 2})

    assert store.spilled is True
    assert len(repo.db.aggregate_rebuild_event_deltas.items) == 2
    assert [deltas for _event, deltas in store.get_batch("batch-1")] == [
        {"activeDeltaSeconds": 1},
        {"activeDeltaSeconds": 2},
    ]

    store.cleanup()

    assert repo.db.aggregate_rebuild_event_deltas.items == []


def test_raw_event_batch_accounting_flushes_state_and_daily_with_bulk_write():
    repo = fake_repository()
    calls = {"state_bulk": 0, "daily_bulk": 0, "state_update": 0, "daily_update": 0}
    original_state_bulk = repo.db.aggregate_session_state.bulk_write
    original_daily_bulk = repo.db.daily_author_activity.bulk_write
    original_state_update = repo.db.aggregate_session_state.update_one
    original_daily_update = repo.db.daily_author_activity.update_one

    def counting_state_bulk(*args, **kwargs):
        calls["state_bulk"] += 1
        return original_state_bulk(*args, **kwargs)

    def counting_daily_bulk(*args, **kwargs):
        calls["daily_bulk"] += 1
        return original_daily_bulk(*args, **kwargs)

    def counting_state_update(*args, **kwargs):
        calls["state_update"] += 1
        return original_state_update(*args, **kwargs)

    def counting_daily_update(*args, **kwargs):
        calls["daily_update"] += 1
        return original_daily_update(*args, **kwargs)

    repo.db.aggregate_session_state.bulk_write = counting_state_bulk
    repo.db.daily_author_activity.bulk_write = counting_daily_bulk
    repo.db.aggregate_session_state.update_one = counting_state_update
    repo.db.daily_author_activity.update_one = counting_daily_update
    repo._begin_raw_event_batch_accounting([])
    repo._set_batch_state_doc("state-1", {"author": "Future Artist", "date": "2026-06-16", "state": {}, "updatedAt": dt.datetime.now(dt.UTC)})
    repo._set_batch_daily_doc(
        "ual|Future Artist|bike-rush-2|2026-06-16",
        {"source": "ual", "author": "Future Artist", "projectId": "bike-rush-2", "date": "2026-06-16"},
    )

    repo._finish_raw_event_batch_accounting()

    assert calls == {"state_bulk": 1, "daily_bulk": 1, "state_update": 0, "daily_update": 0}
    assert repo.db.aggregate_session_state.find_one({"_id": "state-1"}) is not None
    assert repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-06-16"}) is not None
    assert repo._last_raw_event_batch_accounting_stats["rawStateWrites"] == 1
    assert repo._last_raw_event_batch_accounting_stats["rawDailyWrites"] == 1


def test_raw_event_accounting_without_context_keeps_immediate_writes():
    repo = fake_repository()
    calls = {"state_update": 0, "daily_update": 0}
    original_state_update = repo.db.aggregate_session_state.update_one
    original_daily_update = repo.db.daily_author_activity.update_one

    def counting_state_update(*args, **kwargs):
        calls["state_update"] += 1
        return original_state_update(*args, **kwargs)

    def counting_daily_update(*args, **kwargs):
        calls["daily_update"] += 1
        return original_daily_update(*args, **kwargs)

    repo.db.aggregate_session_state.update_one = counting_state_update
    repo.db.daily_author_activity.update_one = counting_daily_update

    repo._set_batch_state_doc("state-1", {"author": "Future Artist", "date": "2026-06-16", "state": {}, "updatedAt": dt.datetime.now(dt.UTC)})
    repo._set_batch_daily_doc(
        "ual|Future Artist|bike-rush-2|2026-06-16",
        {"source": "ual", "author": "Future Artist", "projectId": "bike-rush-2", "date": "2026-06-16"},
    )

    assert calls == {"state_update": 1, "daily_update": 1}


def test_fast_raw_event_batch_accounting_defers_daily_merge_until_finish():
    repo = fake_repository()
    calls = {"daily_bulk": 0, "daily_update": 0}
    original_daily_bulk = repo.db.daily_author_activity.bulk_write
    original_daily_update = repo.db.daily_author_activity.update_one

    def counting_daily_bulk(*args, **kwargs):
        calls["daily_bulk"] += 1
        return original_daily_bulk(*args, **kwargs)

    def counting_daily_update(*args, **kwargs):
        calls["daily_update"] += 1
        return original_daily_update(*args, **kwargs)

    repo.db.daily_author_activity.bulk_write = counting_daily_bulk
    repo.db.daily_author_activity.update_one = counting_daily_update
    repo._begin_raw_event_batch_accounting([])
    snapshot = {
        "source": "ual",
        "author": "Future Artist",
        "projectId": "bike-rush-2",
        "date": "2026-06-16",
        "receivedAt": dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC),
        "recordedAt": "2026-06-16T10:00:00+00:00",
    }
    deltas = {
        "activeDeltaSeconds": 1,
        "activeDeltaMicroseconds": 1_000_000,
        "idleDeltaSeconds": 0,
        "idleDeltaMicroseconds": 0,
        "breakDeltaSeconds": 0,
        "overtimeActiveDeltaSeconds": 0,
        "overtimeActiveDeltaMicroseconds": 0,
        "hourlyActivityDelta": [],
        "activityCountDeltas": [{"type": "editor_input", "count": 2}],
        "savedPrefabDeltas": [],
        "overtimeActivityCountDeltas": [],
        "overtimeSavedPrefabDeltas": [],
    }

    repo._update_daily_author_activity(snapshot, deltas)

    assert repo.db.daily_author_activity.items == []
    assert calls == {"daily_bulk": 0, "daily_update": 0}

    repo._finish_raw_event_batch_accounting()

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-06-16"})
    assert calls == {"daily_bulk": 1, "daily_update": 0}
    assert daily["activeMicroseconds"] == 1_000_000
    assert daily["activityCounts"] == [{"type": "editor_input", "count": 2}]
    assert repo._last_raw_event_batch_accounting_stats["rawFastAccountingEnabled"] is True
    assert repo._last_raw_event_batch_accounting_stats["rawDailyMergeFlushSeconds"] >= 0


def test_raw_event_batch_accounting_fast_flag_disabled_keeps_immediate_batch_daily_merge(monkeypatch):
    monkeypatch.setattr(raw_accounting_module, "REBUILD_FAST_ACCOUNTING_ENABLED", False)
    repo = fake_repository()
    repo._begin_raw_event_batch_accounting([])
    snapshot = {
        "source": "ual",
        "author": "Future Artist",
        "projectId": "bike-rush-2",
        "date": "2026-06-16",
        "receivedAt": dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC),
        "recordedAt": "2026-06-16T10:00:00+00:00",
    }
    deltas = {
        "activeDeltaSeconds": 1,
        "activeDeltaMicroseconds": 1_000_000,
        "idleDeltaSeconds": 0,
        "idleDeltaMicroseconds": 0,
        "breakDeltaSeconds": 0,
        "overtimeActiveDeltaSeconds": 0,
        "overtimeActiveDeltaMicroseconds": 0,
        "hourlyActivityDelta": [],
        "activityCountDeltas": [],
        "savedPrefabDeltas": [],
        "overtimeActivityCountDeltas": [],
        "overtimeSavedPrefabDeltas": [],
    }

    repo._update_daily_author_activity(snapshot, deltas)

    assert repo._raw_event_batch_accounting["pendingDailyDeltas"] == {}
    assert repo._raw_event_batch_accounting["daily"]
    repo._finish_raw_event_batch_accounting()
    assert repo._last_raw_event_batch_accounting_stats["rawFastAccountingEnabled"] is False


def test_rebuild_event_batches_do_not_use_live_ingest_batch_builder():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "raw-1",
            "batchId": "batch-1",
            "author": author,
            "date": day,
            "source": "ual",
            "eventType": "editor_input",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "timeZoneId": "UTC",
            "occurredAtUtc": event_at,
            "receivedAt": event_at,
        }
    )
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": "batch-1",
            "author": author,
            "date": day,
            "source": "ual",
            "pluginVersion": "test",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "receivedAt": event_at,
        }
    )
    repo._apply_raw_event_to_aggregates = lambda event: {"activeDeltaSeconds": 1}
    repo._build_event_batch_report_rows = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live builder used"))
    repo._materialize_status_report_rows = lambda: None

    repo._rebuild_aggregates_from_sources({day}, {author})

    row = repo.db.report_rows.find_one({"batchId": "batch-1"})
    assert row is not None
    assert row["activeDeltaSeconds"] == 1
    assert repo.db.aggregate_rebuild_event_deltas.items == []


def test_rebuild_event_batch_materializer_updates_inner_batch_progress(monkeypatch):
    repo = fake_repository()
    job_id = "job-progress"
    repo._active_rebuild_job_id = job_id
    repo.db.aggregate_rebuild_jobs.insert_one({"jobId": job_id, "status": "running"})
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    items = [
        (
            {
                "date": "2026-06-16",
                "occurredAtUtc": event_at + dt.timedelta(seconds=index),
                "timeZoneId": "UTC",
            },
            {"activeDeltaSeconds": 1},
        )
        for index in range(3)
    ]
    progress_calls = []
    monkeypatch.setattr(rebuild_module, "REBUILD_BATCH_DELTA_CHUNK_SIZE", 2)

    rows = repo._build_rebuild_event_batch_report_rows(
        {"batchId": "batch-1", "source": "ual", "author": "Future Artist", "receivedAt": event_at},
        iter(items),
        None,
        lambda phase, current, total: progress_calls.append((phase, current, total)),
        batch_index=1,
        batch_total=1,
        batch_delta_total=3,
    )

    job = repo.db.aggregate_rebuild_jobs.find_one({"jobId": job_id})
    assert rows[0]["activeDeltaSeconds"] == 2
    assert progress_calls == [("Rebuilding event batches", 1, 1)]
    assert job["currentBatch"] == 1
    assert job["totalBatches"] == 1
    assert job["currentBatchEvents"] == 3
    assert job["totalBatchEvents"] == 3


def test_rebuild_memory_guard_records_pause(monkeypatch):
    repo = fake_repository()
    metrics = {"memoryGuardPauses": 0, "rssPeakMb": 0}
    pauses = []

    monkeypatch.setattr(rebuild_module, "_rebuild_current_rss_mb", lambda: 999)
    monkeypatch.setattr(rebuild_module.time, "sleep", lambda seconds: pauses.append(seconds))

    repo._maybe_apply_rebuild_memory_guard(metrics)

    assert metrics["rssPeakMb"] == 999
    assert metrics["memorySoftLimitMb"] == 400
    assert metrics["memoryGuardPauses"] == 1
    assert pauses == [0.05]


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


def test_raw_event_batch_accounting_caches_author_day_context_lookups():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    repo.db.day_sessions.insert_one({"rawAuthor": author, "date": day, "startedAt": dt.datetime(2026, 6, 16, 9, 0, tzinfo=dt.UTC)})
    repo.db.status_events.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "statusEventType": "offline",
            "reason": "reports_stopped",
            "transitionAt": dt.datetime(2026, 6, 16, 9, 30, tzinfo=dt.UTC),
        }
    )
    repo.db.break_events.insert_one(
        {
            "rawAuthor": author,
            "date": "2026-06-15",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 6, 15, 23, 59, tzinfo=dt.UTC),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "author": author,
            "date": day,
            "source": "ual",
            "overtimeActiveSeconds": 60,
            "hourlyActivity": [{"hour": 0, "overtimeActiveSeconds": 60}],
        }
    )
    calls = {"day_sessions_find_one": 0, "status_events_find": 0, "break_events_find": 0, "daily_author_activity_find": 0}
    original_day_session_find_one = repo.db.day_sessions.find_one
    original_status_events_find = repo.db.status_events.find
    original_break_events_find = repo.db.break_events.find
    original_daily_find = repo.db.daily_author_activity.find

    def counting_day_session_find_one(*args, **kwargs):
        calls["day_sessions_find_one"] += 1
        return original_day_session_find_one(*args, **kwargs)

    def counting_status_events_find(*args, **kwargs):
        calls["status_events_find"] += 1
        return original_status_events_find(*args, **kwargs)

    def counting_break_events_find(*args, **kwargs):
        calls["break_events_find"] += 1
        return original_break_events_find(*args, **kwargs)

    def counting_daily_find(*args, **kwargs):
        calls["daily_author_activity_find"] += 1
        return original_daily_find(*args, **kwargs)

    repo.db.day_sessions.find_one = counting_day_session_find_one
    repo.db.status_events.find = counting_status_events_find
    repo.db.break_events.find = counting_break_events_find
    repo.db.daily_author_activity.find = counting_daily_find
    event = {
        "author": author,
        "date": day,
        "source": "ual",
        "timeZoneId": "UTC",
        "occurredAtUtc": event_at,
        "receivedAt": event_at,
    }

    repo._begin_raw_event_batch_accounting([event, event])
    try:
        for _ in range(2):
            assert repo._is_waiting_for_first_workday_activity(event, None, event_at) is True
            repo._status_interval_context_for_event(event, event_at, event_at)
            assert repo._has_reports_stopped_gap_overlap(event, event_at, event_at + dt.timedelta(minutes=5)) is True
            repo._suppress_night_overtime_for_midnight_offline_carryover(
                {**event, "occurredAtUtc": dt.datetime(2026, 6, 16, 0, 5, tzinfo=dt.UTC)}
            )
            repo._is_waiting_for_telegram_online_after_night_overtime(event, author, day, event_at)
    finally:
        repo._finish_raw_event_batch_accounting()

    assert calls == {
        "day_sessions_find_one": 1,
        "status_events_find": 1,
        "break_events_find": 1,
        "daily_author_activity_find": 1,
    }


def test_raw_event_batch_accounting_caches_vacation_marks():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    event_at = dt.datetime(2026, 6, 16, 10, 0, tzinfo=dt.UTC)
    event = {
        "author": author,
        "date": day,
        "source": "ual",
        "timeZoneId": "UTC",
        "occurredAtUtc": event_at,
        "receivedAt": event_at,
    }
    repo.db.calendar_marks.insert_one({"rawAuthor": author, "date": day, "reasonId": "vacation", "note": ""})
    calls = {"calendar_marks_find_one": 0}
    original_find_one = repo.db.calendar_marks.find_one

    def counting_find_one(*args, **kwargs):
        calls["calendar_marks_find_one"] += 1
        return original_find_one(*args, **kwargs)

    repo.db.calendar_marks.find_one = counting_find_one

    repo._begin_raw_event_batch_accounting([event, event])
    try:
        for _ in range(2):
            assert repo.is_vacation_day(author, day) is True
            assert repo.vacation_overtime_window_for_event(event) == (
                dt.datetime(2026, 6, 16, 0, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 6, 17, 0, 0, tzinfo=dt.UTC),
            )
    finally:
        repo._finish_raw_event_batch_accounting()

    assert calls == {"calendar_marks_find_one": 1}


def test_vacation_mark_lookup_without_batch_context_still_hits_database():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    repo.db.calendar_marks.insert_one({"rawAuthor": author, "date": day, "reasonId": "vacation", "note": ""})
    calls = {"calendar_marks_find_one": 0}
    original_find_one = repo.db.calendar_marks.find_one

    def counting_find_one(*args, **kwargs):
        calls["calendar_marks_find_one"] += 1
        return original_find_one(*args, **kwargs)

    repo.db.calendar_marks.find_one = counting_find_one

    assert repo.is_vacation_day(author, day) is True
    assert repo.is_vacation_day(author, day) is True

    assert calls == {"calendar_marks_find_one": 2}


def test_raw_event_batch_accounting_caches_overtime_rule_context_lookups():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    event_at = dt.datetime(2026, 6, 16, 20, 0, tzinfo=dt.UTC)
    event = {
        "author": author,
        "date": day,
        "source": "ual",
        "timeZoneId": "UTC",
        "occurredAtUtc": event_at,
        "receivedAt": event_at,
    }
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "lastOfflineAt": dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.break_events.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC),
        }
    )
    calls = {"day_sessions_find_one": 0, "break_events_find": 0}
    original_day_session_find_one = repo.db.day_sessions.find_one
    original_break_events_find = repo.db.break_events.find

    def counting_day_session_find_one(*args, **kwargs):
        calls["day_sessions_find_one"] += 1
        return original_day_session_find_one(*args, **kwargs)

    def counting_break_events_find(*args, **kwargs):
        calls["break_events_find"] += 1
        return original_break_events_find(*args, **kwargs)

    repo.db.day_sessions.find_one = counting_day_session_find_one
    repo.db.break_events.find = counting_break_events_find

    repo._begin_raw_event_batch_accounting([event, event])
    try:
        for _ in range(2):
            assert repo._day_session_for_overtime_rules(author, day)["lastOfflineAt"] == dt.datetime(
                2026, 6, 16, 18, 0, tzinfo=dt.UTC
            )
            assert repo._is_author_offline_after_latest_telegram_state(author, day, event_at) is True
    finally:
        repo._finish_raw_event_batch_accounting()

    assert calls == {"day_sessions_find_one": 1, "break_events_find": 1}


def test_overtime_rule_context_without_batch_context_still_hits_database():
    repo = fake_repository()
    author = "Future Artist"
    day = "2026-06-16"
    event_at = dt.datetime(2026, 6, 16, 20, 0, tzinfo=dt.UTC)
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "lastOfflineAt": dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.break_events.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 6, 16, 18, 0, tzinfo=dt.UTC),
        }
    )
    calls = {"day_sessions_find_one": 0, "break_events_find": 0}
    original_day_session_find_one = repo.db.day_sessions.find_one
    original_break_events_find = repo.db.break_events.find

    def counting_day_session_find_one(*args, **kwargs):
        calls["day_sessions_find_one"] += 1
        return original_day_session_find_one(*args, **kwargs)

    def counting_break_events_find(*args, **kwargs):
        calls["break_events_find"] += 1
        return original_break_events_find(*args, **kwargs)

    repo.db.day_sessions.find_one = counting_day_session_find_one
    repo.db.break_events.find = counting_break_events_find

    for _ in range(2):
        assert repo._day_session_for_overtime_rules(author, day)["lastOfflineAt"] == dt.datetime(
            2026, 6, 16, 18, 0, tzinfo=dt.UTC
        )
        assert repo._is_author_offline_after_latest_telegram_state(author, day, event_at) is True

    assert calls == {"day_sessions_find_one": 2, "break_events_find": 2}


def test_raw_event_batch_accounting_caches_repeated_time_zone_updates():
    repo = fake_repository()
    calls = []
    repo.update_author_time_zone = lambda raw_author, time_zone_id, time_zone_display_name=None: calls.append(
        (raw_author, time_zone_id, time_zone_display_name)
    )

    repo._begin_raw_event_batch_accounting([])
    try:
        repo._update_author_time_zone_for_raw_event_accounting("Future Artist", "UTC", "UTC")
        repo._update_author_time_zone_for_raw_event_accounting("Future Artist", "UTC", "UTC")
        repo._update_author_time_zone_for_raw_event_accounting("Future Artist", "Europe/Madrid", "CET")
    finally:
        repo._finish_raw_event_batch_accounting()

    assert calls == [("Future Artist", "UTC", "UTC"), ("Future Artist", "Europe/Madrid", "CET")]
