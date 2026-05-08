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
    _date_query,
    _empty_event_deltas,
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
from al_backend.hourly_fill_rules import (
    empty_hourly_activity,
    apply_breaks_to_hourly_activity,
    add_break_interval_to_buckets,
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


from tests.activity_status_helpers import _author_from_summary, _author_status, _insert_presence_daily_activity


def test_fresh_normal_plugin_report_without_telegram_offline_keeps_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 1, tzinfo=dt.UTC))
    assert author["status"] == "online"
    assert "stalePresence" not in author

def test_stale_plugin_report_before_telegram_online_does_not_create_reports_stopped():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"
    assert repo.db.status_events.count_documents({"rawAuthor": "Future Artist", "reason": "reports_stopped"}) == 0
    assert repo.db.report_rows.count_documents({"author": "Future Artist", "source": "status", "statusReason": "reports_stopped"}) == 0

def test_telegram_offline_after_fresh_plugin_report_marks_author_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 6, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"

def test_stale_presence_reports_when_unity_reports_stop_without_telegram_signoff():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 16, 0, tzinfo=dt.UTC),
        }
    )
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "reports"

def test_reports_stopped_skipped_when_last_raw_report_recent_despite_stale_report_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "lastRawReportReceivedAt": dt.datetime(2026, 4, 28, 18, 29, 0, tzinfo=dt.UTC),
        }
    )
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC))
    assert author["status"] == "online"

def test_historical_activity_summary_does_not_mark_stopped_reports_as_realtime_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))

    author = _author_from_summary(repo, dt.datetime(2026, 4, 29, 18, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"

def test_historical_activity_summary_keeps_selected_day_telegram_offline_gray():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.record_break_event("future_artist", "online", "2026-04-29T09:00:00Z")

    author = _author_from_summary(repo, dt.datetime(2026, 4, 29, 18, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"

def test_historical_activity_summary_ignores_current_profile_raw_report_presence():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "Europe/Kyiv",
            "lastRawReportReceivedAt": dt.datetime(2026, 5, 5, 17, 49, 35, tzinfo=dt.UTC),
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-04",
        end_date="2026-05-04",
        now=dt.datetime(2026, 5, 5, 17, 50, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"
    assert repo.db.status_events.count_documents({"rawAuthor": "Future Artist", "reason": "reports_stopped"}) == 0
    assert repo.db.report_rows.count_documents({"author": "Future Artist", "source": "status", "statusReason": "reports_stopped"}) == 0

def test_live_activity_summary_keeps_current_profile_raw_report_presence_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "Europe/Kyiv",
            "lastRawReportReceivedAt": dt.datetime(2026, 5, 5, 17, 49, 35, tzinfo=dt.UTC),
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-05-05",
            "startedAt": dt.datetime(2026, 5, 5, 8, 47, 44, tzinfo=dt.UTC),
        }
    )

    summary = repo.activity_summary(
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 5, 17, 50, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "online"

def test_regular_date_still_applies_reports_stopped_when_it_is_author_local_today():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 16, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": dt.datetime(2026, 4, 28, 20, 0, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 29, 1, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "reports"

def test_closed_telegram_workday_does_not_apply_reports_stopped_even_with_plugin_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 16, 0, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 4, 28, 23, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": dt.datetime(2026, 4, 28, 20, 0, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    frozen_now = dt.datetime(2026, 4, 29, 1, 30, tzinfo=dt.UTC)
    author = _author_from_summary(repo, frozen_now)

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"
    assert repo.db.status_events.count_documents({"rawAuthor": "Future Artist", "reason": "reports_stopped"}) == 0

def test_open_telegram_workday_still_applies_reports_stopped_when_plugin_stale():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 16, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": dt.datetime(2026, 4, 28, 20, 0, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 29, 1, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "reports"

def test_regular_date_author_local_today_before_workday_start_is_gray_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "America/Vancouver",
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 29, 1, 30, tzinfo=dt.UTC))

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"

def test_with_author_presence_stale_presence_telegram_without_reports_stopped():
    frozen_now = dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC)
    author = {
        "rawAuthor": "Future Artist",
        "displayName": "Future Artist",
        "activeSeconds": 60,
        "idleSeconds": 0,
        "breakSeconds": 0,
        "overtimeActiveSeconds": 0,
        "activityCounts": [{"type": "selection", "count": 1}],
        "lastReceivedAt": dt.datetime(2026, 4, 28, 18, 9, tzinfo=dt.UTC),
    }
    wrapped = _with_activity_mix(_with_productivity(author))
    result = _with_author_presence(
        wrapped,
        60,
        frozen_now,
        {"offlineAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC)},
    )
    assert result["status"] == "stale"
    assert result["stalePresence"] == "telegram"

def test_with_author_presence_stale_presence_both_when_telegram_offline_and_reports_stopped():
    frozen_now = dt.datetime(2026, 4, 28, 18, 30, tzinfo=dt.UTC)
    author = {
        "rawAuthor": "Future Artist",
        "displayName": "Future Artist",
        "activeSeconds": 60,
        "idleSeconds": 0,
        "breakSeconds": 0,
        "overtimeActiveSeconds": 0,
        "activityCounts": [{"type": "selection", "count": 1}],
        "lastReceivedAt": dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC),
    }
    wrapped = _with_activity_mix(_with_productivity(author))
    result = _with_author_presence(
        wrapped,
        60,
        frozen_now,
        {"offlineAt": dt.datetime(2026, 4, 28, 18, 5, tzinfo=dt.UTC)},
    )
    assert result["status"] == "stale"
    assert result["stalePresence"] == "both"
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 0,
            "overtimeActiveDeltaMicroseconds": 0,
        }
    )

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "stale"

