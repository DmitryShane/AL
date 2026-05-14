import datetime as dt

from al_backend.hourly_fill_rules import empty_hourly_activity
from tests.fakes import fake_repository


def test_publisher_profile_can_be_empty_and_link_device_profile():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one({"rawAuthor": "Device8", "source": "dev-android"})

    result = repo.upsert_publisher_profile("Publisher QA", "Publisher QA", "External", "#22c55e")
    link = repo.link_publisher_device("Publisher QA", "Device8")

    assert result["ok"] is True
    assert link["ok"] is True
    profile = repo.db.author_profiles.find_one({"rawAuthor": "Publisher QA"})
    assert profile["profileType"] == "publisher"
    assert profile.get("authorEmail") is None
    assert profile.get("telegramUsername") is None
    assert repo.resolve_author_alias("Device8") == "Publisher QA"


def test_publisher_profiles_are_hidden_from_calendar_summary():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Real Author",
            "displayName": "Real Author",
            "team": "Art",
            "authorColor": "#22c55e",
            "profileType": "person",
        }
    )
    repo.upsert_publisher_profile("Publisher QA", "Publisher QA", "External", "#ef4444")
    repo.db.calendar_marks.insert_one({"rawAuthor": "Real Author", "date": "2026-05-14", "reasonId": "vacation", "note": ""})
    repo.db.calendar_marks.insert_one({"rawAuthor": "Publisher QA", "date": "2026-05-14", "reasonId": "vacation", "note": ""})

    summary = repo.calendar_summary(2026)

    assert [item["rawAuthor"] for item in summary["authors"]] == ["Real Author"]
    assert [item["rawAuthor"] for item in summary["marks"]] == ["Real Author"]
    assert [item["rawAuthor"] for item in summary["stats"]] == ["Real Author"]


def test_publisher_device_stale_is_grey_device_offline_without_reports_stopped_event():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 14, 1, 0, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Publisher QA",
            "displayName": "Publisher QA",
            "profileType": "publisher",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev-android",
            "author": "Publisher QA",
            "projectId": "Bike Rush 2",
            "date": "2026-05-14",
            "timeZoneId": "Local",
            "lastReceivedAt": dt.datetime(2026, 5, 13, 22, 5, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-14T00:05:00+02:00",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=now)
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Publisher QA")

    assert author["status"] == "stale"
    assert author["stalePresence"] == "device"
    assert repo.db.status_events.items == []


def test_publisher_device_alias_hides_raw_device_author_in_activity_summary():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one({"rawAuthor": "Device8", "source": "dev-android"})
    repo.upsert_publisher_profile("Publisher QA", "Publisher QA", "", "")
    repo.link_publisher_device("Publisher QA", "Device8")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev-android",
            "author": "Device8",
            "projectId": "Bike Rush 2",
            "date": "2026-05-14",
            "timeZoneId": "Local",
            "lastReceivedAt": dt.datetime(2026, 5, 14, 10, 0, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-14T12:00:00+02:00",
            "activeSeconds": 120,
            "idleSeconds": 30,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=dt.datetime(2026, 5, 14, 10, 1, tzinfo=dt.UTC))
    authors = {item["rawAuthor"]: item for item in summary["authors"]}

    assert "Device8" not in authors
    assert authors["Publisher QA"]["activeSeconds"] == 120


def test_publisher_local_timezone_offset_extends_live_scope_past_utc_day():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 13, 22, 30, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Publisher QA",
            "displayName": "Publisher QA",
            "profileType": "publisher",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev-android",
            "author": "Publisher QA",
            "projectId": "Bike Rush 2",
            "date": "2026-05-14",
            "timeZoneId": "Local",
            "lastReceivedAt": dt.datetime(2026, 5, 13, 22, 10, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-14T00:10:00+02:00",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=now)

    assert [item["rawAuthor"] for item in summary["authors"]] == ["Publisher QA"]
