import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import al_backend.services.activity_aggregation_rebuild as rebuild_module
import al_backend.services.activity_raw_event_accounting as accounting_module
from tests.fakes import fake_repository, set_idle_threshold


def _event(index, event_type, local_at, delay_seconds=0):
    occurred_at = dt.datetime.fromisoformat(local_at)
    return {
        "eventId": f"night-event-{index}",
        "batchId": f"night-batch-{index}",
        "author": "Night Worker",
        "source": "bal",
        "projectId": "night-project",
        "sessionId": "night-session",
        "date": occurred_at.date().isoformat(),
        "timeZoneId": "Europe/Kyiv",
        "eventType": event_type,
        "occurredAtUtc": occurred_at.astimezone(dt.UTC),
        "occurredAtLocal": local_at,
        "receivedAt": occurred_at.astimezone(dt.UTC) + dt.timedelta(seconds=delay_seconds),
    }


@pytest.mark.parametrize("mode", ["live", "rebuild", "rebuild_accumulator"])
@pytest.mark.parametrize("delay_seconds", [0, 603, 8 * 3600])
def test_delayed_night_heartbeats_preserve_overtime_exclusion(mode, delay_seconds, monkeypatch):
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    events = [
        _event(0, "select", "2026-10-15T00:09:00+03:00"),
        _event(1, "select", "2026-10-15T00:11:00+03:00"),
    ]
    first_heartbeat = dt.datetime(2026, 10, 15, 0, 23, 28, tzinfo=ZoneInfo("Europe/Kyiv"))
    for index in range(25):
        local_at = first_heartbeat + dt.timedelta(seconds=602 * index)
        events.append(_event(index + 2, "heartbeat", local_at.isoformat(), delay_seconds))
    # A morning delivery must not backfill the remaining overnight interval as idle.
    events.append(_event(27, "heartbeat", "2026-10-15T09:33:49+03:00", 10))

    if mode == "live":
        for event in events:
            repo._apply_raw_event_to_aggregates(event)
    else:
        enabled = mode == "rebuild_accumulator"
        monkeypatch.setattr(accounting_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        monkeypatch.setattr(rebuild_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        for event in events:
            repo.db.raw_activity_events.insert_one(event)
            repo.db.raw_event_batches.insert_one(
                {key: event[key] for key in ("batchId", "author", "source", "projectId", "receivedAt")}
            )
        repo._materialize_status_report_rows = lambda: None
        repo._rebuild_aggregates_from_sources({"2026-10-15"}, {"Night Worker"})

    daily = repo.db.daily_author_activity.find_one({"author": "Night Worker", "date": "2026-10-15"})
    assert daily["idleSeconds"] == 0
    assert daily["overtimeActiveSeconds"] == 120
    assert daily["activeSeconds"] == 0
    for hour in daily["hourlyActivity"]:
        if hour["hour"] < 7:
            assert hour["idleSeconds"] == 0
            assert all(segment["kind"] != "idle" for segment in hour["fillSegments"])
    assert all(row["idleDeltaSeconds"] == 0 for row in repo.db.report_rows.find({"author": "Night Worker"}))


@pytest.mark.parametrize("hour, expected_idle", [(6, 0), (7, 600), (14, 600)])
def test_delayed_heartbeat_idle_respects_local_seven_am_boundary(hour, expected_idle):
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo._apply_raw_event_to_aggregates(_event(0, "select", f"2026-10-15T{hour:02}:09:00+03:00"))
    repo._apply_raw_event_to_aggregates(_event(1, "select", f"2026-10-15T{hour:02}:11:00+03:00"))

    deltas = repo._apply_raw_event_to_aggregates(
        _event(2, "heartbeat", f"2026-10-15T{hour:02}:23:28+03:00", 603)
    )

    assert deltas["idleDeltaSeconds"] == expected_idle
    assert deltas["overtimeActiveDeltaSeconds"] == 0