def test_overtime_report_after_telegram_offline_keeps_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 30,
            "overtimeActiveDeltaMicroseconds": 30 * 1_000_000,
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC))
    assert author["status"] == "online"
    assert "stalePresence" not in author

def test_overtime_report_after_telegram_offline_expires_with_stale_threshold():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 30,
            "overtimeActiveDeltaMicroseconds": 30 * 1_000_000,
        }
    )

    author = _author_from_summary(repo, dt.datetime(2026, 4, 28, 18, 13, 1, tzinfo=dt.UTC))
    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"

def test_heartbeat_idle_after_telegram_offline_is_suppressed_from_aggregates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:00Z",
            "occurredAtLocal": "2026-04-28T18:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
        }
    )
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-04-28T18:10:00Z",
            "occurredAtLocal": "2026-04-28T18:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 0
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily["idleSeconds"] == 0

def test_rebuild_suppresses_stored_heartbeat_idle_after_telegram_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-1",
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-28T18:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "heartbeat-1",
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-28T18:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
        }
    )

    repo.rebuild_aggregates_if_needed(force=True)

    assert not [
        row
        for row in repo.db.report_rows.items
        if row.get("source") == "ual" and row.get("idleDeltaSeconds", 0) > 0
    ]
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily["idleSeconds"] == 0

def test_red_offline_creates_status_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 29, 9, 3, 1, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", source="status")

    assert author["status"] == "stale"
    assert author["stalePresence"] == "reports"
    assert repo.db.status_events.count_documents({"rawAuthor": "Future Artist", "statusEventType": "offline"}) == 1
    assert page["reports"][0]["source"] == "status"
    assert page["reports"][0]["reportType"] == "status"
    assert page["reports"][0]["statusEventType"] == "offline"

def test_historical_stale_does_not_create_status_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 30, 9, 0, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "stale"
    assert author["stalePresence"] == "telegram"
    assert repo.db.status_events.count_documents({}) == 0

