import datetime as dt
import pytest
from fastapi import BackgroundTasks, HTTPException
from al_backend.hourly_fill_rules import empty_hourly_activity
from al_backend.routers.publisher_profiles import _assign_publisher_author, _run_publisher_author_rebuild
from tests.fakes import fake_repository

DAY = "2026-05-14"
NOW = dt.datetime(2026, 5, 14, 12, 1, tzinfo=dt.UTC)


def setup_repo():
    repo = fake_repository()
    repo.upsert_publisher_profile("Publisher", "Publisher", "External", "")
    repo.db.author_profiles.insert_one({"rawAuthor": "Alice", "displayName": "Alice A", "team": "QA", "authorEmail": "alice@example.com", "githubUsername": "alice"})
    return repo


def add_daily(repo, author, seconds, source="cur"):
    repo.db.daily_author_activity.insert_one({"author": author, "source": source, "projectId": "AL", "date": DAY,
        "timeZoneId": "UTC", "lastReceivedAt": NOW, "lastRecordedAt": NOW.isoformat(),
        "activeSeconds": seconds, "idleSeconds": 0, "hourlyActivity": empty_hourly_activity()})


def summary(repo):
    return {a["rawAuthor"]: a for a in repo.activity_summary(start_date=DAY, end_date=DAY, now=NOW)["authors"]}


def test_reversible_link_preserves_settings_and_groups_history_and_devices():
    repo = setup_repo()
    before = repo.db.author_profiles.find_one({"rawAuthor": "Alice"}).copy()
    repo.db.device_report_identities.insert_one({"rawAuthor": "Device8", "source": "dev-android"})
    repo.upsert_device_profile_alias("Device8", "Alice")
    add_daily(repo, "Alice", 120)
    add_daily(repo, "Device8", 60, "dev-android")
    assert repo.set_publisher_author("Publisher", "Alice", True)["ok"]
    assert "Alice" not in {p["rawAuthor"] for p in repo.author_profiles()}
    assert repo.publisher_profiles()[0]["authors"] == [{"rawAuthor": "Alice", "displayName": "Alice A"}]
    assert repo.activity_author_keys("Publisher") == ["Alice", "Device8", "Publisher"]
    assert repo.resolve_author_alias("Device8") == "Alice"
    rows = summary(repo)
    assert "Alice" not in rows
    assert rows["Publisher"]["activeSeconds"] == 180
    add_daily(repo, "Alice", 30, "vsc")
    assert summary(repo)["Publisher"]["activeSeconds"] == 210
    assert repo.set_publisher_author("Publisher", "Alice", False)["ok"]
    assert summary(repo)["Alice"]["activeSeconds"] == 210
    restored = repo.db.author_profiles.find_one({"rawAuthor": "Alice"})
    for key, value in before.items():
        assert restored[key] == value
    assert "publisherRawAuthor" not in restored


def test_multiple_members_and_invalid_assignments():
    repo = setup_repo()
    repo.upsert_publisher_profile("Other", "Other", "", "")
    repo.db.author_profiles.insert_one({"rawAuthor": "Bob"})
    assert repo.set_publisher_author("Publisher", "Alice", True)["ok"]
    assert repo.set_publisher_author("Publisher", "Alice", True)["ok"]
    assert repo.set_publisher_author("Publisher", "Bob", True)["ok"]
    assert not repo.set_publisher_author("Other", "Alice", True)["ok"]
    assert not repo.set_publisher_author("Other", "Alice", False)["ok"]
    assert not repo.set_publisher_author("Publisher", "Other", True)["ok"]
    assert not repo.set_publisher_author("Publisher", "Missing", True)["ok"]
    repo.db.device_report_identities.insert_one({"rawAuthor": "Device8"})
    assert not repo.set_publisher_author("Publisher", "Device8", True)["ok"]
    add_daily(repo, "Alice", 120)
    add_daily(repo, "Bob", 60)
    assert summary(repo)["Publisher"]["activeSeconds"] == 180


def test_calendar_and_snapshot_membership_changes():
    repo = setup_repo()
    add_daily(repo, "Alice", 120)
    repo.set_publisher_author("Publisher", "Alice", True)
    assert repo.calendar_summary(2026)["authors"] == []
    later = NOW + dt.timedelta(days=2)
    repo.rebuild_activity_day_summary_snapshots_for_dates([DAY], ["Alice", "Publisher"], now=later)
    assert [d["rawAuthor"] for d in repo.db.activity_author_day_summary_snapshots.items] == ["Publisher"]
    repo.set_publisher_author("Publisher", "Alice", False)
    repo.rebuild_activity_day_summary_snapshots_for_dates([DAY], ["Alice", "Publisher"], now=later)
    assert [d["rawAuthor"] for d in repo.db.activity_author_day_summary_snapshots.items] == ["Alice"]


