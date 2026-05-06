import datetime as dt
import json
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import quote

import al_backend.discord_bot as discord_bot_module
from al_backend.app import PUBLIC_API_PATHS
from al_backend.discord_author_mappings import apply_discord_author_mappings
from al_backend.discord_bot import MeetingAudioSink, MeetingClient, RecordingSession, UserPcmTrack, cleanup_old_retained_recordings, retain_recording_recovery_files
from al_backend.meeting_summary import DEFAULT_MEETING_SUMMARY_PROMPT, DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE, meeting_summary_sections, render_meeting_summary_prompt
from al_backend.routers.reports import plugin_config
from al_backend.activity_math import (
    _add_break_interval_to_buckets,
    _apply_breaks_to_hourly_activity,
    _date_query,
    _empty_event_deltas,
    _empty_hourly_activity,
    _interval_deltas,
    _merge_batch_deltas,
    _normalize_telegram_username,
    _plugin_day_seconds,
    _saved_prefab_delta,
    _with_activity_mix,
    _with_author_presence,
    _with_productivity,
    _worked_file_delta,
)
from al_backend.telegram_bot import (
    BotConfig,
    edit_reminder_message,
    format_prompt_time,
    format_duration_label,
    format_meeting_duration_label,
    get_updates,
    handle_callback_query,
    parse_callback_data,
    parse_event_type,
    parse_reminder_callback,
    meeting_summary_chat_id,
    format_meeting_recording_notification_message,
    format_meeting_summary_message,
    send_break_activity_prompt_message,
    send_duplicate_afk_prompt_message,
    send_online_prompt_message,
    send_plain_message,
    send_reminder_message,
    telegram_username,
)
from tests.fakes import fake_repository, set_idle_threshold


def test_author_profile_stores_plugin_ingest_resume_timestamp_when_plugin_re_enabled():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Resume Author", "displayName": "Resume Author", "pluginEnabled": False}
    )
    repo.upsert_author_profile(
        raw_author="Resume Author",
        display_name="Resume Author",
        team="",
        telegram_username=None,
        plugin_enabled=True,
    )
    profile = repo.db.author_profiles.find_one({"rawAuthor": "Resume Author"}) or {}
    assert profile.get("pluginIngestResumedAtUtc") is not None

def test_reports_page_filters_by_author_local_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "Europe/Moscow"})
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T12:30:00Z",
            "receivedAt": dt.datetime(2026, 5, 1, 12, 30, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T13:30:00Z",
            "receivedAt": dt.datetime(2026, 5, 1, 13, 30, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
        }
    )

    page = repo.reports_page(
        start_date="2026-05-01",
        end_date="2026-05-01",
        author="Future Artist",
        hour=16,
    )

    assert page["total"] == 1
    assert len(page["reports"]) == 1
    assert page["reports"][0]["recordedAt"] == "2026-05-01T13:30:00Z"

def test_reports_page_includes_alias_source_rows_for_selected_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "team": "Core",
            "timeZoneId": "Europe/Sofia",
            "timeZoneDisplayName": "EET",
        }
    )
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device2", "targetRawAuthor": "Dmitry Shane"})
    repo.db.report_rows.insert_one(
        {
            "source": "dev",
            "author": "Device2",
            "date": "2026-05-05",
            "recordedAt": "2026-05-05T01:11:23+02:00",
            "receivedAt": dt.datetime(2026, 5, 4, 23, 11, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Other Author",
            "date": "2026-05-05",
            "recordedAt": "2026-05-05T01:12:23+02:00",
            "receivedAt": dt.datetime(2026, 5, 4, 23, 12, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
        }
    )

    page = repo.reports_page(start_date="2026-05-05", end_date="2026-05-05", author="Dmitry Shane")

    assert page["total"] == 1
    assert page["sources"] == ["dev"]
    assert page["reports"][0]["author"] == "Device2"
    assert page["reports"][0]["displayName"] == "Dmitry Shane"
    assert page["reports"][0]["team"] == "Core"
    assert page["reports"][0]["timeZoneId"] == "Europe/Sofia"

def test_reports_page_enriches_report_timezone_from_author_profile():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
            "timeZoneDisplayName": "PST",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T18:30:00Z",
            "receivedAt": dt.datetime(2026, 5, 1, 18, 30, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
        }
    )

    page = repo.reports_page(start_date="2026-05-01", end_date="2026-05-01", author="Igor Mats")

    assert page["reports"][0]["timeZoneId"] == "America/Vancouver"
    assert page["reports"][0]["timeZoneDisplayName"] == "PST"

def test_reports_page_prefers_author_profile_timezone_over_legacy_report_timezone():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
            "timeZoneDisplayName": "America/Vancouver",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T15:30:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 22, 30, tzinfo=dt.UTC),
            "timeZoneId": "Canada/Pacific",
            "timeZoneDisplayName": "PST",
            "activeDeltaSeconds": 60,
        }
    )

    page = repo.reports_page(start_date="2026-05-01", end_date="2026-05-01", author="Igor Mats")

    assert page["reports"][0]["timeZoneId"] == "America/Vancouver"
    assert page["reports"][0]["timeZoneDisplayName"] == "America/Vancouver"

