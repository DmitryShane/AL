import datetime as dt

import pytest

import al_backend.services.activity_aggregation_rebuild as rebuild_module
import al_backend.services.activity_raw_event_accounting as accounting_module
from tests.fakes import fake_repository, set_idle_threshold


AUTHOR = "Online Boundary Worker"
DAY = "2026-10-15"
ONLINE = dt.datetime.fromisoformat("2026-10-15T14:44:21+03:00").astimezone(dt.UTC)


def event(index, time, source="bal", kind="select", metadata=None):
    at = dt.datetime.fromisoformat(f"{DAY}T{time}+03:00")
    return {
        "eventId": f"boundary-event-{index}", "batchId": f"boundary-batch-{index}",
        "author": AUTHOR, "date": DAY, "source": source, "projectId": "boundary-project",
        "sessionId": "boundary-session", "deviceId": "boundary-device", "timeZoneId": "Europe/Kyiv",
        "eventType": kind, "occurredAtUtc": at.astimezone(dt.UTC), "occurredAtLocal": at.isoformat(),
        "receivedAt": at.astimezone(dt.UTC) + dt.timedelta(seconds=1), "metadata": metadata or {},
    }


def repository():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo.db.author_profiles.insert_one({
        "rawAuthor": AUTHOR, "telegramUsername": "boundary_worker", "timeZoneId": "Europe/Kyiv",
    })
    return repo


def online(repo):
    repo.db.day_sessions.insert_one({
        "rawAuthor": AUTHOR, "date": DAY, "startedAt": ONLINE, "lastOnlineAt": ONLINE,
        "timeZoneId": "Europe/Kyiv",
    })


@pytest.mark.parametrize("source,kind", [("bal", "select"), ("ual", "selection"), ("codex", "external")])
@pytest.mark.parametrize("mode", ["live", "known_online", "rebuild", "rebuild_accumulator"])
def test_full_day_only_counts_time_after_online_and_night_overtime(source, kind, mode, monkeypatch):
    repo = repository()
    events = [event(i, time, source, kind) for i, time in enumerate([
        "00:09:00", "00:11:00", "13:11:32", "13:12:32", "14:44:20", "14:44:22", "14:45:22",
    ])]
    events.insert(4, event(8, "13:22:32", source, "heartbeat"))
    if mode != "live":
        online(repo)
    if mode in {"live", "known_online"}:
        for item in events:
            if mode == "live" and item["occurredAtUtc"] >= ONLINE and not repo.db.day_sessions.items:
                online(repo)
            deltas = repo._apply_raw_event_to_aggregates(item)
            if dt.time(7) <= dt.datetime.fromisoformat(item["occurredAtLocal"]).time() and item["occurredAtUtc"] < ONLINE:
                assert deltas["activeDeltaSeconds"] == 0
                assert deltas["idleDeltaSeconds"] == 0
    else:
        enabled = mode == "rebuild_accumulator"
        monkeypatch.setattr(accounting_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        monkeypatch.setattr(rebuild_module, "REBUILD_INTERVAL_ACCUMULATOR_ENABLED", enabled)
        for item in events:
            repo.db.raw_activity_events.insert_one(item)
            repo.db.raw_event_batches.insert_one({k: item[k] for k in ("batchId", "author", "source", "projectId", "receivedAt")})
        repo._materialize_status_report_rows = lambda: None
        repo._rebuild_aggregates_from_sources({DAY}, {AUTHOR})
    daily = repo.db.daily_author_activity.find_one({"author": AUTHOR, "date": DAY, "source": source})
    assert daily["overtimeActiveSeconds"] == 120
    assert daily["idleSeconds"] == 0
    assert 60 <= daily["activeSeconds"] <= 61
    for hour in daily["hourlyActivity"]:
        if hour["hour"] < 14:
            assert hour["activeSeconds"] == hour["idleSeconds"] == 0
        for segment in hour["fillSegments"]:
            if segment["kind"] in {"active", "idle"}:
                assert hour["hour"] * 3600 + segment["startSecond"] >= 14 * 3600 + 44 * 60 + 21


@pytest.mark.parametrize("source,kind,metadata", [
    ("dev", "hold", {"holdDurationSeconds": 600, "firstHoldAtUtc": "2026-10-15T11:35:00Z"}),
    ("ual", "scene_view_navigation", {"navigationDurationSeconds": 600, "firstNavigationAtUtc": "2026-10-15T11:35:00Z"}),
])
@pytest.mark.parametrize("time,expected_active", [("13:45:00", 0), ("14:45:00", 39)])
def test_duration_events_clip_to_online(source, kind, metadata, time, expected_active):
    repo = repository()
    online(repo)
    item = event(0, time, source, kind, metadata)
    item["receivedAt"] = ONLINE + dt.timedelta(hours=1)
    deltas = repo._apply_raw_event_to_aggregates(item)
    assert deltas["activeDeltaSeconds"] == expected_active
    assert deltas["idleDeltaSeconds"] == 0


@pytest.mark.parametrize("known_online", [False, True])
def test_linked_author_without_night_activity_waits_for_online(known_online):
    repo = repository()
    if known_online:
        online(repo)
    for i, time in enumerate(["13:00:00", "13:01:00", "13:02:00", "13:20:00"]):
        deltas = repo._apply_raw_event_to_aggregates(event(i, time))
        assert deltas["activeDeltaSeconds"] == 0
        assert deltas["idleDeltaSeconds"] == 0