def test_status_online_row_sorts_before_returning_plugin_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.status_states.insert_one({"rawAuthor": "Future Artist", "status": "offline"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:05:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.activity_summary(
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 29, 9, 5, 30, tzinfo=dt.UTC),
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29")

    assert [report["source"] for report in page["reports"][:2]] == ["status", "ual"]
    assert page["reports"][0]["statusEventType"] == "online"

def test_fresh_daily_activity_without_report_row_resumes_reports_stopped_status():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.status_states.insert_one({"rawAuthor": "Future Artist", "status": "offline"})
    repo.db.report_rows.insert_one(
        {
            "source": "status",
            "pluginVersion": "status",
            "author": "Future Artist",
            "date": "2026-04-29",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "offline",
            "statusReason": "reports_stopped",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "receivedAt": dt.datetime(2026, 4, 29, 8, 58, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:05:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "focus", "count": 1}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 9, 5, 30, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "online"
    assert author["lastReceivedAt"] == "2026-04-29T09:05:00+00:00"
    assert repo.db.status_events.items[-1]["statusEventType"] == "online"
    assert repo.db.status_events.items[-1]["reason"] == "reports_resumed"
    assert repo.db.status_states.items[0]["status"] == "online"

def test_fresh_daily_activity_resumes_when_status_state_online_but_latest_event_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.status_states.insert_one({"rawAuthor": "Future Artist", "status": "online"})
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:05:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "focus", "count": 1}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 9, 5, 30, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    status_events = [event for event in repo.db.status_events.items if event.get("rawAuthor") == "Future Artist"]

    assert author["status"] == "online"
    assert status_events[-1]["statusEventType"] == "online"
    assert status_events[-1]["reason"] == "reports_resumed"
    assert repo.db.status_states.items[0]["status"] == "online"

def test_stale_daily_activity_without_report_row_does_not_resume_reports_stopped_status():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.status_states.insert_one({"rawAuthor": "Future Artist", "status": "offline"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:05:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "focus", "count": 1}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 9, 8, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "stale"
    assert not [event for event in repo.db.status_events.items if event.get("reason") == "reports_resumed"]
    assert repo.db.status_states.items[0]["status"] == "offline"

def test_reports_page_hides_source_rows_between_status_offline_and_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.record_status_event(
        "Future Artist",
        "online",
        dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
        "UTC",
        "reports_resumed",
        dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:11:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 11, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29")

    assert [(report["source"], report.get("statusEventType")) for report in page["reports"]] == [
        ("ual", None),
        ("ual", None),
        ("status", "online"),
        ("ual", None),
        ("status", "offline"),
    ]

def test_reports_page_hides_only_source_rows_after_open_status_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T08:55:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 8, 55, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29")

    assert [(report["source"], report.get("statusEventType")) for report in page["reports"]] == [
        ("status", "offline"),
        ("ual", None),
    ]

def test_reports_page_keeps_telegram_offline_inside_open_status_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "telegram",
            "reportType": "telegram",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
            "telegramEventType": "offline",
            "reminderAction": "overtime",
        }
    )

    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29")

    assert [(report["source"], report.get("telegramEventType")) for report in page["reports"]] == [
        ("telegram", "offline"),
        ("status", None),
    ]

def test_status_offline_interval_turns_return_report_into_idle_delta():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "focus",
            "occurredAtUtc": "2026-04-29T09:00:00Z",
            "occurredAtLocal": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
        "UTC",
        "manual_local_status",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
    )
    repo.record_status_event(
        "Future Artist",
        "online",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_resumed",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "scene_changed",
            "occurredAtUtc": "2026-04-29T09:30:00Z",
            "occurredAtLocal": "2026-04-29T09:30:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["idleDeltaSeconds"] == 55 * 60
    assert deltas["activityCountDeltas"] == []

def test_status_offline_interval_suppresses_activity_payload_after_idle_accounted():
    repo = fake_repository()
    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "focus",
            "occurredAtUtc": "2026-04-29T09:00:00Z",
            "occurredAtLocal": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
        "UTC",
        "manual_local_status",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
    )
    repo.record_status_event(
        "Future Artist",
        "online",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_resumed",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
    )
    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "scene_changed",
            "occurredAtUtc": "2026-04-29T09:30:00Z",
            "occurredAtLocal": "2026-04-29T09:30:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "file_saved",
            "occurredAtUtc": "2026-04-29T09:45:00Z",
            "occurredAtLocal": "2026-04-29T09:45:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "metadata": {"path": "Assets/Level.prefab", "name": "Level"},
        }
    )

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["idleDeltaSeconds"] == 0
    assert deltas["activityCountDeltas"] == []
    assert deltas["savedPrefabDeltas"] == []