def test_summary_author_rows_have_no_removed_dashboard_arrays():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Healthy Author", "displayName": "Healthy Author"})
    received_at = dt.datetime.now(dt.UTC)
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Healthy Author",
            "projectId": "unity",
            "date": "2026-04-29",
            "activeSeconds": 1,
            "lastRecordedAt": received_at,
            "lastReceivedAt": received_at,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Healthy Author")

    assert "alerts" not in author
    assert "alertStats" not in author

def test_manual_profile_is_listed_before_activity_and_can_receive_email():
    repo = fake_repository()

    repo.upsert_author_profile(
        raw_author="Future Artist",
        display_name="Future Artist",
        team="Art",
        telegram_username="@future_artist",
        plugin_enabled=True,
        author_color="#13a37b",
        time_zone_id="UTC",
    )

    assert repo.list_authors() == ["Future Artist"]
    assert repo.author_profiles()[0]["telegramUsername"] == "future_artist"

    repo.update_author_email("Future Artist", "future@example.com")

    assert repo.author_profiles()[0]["authorEmail"] == "future@example.com"

def test_author_profile_github_username_sets_avatar_url():
    repo = fake_repository()

    repo.upsert_author_profile(
        raw_author="Test Dev",
        display_name="Test Dev",
        team="",
        telegram_username=None,
        plugin_enabled=True,
        author_color="#13a37b",
        github_username="OctoCat",
    )

    profiles = repo.author_profiles()
    assert len(profiles) == 1
    assert profiles[0]["githubUsername"] == "OctoCat"
    base = f"/api/v1/avatars/author?rawAuthor={quote('Test Dev', safe='')}"
    assert profiles[0]["avatarUrl"].startswith(f"{base}&v=")

def test_cyrillic_author_names_are_unicode_normalized_for_profile_matching():
    repo = fake_repository()
    decomposed_author = "Але\u0308на Иванова"
    composed_author = unicodedata.normalize("NFC", decomposed_author)

    repo.upsert_author_profile(
        raw_author=decomposed_author,
        display_name=None,
        team="Art",
        telegram_username="@alena",
        plugin_enabled=True,
        author_color="#13a37b",
        time_zone_id="UTC",
    )
    repo.update_author_email(composed_author, "alena@example.com")

    assert repo.list_authors() == [composed_author]
    assert repo.author_profiles()[0]["rawAuthor"] == composed_author
    assert repo.author_profiles()[0]["authorEmail"] == "alena@example.com"

def test_bootstrap_admin_does_not_reset_existing_password():
    repo = fake_repository()

    repo.ensure_bootstrap_site_admin("admin@example.com", "first-password")
    repo.ensure_bootstrap_site_admin("admin@example.com", "second-password")

    assert repo.authenticate_site_user("admin@example.com", "first-password")
    assert repo.authenticate_site_user("admin@example.com", "second-password") is None

