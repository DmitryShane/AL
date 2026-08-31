from __future__ import annotations

import datetime as dt

import pytest

from al_backend.routers.reports import reports_activity_hourly, reports_activity_hourly_status
from tests.fakes import fake_repository


NOW = dt.datetime(2026, 8, 31, 14, 30, tzinfo=dt.UTC)


def _repo_with_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "timeZoneId": "Europe/Madrid",
        }
    )
    return repo


@pytest.mark.parametrize("report_status", ["queued", "processing"])
def test_activity_hourly_freshness_reports_active_queue_as_updating(report_status: str):
    repo = _repo_with_author()
    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Shane",
            "status": report_status,
            "queuedAt": NOW - dt.timedelta(seconds=5),
            "receivedAt": NOW - dt.timedelta(seconds=5),
        }
    )

    freshness = repo.activity_hourly_freshness("Dmitry Shane", now=NOW)

    assert freshness["status"] == "updating"
    assert freshness["pendingReportCount"] == 1


def test_activity_hourly_freshness_reports_processed_current_day_version_and_time():
    repo = _repo_with_author()
    processed_at = NOW - dt.timedelta(seconds=2)
    recorded_at = "2026-08-31T16:29:45+02:00"
    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Shane",
            "status": "processed",
            "affectedDates": ["2026-08-31"],
            "processedAt": processed_at,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Dmitry Shane",
            "date": "2026-08-31",
            "lastRecordedAt": recorded_at,
        }
    )

    freshness = repo.activity_hourly_freshness("Dmitry Shane", now=NOW)

    assert freshness == {
        "status": "current",
        "checkedAt": NOW.isoformat(),
        "dataVersion": processed_at.isoformat(),
        "dataThrough": "2026-08-31T14:29:45+00:00",
        "pendingReportCount": 0,
    }


@pytest.mark.parametrize("collection_name", ["report_refresh_requests", "manual_report_expectations"])
def test_activity_hourly_freshness_tracks_manual_refresh_and_marks_overdue_wait_delayed(collection_name: str):
    repo = _repo_with_author()
    collection = getattr(repo.db, collection_name)
    collection.insert_one(
        {
            "author": "Dmitry Shane",
            "requestedAt": NOW - dt.timedelta(seconds=30),
        }
    )

    assert repo.activity_hourly_freshness("Dmitry Shane", now=NOW)["status"] == "updating"

    collection.items[0]["requestedAt"] = NOW - dt.timedelta(minutes=3)

    assert repo.activity_hourly_freshness("Dmitry Shane", now=NOW)["status"] == "delayed"


def test_activity_hourly_freshness_ignores_previous_day_failure():
    repo = _repo_with_author()
    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Shane",
            "status": "failed",
            "receivedAt": dt.datetime(2026, 8, 30, 20, tzinfo=dt.UTC),
            "failedAt": dt.datetime(2026, 8, 30, 20, 1, tzinfo=dt.UTC),
        }
    )

    assert repo.activity_hourly_freshness("Dmitry Shane", now=NOW)["status"] == "current"

    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Shane",
            "status": "failed",
            "receivedAt": NOW - dt.timedelta(minutes=1),
            "failedAt": NOW - dt.timedelta(seconds=30),
        }
    )

    assert repo.activity_hourly_freshness("Dmitry Shane", now=NOW)["status"] == "delayed"

    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Shane",
            "status": "processed",
            "affectedDates": ["2026-08-31"],
            "processedAt": NOW,
        }
    )

    assert repo.activity_hourly_freshness("Dmitry Shane", now=NOW)["status"] == "current"


def test_activity_hourly_freshness_resolves_alias_report_versions():
    repo = _repo_with_author()
    repo.db.author_aliases.insert_one(
        {
            "sourceRawAuthor": "Dmitry Old",
            "targetRawAuthor": "Dmitry Shane",
        }
    )
    repo.db.raw_reports.insert_one(
        {
            "authorKey": "Dmitry Old",
            "status": "processed",
            "affectedDates": ["2026-08-31"],
            "processedAt": NOW,
        }
    )

    freshness = repo.activity_hourly_freshness("Dmitry Shane", now=NOW)

    assert freshness["dataVersion"] == NOW.isoformat()


def test_activity_hourly_routes_expose_freshness_contract():
    repo = _repo_with_author()
    repo.cached_activity_summary = lambda **_kwargs: {
        "hourlyActivityByAuthor": [],
        "cache": {"hit": True},
        "snapshot": {},
    }

    status = reports_activity_hourly_status(author="Dmitry Shane", service=repo)
    payload = reports_activity_hourly(
        start_date="2026-08-31",
        end_date="2026-08-31",
        date_mode="authorLocalToday",
        author="Dmitry Shane",
        service=repo,
    )

    assert set(status) == {"status", "checkedAt", "dataVersion", "dataThrough", "pendingReportCount"}
    assert payload["freshness"]["status"] == "current"
    assert payload["hourlyActivityByAuthor"] == []