def test_scoped_rebuild_counts_raw_activity_during_reports_stopped_interval():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
    )
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
        "UTC",
        "reports_stopped",
        dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
    )
    raw_events = [
        {
            "eventId": "focus-after-offline",
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "authorEmail": "future@example.com",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 9, 20, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T09:20:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 20, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
        },
        {
            "eventId": "selection-after-offline",
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "authorEmail": "future@example.com",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 9, 22, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T09:22:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 22, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
        },
    ]

    for event in raw_events:
        repo.db.raw_activity_events.insert_one(event)

    repo.rebuild_aggregates_for_dates("2026-04-29", dates=["2026-04-29"], authors=["Future Artist"])
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-29", "source": "cur"}, {"_id": 0})

    assert daily["activeSeconds"] == 120
    assert daily["activityCounts"] == [{"type": "focus", "count": 1}, {"type": "select", "count": 1}]

    repo.record_status_event(
        "Future Artist",
        "online",
        dt.datetime(2026, 4, 29, 9, 15, tzinfo=dt.UTC),
        "UTC",
        "reports_resumed",
        dt.datetime(2026, 4, 29, 9, 15, tzinfo=dt.UTC),
    )
    repo.rebuild_aggregates_for_dates("2026-04-29", dates=["2026-04-29"], authors=["Future Artist"])
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-29", "source": "cur"}, {"_id": 0})

    assert daily["activeSeconds"] == 120
    assert daily["activityCounts"] == [{"type": "focus", "count": 1}, {"type": "select", "count": 1}]

def test_rebuild_status_idle_batch_keeps_last_event_recorded_at():
    repo = fake_repository()
    batch_id = "status-return-batch"
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": batch_id,
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": "Future Artist",
            "authorEmail": "",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "auto",
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "before-offline",
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
        "UTC",
        "manual_local_status",
        dt.datetime(2026, 4, 29, 9, 5, tzinfo=dt.UTC),
    )
    repo.record_status_event(
        "Future Artist",
        "online",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
        "UTC",
        "reports_resumed",
        dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "idle-gap-delta",
            "batchId": batch_id,
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "scene_changed",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 9, 30, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T09:30:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "last-empty-event",
            "batchId": batch_id,
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": "2026-04-29",
            "eventType": "heartbeat",
            "occurredAtUtc": dt.datetime(2026, 4, 29, 9, 59, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-29T09:59:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    repo.rebuild_aggregates_if_needed(force=True)
    row = repo.db.report_rows.find_one({"source": "cur", "author": "Future Artist", "batchId": batch_id}, {"_id": 0})

    assert row["activeDeltaSeconds"] == 0
    assert row["idleDeltaSeconds"] == 55 * 60
    assert row["recordedAt"] == "2026-04-29T09:59:00+00:00"

def test_reports_stopped_gap_heartbeats_count_idle_through_normal_delta_path():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    day = "2026-05-04"
    author = "Future Artist"
    tz = "UTC"
    offline_at = dt.datetime(2026, 5, 4, 0, 21, 45, 261000, tzinfo=dt.UTC)
    online_at = dt.datetime(2026, 5, 4, 0, 41, 9, 809000, tzinfo=dt.UTC)

    repo.db.status_events.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "statusEventType": "offline",
            "transitionAt": offline_at,
            "receivedAt": offline_at,
            "timeZoneId": tz,
            "reason": "reports_stopped",
        }
    )
    repo.db.status_events.insert_one(
        {
            "rawAuthor": author,
            "date": day,
            "statusEventType": "online",
            "transitionAt": online_at,
            "receivedAt": online_at,
            "timeZoneId": tz,
            "reason": "reports_resumed",
        }
    )

    def batch_doc(batch_id: str, received: dt.datetime) -> dict[str, Any]:
        return {
            "batchId": batch_id,
            "source": "cur",
            "pluginVersion": "cursor-plugin",
            "author": author,
            "authorEmail": "",
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "receivedAt": received,
            "reportType": "auto",
        }

    def evt(eid: str, batch_id: str, etype: str, occurred: dt.datetime, received: dt.datetime) -> dict[str, Any]:
        return {
            "eventId": eid,
            "batchId": batch_id,
            "source": "cur",
            "author": author,
            "projectId": "AL",
            "sessionId": "cursor-session",
            "deviceId": "device",
            "date": day,
            "eventType": etype,
            "occurredAtUtc": occurred,
            "occurredAtLocal": occurred.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
            "receivedAt": received,
            "timeZoneId": tz,
        }

    bid_early = "rs-gap-early"
    bid_gap = "rs-gap-mid"
    bid_resume = "rs-gap-resume"

    repo.db.raw_event_batches.insert_one(batch_doc(bid_early, dt.datetime(2026, 5, 4, 0, 20, 44, tzinfo=dt.UTC)))
    repo.db.raw_event_batches.insert_one(batch_doc(bid_gap, dt.datetime(2026, 5, 4, 0, 22, 0, tzinfo=dt.UTC)))
    repo.db.raw_event_batches.insert_one(batch_doc(bid_resume, online_at))

    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-pre-focus",
            bid_early,
            "focus",
            dt.datetime(2026, 5, 4, 0, 20, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 4, 0, 20, 44, tzinfo=dt.UTC),
        )
    )
    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-pre-hb",
            bid_early,
            "heartbeat",
            dt.datetime(2026, 5, 4, 0, 20, 43, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 4, 0, 20, 44, tzinfo=dt.UTC),
        )
    )
    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-gap-hb-a",
            bid_gap,
            "heartbeat",
            dt.datetime(2026, 5, 4, 0, 22, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 4, 0, 22, 0, tzinfo=dt.UTC),
        )
    )
    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-gap-hb-b",
            bid_gap,
            "heartbeat",
            dt.datetime(2026, 5, 4, 0, 30, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 4, 0, 22, 5, tzinfo=dt.UTC),
        )
    )
    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-resume-focus",
            bid_resume,
            "focus",
            dt.datetime(2026, 5, 4, 0, 40, 55, tzinfo=dt.UTC),
            online_at,
        )
    )
    repo.db.raw_activity_events.insert_one(
        evt(
            "rs-resume-hb",
            bid_resume,
            "heartbeat",
            dt.datetime(2026, 5, 4, 0, 41, 9, 384000, tzinfo=dt.UTC),
            online_at,
        )
    )

    repo.rebuild_aggregates_if_needed(force=True)

    rows = list(repo.db.report_rows.find({"source": "cur", "author": author, "date": day}, {"_id": 0, "idleDeltaSeconds": 1}))
    total_idle = sum(int(r.get("idleDeltaSeconds") or 0) for r in rows)

    assert total_idle == 1255