def test_plugin_config_resolves_author_alias_before_profile_updates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "authorEmail": "vedamir.infinum@gmail.com",
            "pluginEnabled": True,
        }
    )
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Vedamir", "targetRawAuthor": "Denis Ostrovskiy"})

    config = plugin_config(
        source="bal",
        author="Vedamir",
        author_email="vedamir.infinum@gmail.com",
        project_id="project",
        service=repo,
    )

    assert config.author == "Denis Ostrovskiy"
    assert repo.db.author_profiles.count_documents({"rawAuthor": "Vedamir"}) == 0
    assert repo.db.author_profiles.find_one({"rawAuthor": "Denis Ostrovskiy"})["authorEmail"] == "vedamir.infinum@gmail.com"

def test_update_author_email_does_not_create_duplicate_profile_for_existing_email():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "authorEmail": "vedamir.infinum@gmail.com",
            "pluginEnabled": True,
        }
    )

    repo.update_author_email("Vedamir", "vedamir.infinum@gmail.com")

    assert repo.db.author_profiles.count_documents({"rawAuthor": "Vedamir"}) == 0
    assert repo.db.author_profiles.count_documents({"rawAuthor": "Denis Ostrovskiy"}) == 1
    assert repo.db.report_security_events.items[-1]["eventType"] == "author_email_conflict"

def test_reports_page_paginates_author_reports_and_keeps_source_options():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    for index, source in enumerate(["ual", "bal", "ual"]):
        repo.db.report_rows.insert_one(
            {
                "source": source,
                "author": "Future Artist",
                "date": "2026-04-29",
                "recordedAt": f"2026-04-29T10:0{index}:00+00:00",
                "receivedAt": dt.datetime(2026, 4, 29, 10, index, tzinfo=dt.UTC),
                "activeDeltaSeconds": 60,
                "idleDeltaSeconds": 0,
                "overtimeActiveDeltaSeconds": 0,
            }
        )

    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Other Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    first_page = repo.reports_page(
        start_date="2026-04-29",
        end_date="2026-04-29",
        author="Future Artist",
        limit=2,
        offset=0,
    )
    filtered_page = repo.reports_page(
        start_date="2026-04-29",
        end_date="2026-04-29",
        author="Future Artist",
        source="ual",
        limit=10,
        offset=0,
    )

    assert first_page["total"] == 3
    assert len(first_page["reports"]) == 2
    assert first_page["sources"] == ["bal", "ual"]
    assert filtered_page["total"] == 2
    assert [report["source"] for report in filtered_page["reports"]] == ["ual", "ual"]
    assert filtered_page["sources"] == ["bal", "ual"]

def test_delete_author_data_preserves_profile_but_delete_profile_removes_it():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist"})
    repo.db.report_rows.insert_one({"author": "Future Artist"})
    repo.db.day_sessions.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist", "date": "2026-04-28"})

    data_result = repo.delete_author_data("Future Artist")

    assert data_result["ok"] is True
    assert repo.db.author_profiles.items == [{"rawAuthor": "Future Artist", "telegramUsername": "future_artist"}]

    repo.db.report_rows.insert_one({"author": "Future Artist"})
    profile_result = repo.delete_author_profile("Future Artist")

    assert profile_result["ok"] is True
    assert repo.db.author_profiles.items == []
    assert repo.db.report_rows.items == []

def test_delete_author_data_removes_alias_source_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device2", "targetRawAuthor": "Dmitry Shane"})
    repo.db.raw_reports.insert_one({"_id": "raw-device"})
    repo.db.raw_event_batches.insert_one({"author": "Device2", "rawReportId": "raw-device", "batchId": "device-batch"})
    repo.db.raw_activity_events.insert_one({"author": "Device2", "batchId": "device-batch", "date": "2026-05-05"})
    repo.db.activity_snapshots.insert_one({"author": "Device2", "rawReportId": "raw-device"})
    repo.db.report_rows.insert_one({"author": "Device2", "date": "2026-05-05", "source": "dev"})
    repo.db.daily_author_activity.insert_one({"author": "Device2", "date": "2026-05-05", "source": "dev"})
    repo.db.day_sessions.insert_one({"rawAuthor": "Device2", "date": "2026-05-05"})
    repo.db.report_rows.insert_one({"author": "Other Author", "date": "2026-05-05", "source": "ual"})

    result = repo.delete_author_data("Dmitry Shane")

    assert result["ok"] is True
    assert result["authorKeys"] == ["Device2", "Dmitry Shane"]
    assert repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"}) is not None
    assert repo.db.raw_reports.items == []
    assert repo.db.raw_event_batches.find_one({"author": "Device2"}) is None
    assert repo.db.raw_activity_events.find_one({"author": "Device2"}) is None
    assert repo.db.activity_snapshots.find_one({"author": "Device2"}) is None
    assert repo.db.report_rows.find_one({"author": "Device2"}) is None
    assert repo.db.daily_author_activity.find_one({"author": "Device2"}) is None
    assert repo.db.day_sessions.find_one({"rawAuthor": "Device2"}) is None
    assert repo.db.report_rows.find_one({"author": "Other Author"}) is not None