def test_ingestion_retains_source_identity_and_metadata():
    repo = setup_repo()
    repo.set_publisher_author("Publisher", "Alice", True)
    payload = {"author": "Alice", "authorEmail": "alice@example.com", "projectId": "AL", "sessionId": "session-a", "timeZoneId": "UTC", "events": [
        {"eventId": "a1", "eventType": "focus", "date": DAY, "occurredAtUtc": "2026-05-14T12:00:00Z", "occurredAtLocal": "2026-05-14T12:00:00+00:00"},
        {"eventId": "a2", "eventType": "file_saved", "date": DAY, "occurredAtUtc": "2026-05-14T12:00:30Z", "occurredAtLocal": "2026-05-14T12:00:30+00:00"},
    ]}
    raw_id = repo.queue_decoded_report("cur", "1", "", payload, "challenge")
    assert repo.process_queued_report(raw_id)
    assert {e["author"] for e in repo.db.raw_activity_events.items} == {"Alice"}
    assert {e["author"] for e in repo.db.raw_event_batches.items} == {"Alice"}
    assert not repo.db.author_profiles.find_one({"rawAuthor": "Publisher"}).get("authorEmail")
    repo.set_publisher_author("Publisher", "Alice", False)
    assert "Alice" in summary(repo)


def test_job_scopes_rebuild_and_retains_failed_status(monkeypatch):
    repo = setup_repo()
    add_daily(repo, "Alice", 120)
    add_daily(repo, "Unrelated", 999)
    tasks = BackgroundTasks()
    result = _assign_publisher_author(repo, tasks, "Publisher", "Alice", True)
    with pytest.raises(HTTPException) as exc:
        _assign_publisher_author(repo, tasks, "Publisher", "Alice", False)
    assert exc.value.status_code == 409
    calls = []
    def rebuild(start_date, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("test failure")
    monkeypatch.setattr(repo, "rebuild_aggregates_for_dates", rebuild)
    _run_publisher_author_rebuild(repo, result["jobId"])
    assert calls[0]["authors"] == ["Alice", "Publisher"]
    assert calls[0]["dates"] == [DAY]
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": result["jobId"]})["status"] == "failed"


def test_full_job_round_trip_keeps_new_reports_and_unrelated_history():
    repo = setup_repo()
    def ingest(prefix, second):
        payload = {"author": "Alice", "authorEmail": "alice@example.com", "projectId": "AL", "sessionId": "alice-session", "timeZoneId": "UTC", "events": [
            {"eventId": prefix, "eventType": "focus" if second == 0 else "file_saved", "date": DAY,
             "occurredAtUtc": f"2026-05-14T12:00:{second:02d}Z", "occurredAtLocal": f"2026-05-14T12:00:{second:02d}+00:00"}]}
        report_id = repo.queue_decoded_report("cur", "1", "", payload, prefix)
        assert repo.process_queued_report(report_id)
    ingest("before", 0)
    add_daily(repo, "Unrelated", 999)
    unrelated = repo.db.daily_author_activity.find_one({"author": "Unrelated"}).copy()
    result = _assign_publisher_author(repo, BackgroundTasks(), "Publisher", "Alice", True)
    _run_publisher_author_rebuild(repo, result["jobId"])
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": result["jobId"]})["status"] == "completed"
    ingest("during", 30)
    publisher_seconds = summary(repo)["Publisher"]["activeSeconds"]
    assert publisher_seconds > 0
    assert repo.reports_page(start_date=DAY, end_date=DAY, author="Publisher")["total"] > 0
    result = _assign_publisher_author(repo, BackgroundTasks(), "Publisher", "Alice", False)
    _run_publisher_author_rebuild(repo, result["jobId"])
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": result["jobId"]})["status"] == "completed"
    assert summary(repo)["Alice"]["activeSeconds"] == publisher_seconds
    assert {e["eventId"] for e in repo.db.raw_activity_events.items} == {"before", "during"}
    assert repo.db.daily_author_activity.find_one({"author": "Unrelated"}) == unrelated


def test_publisher_author_api_requires_settings_permission():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from al_backend.api_security import current_site_user
    from al_backend.dependencies import get_author_service
    from al_backend.routers.publisher_profiles import router
    repo = setup_repo()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_author_service] = lambda: repo
    client = TestClient(app)
    path = "/api/v1/publisher-profiles/Publisher/authors/Alice"
    assert client.put(path).status_code == 401
    app.dependency_overrides[current_site_user] = lambda: {"role": "viewer"}
    assert client.put(path).status_code == 403
    assert client.delete(path).status_code == 403
    app.dependency_overrides[current_site_user] = lambda: {"role": "editor"}
    assert client.put(path).status_code == 200
    assert repo.db.author_profiles.find_one({"rawAuthor": "Alice"})["publisherRawAuthor"] == "Publisher"
    assert client.delete(path).status_code == 200


