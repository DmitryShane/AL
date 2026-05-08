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


def test_productivity_ignores_first_break_hour():
    author = _with_productivity({"activeSeconds": 5 * 3600, "idleSeconds": 3 * 3600, "breakSeconds": 60 * 60})

    assert author["productivity"] == 62.5

def test_productivity_penalizes_break_time_after_first_hour():
    author = _with_productivity({"activeSeconds": 5 * 3600, "idleSeconds": 3 * 3600, "breakSeconds": 80 * 60})

    assert author["productivity"] == 60

def test_productivity_exceeds_one_hundred_with_overtime():
    author = _with_productivity(
        {
            "activeSeconds": 8 * 3600,
            "idleSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 2 * 3600,
        }
    )

    assert author["productivity"] == 125.0

def test_date_query_uses_inclusive_range():
    assert _date_query("2026-04-01", "2026-04-30") == {"date": {"$gte": "2026-04-01", "$lte": "2026-04-30"}}

def test_activity_summary_exposes_raw_plugin_day_time_without_changing_effective_plugin_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 120,
            "idleSeconds": 600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 120, "idleSeconds": 600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 10, 5, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 10, 10, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "breakSeconds": 300,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["rawPluginDaySeconds"] == 720
    assert author["pluginDaySeconds"] == 420

def test_activity_summary_exposes_telegram_to_first_activity_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T09:17:30Z",
            "receivedAt": dt.datetime(2026, 4, 28, 9, 17, 31, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramToFirstActivitySeconds"] == 17 * 60 + 30


def test_activity_summary_counts_meeting_as_active_without_plugin_overlap_double_count():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[12]["activeSeconds"] = 10 * 60
    hourly_activity[12]["activeMicroseconds"] = 10 * 60 * 1_000_000
    hourly_activity[12]["fillSegments"] = [{"kind": "active", "startSecond": 5 * 60, "endSecond": 15 * 60}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-28",
            "activeSeconds": 10 * 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 12, 30, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "meetingSeconds": 30 * 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour_12 = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 12)

    assert author["activeSeconds"] == 30 * 60
    assert author["pluginDaySeconds"] == 30 * 60
    assert author["meetingSeconds"] == 30 * 60
    assert hour_12["totals"]["activeSeconds"] == 0
    assert hour_12["totals"]["meetingSeconds"] == 30 * 60


def test_activity_summary_adds_plugin_active_outside_meeting_to_meeting_active():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[13]["activeSeconds"] = 10 * 60
    hourly_activity[13]["activeMicroseconds"] = 10 * 60 * 1_000_000
    hourly_activity[13]["fillSegments"] = [{"kind": "active", "startSecond": 0, "endSecond": 10 * 60}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-28",
            "activeSeconds": 10 * 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 12, 30, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "meetingSeconds": 30 * 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["activeSeconds"] == 40 * 60
    assert author["pluginDaySeconds"] == 40 * 60
    assert author["meetingSeconds"] == 30 * 60


def test_activity_summary_uses_meeting_as_first_activity_after_telegram_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"}
    )
    repo.record_break_event("future_artist", "online", "2026-04-28T12:27:00Z")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "projectId": "",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 12, 41, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 13, 0, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "meetingSeconds": 19 * 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramToFirstActivitySeconds"] == 14 * 60
    assert author["activeSeconds"] == 19 * 60
    assert author["pluginDaySeconds"] == 19 * 60
    assert author["meetingSeconds"] == 19 * 60


def test_activity_summary_fills_intraday_partial_hour_gap_as_idle_not_missed():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Denis Ostrovskiy", "displayName": "Denis Ostrovskiy", "timeZoneId": "Europe/Moscow"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "date": "2026-05-01",
            "startedAt": dt.datetime(2026, 5, 1, 8, 29, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 1, 17, 31, tzinfo=dt.UTC),
            "timeZoneId": "Europe/Moscow",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[16]["activeSeconds"] = 2679
    hourly_activity[16]["activeMicroseconds"] = 2679 * 1_000_000
    hourly_activity[16]["idleSeconds"] = 837
    hourly_activity[16]["idleMicroseconds"] = 837 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "projectId": "unity",
            "date": "2026-05-01",
            "activeSeconds": 2679,
            "idleSeconds": 837,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Denis Ostrovskiy")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Denis Ostrovskiy")["hourlyActivity"]
    hour_16 = next(item for item in hourly if item["hour"] == 16)

    assert set(hour_16) == {"hour", "totals", "fillSegments"}
    assert hour_16["totals"]["activeSeconds"] == 2679
    assert hour_16["totals"]["idleSeconds"] == 921
    assert hour_16["totals"]["missedSeconds"] == 0
    assert hour_16["totals"]["activeSeconds"] + hour_16["totals"]["idleSeconds"] == 3600
    assert author["idleSeconds"] == 29841
    assert author["pluginDaySeconds"] == 32520
    assert summary["totals"]["idleSeconds"] == 29841
    assert summary["totals"]["pluginDaySeconds"] == 32520