def test_delete_author_data_for_date_range_keeps_outside_dates_and_profile():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    raw_author = "Range Tester"
    repo.db.author_profiles.insert_one({"rawAuthor": raw_author, "displayName": raw_author})

    def insert_day(day: str, suffix: str) -> None:
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"{day}-{suffix}-focus",
                "source": "cur",
                "author": raw_author,
                "projectId": "al",
                "deviceId": "mac-mini",
                "date": day,
                "eventType": "focus",
                "occurredAtUtc": dt.datetime.fromisoformat(f"{day}T08:00:00+00:00"),
                "occurredAtLocal": f"{day}T08:00:00+00:00",
                "receivedAt": dt.datetime.fromisoformat(f"{day}T08:00:01+00:00"),
            }
        )
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"{day}-{suffix}-save",
                "source": "cur",
                "author": raw_author,
                "projectId": "al",
                "deviceId": "mac-mini",
                "date": day,
                "eventType": "file_saved",
                "occurredAtUtc": dt.datetime.fromisoformat(f"{day}T08:02:00+00:00"),
                "occurredAtLocal": f"{day}T08:02:00+00:00",
                "receivedAt": dt.datetime.fromisoformat(f"{day}T08:02:01+00:00"),
            }
        )
        repo.db.day_sessions.insert_one(
            {
                "rawAuthor": raw_author,
                "telegramUsername": "range_tester",
                "date": day,
                "startedAt": dt.datetime.fromisoformat(f"{day}T09:00:00+00:00"),
                "daySeconds": 3600,
            }
        )

    insert_day("2026-05-01", "a")
    insert_day("2026-05-02", "b")
    insert_day("2026-05-03", "c")

    repo.rebuild_aggregates_for_dates("2026-05-01", end_date="2026-05-03")

    result = repo.delete_author_data_for_date_range(raw_author, "2026-05-02", "2026-05-02")

    assert result["ok"] is True
    assert repo.db.author_profiles.find_one({"rawAuthor": raw_author}) is not None

    mid_events = list(repo.db.raw_activity_events.find({"author": raw_author, "date": "2026-05-02"}))
    assert mid_events == []

    outer_dates = {"2026-05-01", "2026-05-03"}

    for day in outer_dates:
        events = list(repo.db.raw_activity_events.find({"author": raw_author, "date": day}))
        assert len(events) == 2

    mid_sessions = list(repo.db.day_sessions.find({"rawAuthor": raw_author, "date": "2026-05-02"}))
    assert mid_sessions == []

    for day in outer_dates:
        sessions = list(repo.db.day_sessions.find({"rawAuthor": raw_author, "date": day}))
        assert len(sessions) == 1

    mid_daily = repo.db.daily_author_activity.find_one({"author": raw_author, "date": "2026-05-02", "source": "cur"})
    assert mid_daily is None

    for day in outer_dates:
        daily = repo.db.daily_author_activity.find_one({"author": raw_author, "date": day, "source": "cur"})
        assert daily is not None
        assert daily["activeSeconds"] == 120