def test_member_does_not_require_telegram_online_and_restores_setting():
    repo = setup_repo()
    repo.db.author_profiles.update_one({"rawAuthor": "Alice"}, {"$set": {"telegramUsername": "alice"}})
    assert repo._requires_telegram_online("Alice")
    repo.set_publisher_author("Publisher", "Alice", True)
    assert not repo._requires_telegram_online("Alice")
    assert not repo._author_uses_telegram_workday("Alice")
    repo.set_publisher_author("Publisher", "Alice", False)
    assert repo._requires_telegram_online("Alice")


def test_failed_job_can_retry_after_rebuild_error(monkeypatch):
    from al_backend.routers.publisher_profiles import retry_publisher_author_job
    repo = setup_repo()
    add_daily(repo, "Alice", 120)
    original = repo.rebuild_aggregates_for_dates
    def fail(*args, **kwargs):
        raise RuntimeError("temporary failure")
    monkeypatch.setattr(repo, "rebuild_aggregates_for_dates", fail)
    result = _assign_publisher_author(repo, BackgroundTasks(), "Publisher", "Alice", True)
    _run_publisher_author_rebuild(repo, result["jobId"])
    monkeypatch.setattr(repo, "rebuild_aggregates_for_dates", original)
    retry = retry_publisher_author_job(result["jobId"], BackgroundTasks(), {}, repo)
    _run_publisher_author_rebuild(repo, retry["jobId"])
    assert repo.db.aggregate_rebuild_jobs.find_one({"jobId": retry["jobId"]})["status"] == "completed"


def test_accounting_gate_accepts_intake_and_defers_processing_during_rebuild():
    import threading
    from al_backend.services.activity_mutation_lock import activity_mutation_lock
    repo = setup_repo()
    entered = threading.Event()
    finished = threading.Event()
    payload = {"author": "Alice", "projectId": "AL", "sessionId": "during-job", "timeZoneId": "UTC", "events": []}
    with activity_mutation_lock:
        report_id = repo.queue_decoded_report("cur", "1", "", payload, "challenge")
        assert repo.db.raw_reports.find_one({"_id": report_id})["status"] == "queued"
        def process():
            entered.set()
            repo.process_queued_report(report_id)
            finished.set()
        thread = threading.Thread(target=process)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(0.05)
    thread.join(2)
    assert finished.is_set()
    assert repo.db.raw_reports.find_one({"_id": report_id})["status"] == "processed"


def test_link_includes_existing_redirects_and_freshness_queue():
    repo = setup_repo()
    repo.upsert_author_alias("Old Alice", "Alice")
    repo.set_publisher_author("Publisher", "Alice", True)
    assert repo.activity_author_keys("Publisher") == ["Alice", "Old Alice", "Publisher"]
    assert repo._activity_hourly_freshness_author_keys("Publisher") == ["Alice", "Old Alice", "Publisher"]
    assert repo.resolve_activity_author("Old Alice") == "Publisher"
    repo.set_publisher_author("Publisher", "Alice", False)
    assert repo.resolve_author_alias("Old Alice") == "Alice"


def test_member_keeps_workday_settings_without_workday_gate_or_prompts():
    repo = setup_repo()
    repo.db.author_profiles.update_one({"rawAuthor": "Alice"}, {"$set": {"telegramUsername": "alice"}})
    repo.db.day_sessions.insert_one({"rawAuthor": "Alice", "date": DAY, "startedAt": NOW, "timeZoneId": "UTC"})
    repo.set_publisher_author("Publisher", "Alice", True)
    assert repo._batch_day_session_doc("Alice", DAY) == {}
    repo._schedule_telegram_online_prompt_if_needed("Alice", "2026-05-15", "cur", NOW)
    assert repo.db.telegram_online_prompts.items == []
    assert repo.claim_due_telegram_day_reminders(now=NOW + dt.timedelta(hours=12)) == []
    repo.set_publisher_author("Publisher", "Alice", False)
    assert repo._batch_day_session_doc("Alice", DAY)["startedAt"] == NOW