def test_activity_summary_overtime_bracket_fill_is_visual_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "America/Vancouver"})
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T14:27:35-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 21, 27, 35, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T16:05:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 23, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 60,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "AL",
            "date": "2026-05-01",
            "activeSeconds": 32400,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Igor Mats")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert set(hourly_by_hour[15]) == {"hour", "totals", "fillSegments"}
    assert hourly_by_hour[15]["totals"]["overtimeSeconds"] == 3600
    assert hourly_by_hour[15]["fillSegments"] == [{"kind": "overtime-fill", "startSecond": 0, "endSecond": 3600}]

def test_activity_summary_cache_hits_and_invalidates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "file_saved", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    first = repo.cached_activity_summary(
        view="activity-lite",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=False,
        include_breakdowns=False,
    )
    second = repo.cached_activity_summary(
        view="activity-lite",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=False,
        include_breakdowns=False,
    )
    repo.invalidate_activity_summary_cache(["2026-04-29"])
    third = repo.cached_activity_summary(
        view="activity-lite",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=False,
        include_breakdowns=False,
    )

    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert third["cache"]["hit"] is False
    assert second["totals"]["activeSeconds"] == first["totals"]["activeSeconds"]

def test_activity_summary_clears_report_metadata_for_zero_activity_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Igor Mats",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Igor Mats",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")

    assert author["pluginDaySeconds"] == 0
    assert author["activeSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["source"] is None
    assert author["pluginVersion"] is None
    assert author["lastRecordedAt"] == ""
    assert author["lastReceivedAt"] == ""

def test_activity_summary_keeps_report_metadata_for_nonzero_activity_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Igor Mats",
            "projectId": "unity",
            "date": "2026-04-29",
            "lastRecordedAt": "2026-04-29T09:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "unity-plugin",
            "author": "Igor Mats",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")

    assert author["pluginDaySeconds"] == 120
    assert author["source"] == "ual"
    assert author["pluginVersion"] == "unity-plugin"
    assert author["lastRecordedAt"] == "2026-04-29T09:00:00+00:00"

def test_activity_after_telegram_overtime_reminder_counts_as_overtime():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:31:00Z",
            "occurredAtLocal": "2026-04-28T19:31:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 31, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:31:30Z",
            "occurredAtLocal": "2026-04-28T19:31:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 31, 30, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})

    assert deltas["overtimeActiveDeltaSeconds"] == 30
    assert daily["overtimeActiveSeconds"] == 30
    assert daily["activeSeconds"] == 0

def test_heartbeat_after_telegram_overtime_reminder_caps_overtime_at_idle_threshold():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T19:31:00Z",
            "occurredAtLocal": "2026-04-28T19:31:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 31, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-04-28T19:36:30Z",
            "occurredAtLocal": "2026-04-28T19:36:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 36, 30, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})

    assert deltas["overtimeActiveDeltaSeconds"] == 300
    assert deltas["idleDeltaSeconds"] == 0
    assert daily["overtimeActiveSeconds"] == 300
    assert daily["idleSeconds"] == 0

def test_telegram_overtime_heartbeat_gap_does_not_fill_whole_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T22:00:00Z",
            "occurredAtLocal": "2026-04-28T22:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 22, 0, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-04-28T22:59:00Z",
            "occurredAtLocal": "2026-04-28T22:59:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 22, 59, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    hour_22 = next(hour for hour in daily["hourlyActivity"] if hour["hour"] == 22)

    assert deltas["overtimeActiveDeltaSeconds"] == 300
    assert daily["overtimeActiveSeconds"] == 300
    assert hour_22["overtimeActiveSeconds"] == 300
    assert hour_22["idleSeconds"] == 0

def test_telegram_online_uses_author_time_zone_for_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})

    repo.record_break_event("igormats", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-28"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-28"

def test_telegram_online_uses_madrid_author_time_zone_for_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "telegramUsername": "dmitryshane", "timeZoneId": "Europe/Madrid"}
    )

    repo.record_break_event("dmitryshane", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"

def test_author_time_zone_update_rebuckets_existing_telegram_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "telegramUsername": "dmitryshane"})

    repo.record_break_event("dmitryshane", "online", "2026-04-29T00:06:17+02:00")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-28"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-28"

    repo.update_author_time_zone("Dmitry Shane", "Europe/Madrid", "CEST")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Madrid"
    assert repo.db.break_events.items[0]["date"] == "2026-04-29"
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Madrid"
    assert repo.db.report_rows.items[0]["timeZoneDisplayName"] == "CEST"

def test_windows_time_zone_update_rebuckets_existing_telegram_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko", "telegramUsername": "ama_deus"})

    repo.record_break_event("ama_deus", "online", "2026-04-29T21:30:00Z")

    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-29"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "UTC"

    repo.update_author_time_zone("Evgeniy Dotsenko", "FLE Standard Time", "(UTC+02:00) Sofia")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.author_profiles.items[0]["timeZoneDisplayName"] == "(UTC+02:00) Sofia"
    assert repo.db.break_events.items[0]["date"] == "2026-04-30"
    assert repo.db.break_events.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-30"
    assert repo.db.report_rows.items[0]["date"] == "2026-04-30"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Sofia"
    assert repo.db.report_rows.items[0]["timeZoneDisplayName"] == "(UTC+02:00) Sofia"