def test_delete_author_data_for_date_range_removes_alias_source_scope_only():
    repo = fake_repository()
    raw_author = "Dmitry Shane"
    alias_author = "Device2"
    repo.db.author_profiles.insert_one({"rawAuthor": raw_author, "displayName": raw_author})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": alias_author, "targetRawAuthor": raw_author})

    def insert_alias_day(day: str) -> None:
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"{day}-device-focus",
                "source": "dev",
                "author": alias_author,
                "projectId": "al",
                "deviceId": "device",
                "batchId": f"batch-{day}",
                "rawReportId": f"raw-{day}",
                "date": day,
                "eventType": "focus",
                "occurredAtUtc": dt.datetime.fromisoformat(f"{day}T08:00:00+00:00"),
                "occurredAtLocal": f"{day}T08:00:00+00:00",
                "receivedAt": dt.datetime.fromisoformat(f"{day}T08:00:01+00:00"),
            }
        )
        repo.db.raw_event_batches.insert_one({"author": alias_author, "batchId": f"batch-{day}", "rawReportId": f"raw-{day}"})
        repo.db.raw_reports.insert_one({"_id": f"raw-{day}"})
        repo.db.report_rows.insert_one({"author": alias_author, "date": day, "source": "dev"})
        repo.db.daily_author_activity.insert_one({"author": alias_author, "date": day, "source": "dev"})
        repo.db.day_sessions.insert_one({"rawAuthor": alias_author, "date": day})

    insert_alias_day("2026-05-01")
    insert_alias_day("2026-05-02")
    insert_alias_day("2026-05-03")

    result = repo.delete_author_data_for_date_range(raw_author, "2026-05-02", "2026-05-02")

    assert result["ok"] is True
    assert result["authorKeys"] == ["Device2", "Dmitry Shane"]
    assert repo.db.raw_activity_events.find_one({"author": alias_author, "date": "2026-05-02"}) is None
    assert repo.db.raw_event_batches.find_one({"batchId": "batch-2026-05-02"}) is None
    assert repo.db.raw_reports.find_one({"_id": "raw-2026-05-02"}) is None
    assert repo.db.report_rows.find_one({"author": alias_author, "date": "2026-05-02"}) is None
    assert repo.db.daily_author_activity.find_one({"author": alias_author, "date": "2026-05-02"}) is None
    assert repo.db.day_sessions.find_one({"rawAuthor": alias_author, "date": "2026-05-02"}) is None

    for day in ("2026-05-01", "2026-05-03"):
        assert repo.db.raw_activity_events.find_one({"author": alias_author, "date": day}) is not None
        assert repo.db.raw_event_batches.find_one({"batchId": f"batch-{day}"}) is not None
        assert repo.db.raw_reports.find_one({"_id": f"raw-{day}"}) is not None

def test_author_alias_rebuilds_raw_events_into_target_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Unknown User", "displayName": "Unknown User"})
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-1",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "metadata": {},
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-2",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 1, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:01:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 1, tzinfo=dt.UTC),
            "metadata": {},
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-3",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "authorEmail": "",
            "projectId": "figma",
            "sessionId": "figma-session",
            "deviceId": "figma-device",
            "batchId": "batch-1",
            "rawReportId": "raw-1",
            "reportType": "auto",
            "date": "2026-04-29",
            "eventType": "file_saved",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 1, 1, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:01:01+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 1, 1, tzinfo=dt.UTC),
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    result = repo.upsert_author_alias("Unknown User", "Dmitry Shane")
    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    profiles = {profile["rawAuthor"]: profile for profile in summary["profiles"]}

    assert result["ok"] is True
    assert "Unknown User" not in authors
    assert "Unknown User" not in profiles
    assert authors["Dmitry Shane"]["activeSeconds"] > 0
    assert authors["Dmitry Shane"]["savedPrefabs"] == [
        {"path": "https://www.figma.com/design/abc123/Game-HUD", "name": "Game HUD", "saveCount": 1}
    ]

def test_author_alias_delete_restores_source_author_listing():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "figma-1",
            "source": "fch",
            "pluginVersion": "0.1.0",
            "author": "Unknown User",
            "projectId": "figma",
            "sessionId": "figma-session",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "metadata": {},
        }
    )

    repo.upsert_author_alias("Unknown User", "Dmitry Shane")
    repo.delete_author_alias("Unknown User")

    assert "Unknown User" in {profile["rawAuthor"] for profile in repo.author_profiles()}