def test_status_events_rematerialize_report_rows_after_rebuild():
    repo = fake_repository()
    repo.record_status_event(
        "Future Artist",
        "offline",
        dt.datetime(2026, 4, 29, 9, 2, 1, tzinfo=dt.UTC),
        "UTC",
    )
    repo.db.report_rows.delete_many({})

    repo._materialize_status_report_rows()
    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", source="status")

    assert len(page["reports"]) == 1
    assert page["reports"][0]["statusEventType"] == "offline"

def test_reports_stopped_closed_interval_does_not_hide_plugin_report_rows():
    repo = fake_repository()
    day = "2026-04-30"
    plugin_row = {
        "source": "ual",
        "author": "A",
        "date": day,
        "recordedAt": "2026-04-30T10:30:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 30, 10, 30, tzinfo=dt.UTC),
        "reportType": "auto",
    }
    status_rows = [
        {
            "source": "status",
            "author": "A",
            "date": day,
            "recordedAt": "2026-04-30T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 10, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "offline",
            "statusReason": "reports_stopped",
        },
        {
            "source": "status",
            "author": "A",
            "date": day,
            "recordedAt": "2026-04-30T11:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 11, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "online",
            "statusReason": "reports_resumed",
        },
    ]

    intervals = repo._status_intervals_for_reports(status_rows)

    assert repo._is_report_inside_status_interval(plugin_row, intervals) is False

def test_reports_stopped_open_interval_hides_current_plugin_report_rows():
    repo = fake_repository()
    day = "2026-04-30"
    plugin_row = {
        "source": "ual",
        "author": "A",
        "date": day,
        "recordedAt": "2026-04-30T10:30:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 30, 10, 30, tzinfo=dt.UTC),
        "reportType": "auto",
    }
    status_rows = [
        {
            "source": "status",
            "author": "A",
            "date": day,
            "recordedAt": "2026-04-30T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 10, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "offline",
            "statusReason": "reports_stopped",
        }
    ]

    intervals = repo._status_intervals_for_reports(status_rows)

    assert repo._is_report_inside_status_interval(plugin_row, intervals) is True