def test_configured_author_time_zone_overrides_windows_time_zone():
    repo = fake_repository()

    repo.update_author_time_zone("Denis Ostrovskiy", "FLE Daylight Time", "FLE Daylight Time")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Kyiv"
    assert repo.db.author_profiles.items[0]["timeZoneDisplayName"] == "FLE Daylight Time"

def test_configured_author_time_zone_normalizes_saved_report_row():
    repo = fake_repository()
    payload = {
        "author": "Denis Ostrovskiy",
        "authorEmail": "denis@example.com",
        "projectId": "project",
        "sessionId": "session",
        "deviceId": "device",
        "timeZoneId": "FLE Daylight Time",
        "timeZoneDisplayName": "FLE Daylight Time",
        "events": [
            {
                "eventId": "event-1",
                "eventType": "scene_changed",
                "occurredAtUtc": "2026-04-29T15:58:07Z",
                "occurredAtLocal": "2026-04-29T18:58:07+03:00",
                "metadata": {"inputType": "LEFTMOUSE"},
                },
                {
                    "eventId": "event-2",
                    "eventType": "scene_changed",
                    "occurredAtUtc": "2026-04-29T15:59:07Z",
                    "occurredAtLocal": "2026-04-29T18:59:07+03:00",
                    "metadata": {"inputType": "LEFTMOUSE"},
            }
        ],
    }

    repo.save_report("bal", "0.1.0", "packet", payload, "challenge")

    assert repo.db.author_profiles.items[0]["timeZoneId"] == "Europe/Kyiv"
    assert repo.db.report_rows.items[0]["timeZoneId"] == "Europe/Kyiv"

def test_author_local_today_summary_uses_observer_selected_date_for_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Kyiv Author", "displayName": "Kyiv Author", "timeZoneId": "Europe/Kyiv"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Vancouver Author", "displayName": "Vancouver Author", "timeZoneId": "America/Vancouver"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Kyiv Author",
            "date": "2026-05-06",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "Europe/Kyiv",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Kyiv Author",
            "date": "2026-05-07",
            "activeSeconds": 600,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "Europe/Kyiv",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Vancouver Author",
            "date": "2026-05-06",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "America/Vancouver",
        }
    )

    vancouver_observer_summary = repo.activity_summary(
        start_date="2026-05-06",
        end_date="2026-05-06",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 7, 0, 30, tzinfo=dt.UTC),
    )
    vancouver_observer_authors = {author["rawAuthor"]: author for author in vancouver_observer_summary["authors"]}
    assert vancouver_observer_authors["Kyiv Author"]["activeSeconds"] == 60
    assert vancouver_observer_authors["Vancouver Author"]["activeSeconds"] == 120

    kyiv_observer_summary = repo.activity_summary(
        start_date="2026-05-07",
        end_date="2026-05-07",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 7, 0, 30, tzinfo=dt.UTC),
    )
    kyiv_observer_authors = {author["rawAuthor"]: author for author in kyiv_observer_summary["authors"]}
    assert kyiv_observer_authors["Kyiv Author"]["activeSeconds"] == 600
    assert kyiv_observer_authors["Vancouver Author"]["activeSeconds"] == 120

def test_activity_summary_regular_date_keeps_calendar_filter_and_all_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Madrid Author", "displayName": "Madrid Author", "timeZoneId": "Europe/Madrid"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Utc Author", "displayName": "Utc Author", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Madrid Author",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Utc Author",
            "date": "2026-04-28",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 28, 22, 30, tzinfo=dt.UTC),
    )

    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    assert authors["Madrid Author"]["activeSeconds"] == 60
    assert authors["Utc Author"]["activeSeconds"] == 0
    assert authors["Utc Author"]["status"] == "stale"

def test_plugin_day_time_is_capped_to_work_window():
    item = {"activeSeconds": 8 * 3600, "idleSeconds": 2 * 3600, "workWindowSeconds": 9 * 3600}

    assert _plugin_day_seconds(item) == 9 * 3600

def test_plugin_day_time_uses_snapshot_work_window():
    item = {"activeSeconds": 2 * 3600, "idleSeconds": 2 * 3600, "workWindowSeconds": 3 * 3600}

    assert _plugin_day_seconds(item) == 3 * 3600

def test_effective_idle_fits_inside_plugin_day_cap():
    plugin_day_seconds = 9 * 3600
    report_active_seconds = 30 * 60
    report_idle_seconds = 9 * 3600
    effective_active_seconds = min(report_active_seconds, plugin_day_seconds)
    effective_idle_seconds = min(report_idle_seconds, max(0, plugin_day_seconds - effective_active_seconds))

    assert effective_active_seconds == 30 * 60
    assert effective_idle_seconds == (8 * 3600) + (30 * 60)
