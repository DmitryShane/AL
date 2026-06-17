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
from al_backend.routers.reports import plugin_config, reports_summary
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


def test_activity_mix_hides_items_with_zero_display_percent():
    author = _with_activity_mix(
        {
            "activityCounts": [
                {"type": "scene_view_navigation", "count": 883},
                {"type": "select", "count": 398},
                {"type": "scene_saved", "count": 5},
                {"type": "undo_redo", "count": 5},
                {"type": "hold", "count": 4},
                {"type": "play_mode", "count": 4},
                {"type": "click", "count": 2},
            ],
            "overtimeActivityCounts": [{"type": "click", "count": 0}],
        }
    )

    assert author["activityMix"] == [
        {"type": "scene_view_navigation", "count": 883, "percent": 68},
        {"type": "select", "count": 398, "percent": 31},
    ]
    assert author["overtimeActivityMix"] == []


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


def test_empty_daily_heartbeat_does_not_keep_not_started_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev-ios",
            "author": "Igor Mats",
            "projectId": "device",
            "date": "2026-05-12",
            "lastRecordedAt": "2026-05-12T00:01:29.0436120-07:00",
            "lastReceivedAt": dt.datetime(2026, 5, 12, 7, 1, 36, tzinfo=dt.UTC),
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 12,
            "overtimeActiveMicroseconds": 12_000_000,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "vscode",
            "date": "2026-05-12",
            "lastRecordedAt": "2026-05-12T04:19:08.962-07:00",
            "lastReceivedAt": dt.datetime(2026, 5, 12, 11, 19, 9, tzinfo=dt.UTC),
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-12",
        end_date="2026-05-12",
        now=dt.datetime(2026, 5, 12, 11, 19, 49, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")

    assert author["lastReceivedAt"] == "2026-05-12T07:01:36+00:00"
    assert author["status"] == "stale"


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


def test_activity_summary_closed_meeting_interval_replaces_live_meeting_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[8]["meetingSeconds"] = 20 * 60
    repo.db.daily_author_activity.insert_one(
        {
            "source": "discord",
            "reportType": "meeting",
            "author": "Igor Mats",
            "projectId": "discord",
            "date": "2026-05-08",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "startedAt": dt.datetime(2026, 5, 8, 8, 16, 19, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 8, 8, 43, 32, tzinfo=dt.UTC),
            "date": "2026-05-08",
            "timeZoneId": "UTC",
            "meetingSeconds": 27 * 60 + 13,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-08", end_date="2026-05-08")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")
    hour_8 = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 8)

    assert author["meetingSeconds"] == 27 * 60 + 13
    assert author["activeSeconds"] == 27 * 60 + 13
    assert author["pluginDaySeconds"] == 27 * 60 + 13
    assert hour_8["totals"]["meetingSeconds"] == 27 * 60 + 13


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


def test_activity_summary_meeting_active_is_not_limited_by_plugin_day_cap():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[9]["activeSeconds"] = 32_000
    hourly_activity[9]["activeMicroseconds"] = 32_000 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-28",
            "activeSeconds": 32_000,
            "idleSeconds": 0,
            "workWindowSeconds": 32_400,
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

    assert author["activeSeconds"] == 32_000 + 30 * 60
    assert author["pluginDaySeconds"] == 32_400
    assert author["meetingSeconds"] == 30 * 60


def test_activity_summary_discord_daily_item_does_not_hide_plugin_meeting_overlap():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[17]["activeSeconds"] = 10 * 60
    hourly_activity[17]["activeMicroseconds"] = 10 * 60 * 1_000_000
    hourly_activity[17]["fillSegments"] = [{"kind": "active", "startSecond": 10 * 60, "endSecond": 20 * 60}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "projectId": "",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32_400,
            "lastRecordedAt": "2026-04-28T16:00:00+00:00",
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-28",
            "activeSeconds": 10 * 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32_400,
            "lastRecordedAt": "2026-04-28T18:00:00+00:00",
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 17, 30, tzinfo=dt.UTC),
            "date": "2026-04-28",
            "timeZoneId": "UTC",
            "meetingSeconds": 30 * 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour_17 = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 17)

    assert author["activeSeconds"] == 30 * 60
    assert author["pluginDaySeconds"] == 30 * 60
    assert author["meetingSeconds"] == 30 * 60
    assert hour_17["totals"]["activeSeconds"] == 0
    assert hour_17["totals"]["meetingSeconds"] == 30 * 60


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

def test_historical_single_day_activity_summary_miss_returns_preparing_then_reads_snapshot():
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
        view="activity",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )
    snapshots = list(repo.db.activity_day_summary_snapshots.find({"date": "2026-04-29"}))
    repo.materialize_activity_author_day_summary_snapshots(limit=10, now=dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC))

    def fail_activity_summary(*_args, **_kwargs):
        raise AssertionError("snapshot hit should not rebuild activity summary")

    repo.activity_summary = fail_activity_summary
    second = repo.cached_activity_summary(
        view="activity",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert first["snapshot"] == {
        "hit": False,
        "status": "preparing",
        "date": "2026-04-29",
        "partial": True,
        "readyAuthors": [],
        "pendingAuthors": ["Future Artist"],
        "preparingAuthors": ["Future Artist"],
        "liveAuthors": [],
    }
    assert snapshots == []
    assert second["snapshot"]["hit"] is True
    assert second["snapshot"]["date"] == "2026-04-29"
    assert second["snapshot"]["readyAuthors"] == ["Future Artist"]
    assert second["totals"]["activeSeconds"] == 120

def test_activity_reports_summary_does_not_call_global_list_authors():
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
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    def fail_list_authors():
        raise AssertionError("activity summary should derive top-level authors from activitySummary")

    repo.list_authors = fail_list_authors
    response = reports_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        date_mode=None,
        view="activity",
        author_service=repo,
        settings_service=repo,
        summary_service=repo,
    )

    assert response.authors == ["Future Artist"]
    assert response.activity_summary["authors"][0]["rawAuthor"] == "Future Artist"

def test_empty_historical_single_day_returns_day_off_summary_with_zero_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "team": "Tech Lead",
            "timeZoneId": "Europe/Kyiv",
            "authorColor": "#7c3aed",
            "githubUsername": "igormats",
        }
    )
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Evgeniy Dotsenko",
            "displayName": "Evgeniy Dotsenko",
            "team": "QA",
            "timeZoneId": "Europe/Kyiv",
        }
    )

    summary = repo.cached_activity_summary(
        view="activity",
        start_date="2026-04-26",
        end_date="2026-04-26",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert summary["snapshot"] == {"hit": False, "status": "empty", "date": "2026-04-26"}
    assert summary["totals"]["activeSeconds"] == 0
    assert summary["activityMix"] == []
    assert [author["rawAuthor"] for author in summary["authors"]] == ["Evgeniy Dotsenko", "Igor Mats"]
    assert all(author["activeSeconds"] == 0 for author in summary["authors"])
    assert all(author["status"] == "stale" and author["stalePresence"] == "telegram" for author in summary["authors"])
    assert next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")["avatarUrl"].startswith(
        "/api/v1/avatars/author?rawAuthor=Igor%20Mats"
    )
    assert len(summary["hourlyActivityByAuthor"]) == 2
    assert repo.db.activity_day_summary_snapshots.count_documents({"date": "2026-04-26"}) == 0


def test_historical_reports_page_reads_author_day_snapshot_payload():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
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
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:15:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
        }
    )

    repo.materialize_activity_author_day_summary_snapshots(limit=10, now=dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC))
    repo.db.report_rows.delete_many({})

    page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", author="Future Artist")

    assert page["snapshot"] == {"hit": True, "date": "2026-04-29", "rawAuthor": "Future Artist"}
    assert page["total"] == 1
    assert page["reports"][0]["source"] == "cur"
    assert page["reports"][0]["displayName"] == "Future Artist"


def test_historical_reports_page_falls_back_for_snapshot_filters_and_later_pages():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.activity_author_day_summary_snapshots.insert_one(
        {
            "date": "2026-04-29",
            "rawAuthor": "Future Artist",
            "snapshotVersion": version,
            "payload": {
                "reportsPage": {
                    "reports": [
                        {
                            "source": "cur",
                            "author": "Future Artist",
                            "displayName": "Future Artist",
                            "date": "2026-04-29",
                            "recordedAt": "2026-04-29T10:15:00+00:00",
                            "receivedAt": "2026-04-29T10:15:00+00:00",
                        }
                    ],
                    "total": 2,
                    "limit": 1,
                    "offset": 0,
                    "sources": ["cur", "ual"],
                }
            },
        }
    )
    for index, source in enumerate(("cur", "ual")):
        repo.db.report_rows.insert_one(
            {
                "source": source,
                "author": "Future Artist",
                "date": "2026-04-29",
                "recordedAt": f"2026-04-29T10:{15 + index:02d}:00+00:00",
                "receivedAt": dt.datetime(2026, 4, 29, 10, 15 + index, tzinfo=dt.UTC),
                "activeDeltaSeconds": 60,
                "idleDeltaSeconds": 0,
            }
        )

    filtered_page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", author="Future Artist", source="ual")
    later_page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", author="Future Artist", limit=1, offset=1)

    assert "snapshot" not in filtered_page
    assert filtered_page["reports"][0]["source"] == "ual"
    assert "snapshot" not in later_page
    assert later_page["offset"] == 1


def test_author_scoped_snapshot_invalidation_keeps_other_author_day_snapshots():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    day_date = "2026-04-29"

    for raw_author in ("Alpha Artist", "Beta Artist"):
        repo.db.activity_author_day_summary_snapshots.insert_one(
            {
                "date": day_date,
                "rawAuthor": raw_author,
                "snapshotVersion": version,
                "payload": {"author": {"rawAuthor": raw_author}},
            }
        )

    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": day_date,
            "view": repo.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
            "snapshotVersion": version,
            "payload": {"authors": []},
        }
    )

    repo.invalidate_activity_summary_cache([day_date], ["Alpha Artist"])

    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": day_date, "rawAuthor": "Alpha Artist"}) is None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": day_date, "rawAuthor": "Beta Artist"}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": day_date, "snapshotVersion": version}) is None


def test_future_single_day_returns_empty_summary_with_zero_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "team": "Lead"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "team": "Tech Lead"})

    summary = repo.cached_activity_summary(
        view="activity",
        start_date="2099-06-11",
        end_date="2099-06-11",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert summary["snapshot"] == {"hit": False, "status": "empty", "date": "2099-06-11"}
    assert [author["rawAuthor"] for author in summary["authors"]] == ["Dmitry Shane", "Igor Mats"]
    assert all(author["activeSeconds"] == 0 for author in summary["authors"])
    assert len(summary["hourlyActivityByAuthor"]) == 2


def test_completed_day_snapshot_adds_zero_rows_for_inactive_people_when_one_author_has_input():
    repo = fake_repository()
    day_date = "2026-06-06"
    now = dt.datetime(2026, 6, 10, 12, tzinfo=dt.UTC)

    for raw_author, display_name in (
        ("Dmitry Shane", "Dmitry Shane"),
        ("Igor Mats", "Igor Mats"),
        ("Denis Ostrovskiy", "Denis Ostrovskiy"),
        ("Евгений Доценко", "Evgeniy Dotsenko"),
    ):
        repo.db.author_profiles.insert_one(
            {
                "rawAuthor": raw_author,
                "displayName": display_name,
                "timeZoneId": "UTC",
            }
        )
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Ketchapp",
            "displayName": "Ketchapp",
            "profileType": "publisher",
            "timeZoneId": "UTC",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "codex",
            "author": "Dmitry Shane",
            "projectId": "AL",
            "date": day_date,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    materialized = repo.materialize_activity_author_day_summary_snapshots(limit=10, now=now)
    summary = repo.cached_activity_summary(
        view="activity",
        start_date=day_date,
        end_date=day_date,
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert [item["rawAuthor"] for item in materialized["processed"]] == ["Dmitry Shane"]
    assert summary["snapshot"]["hit"] is True
    assert summary["snapshot"]["date"] == day_date
    assert summary["snapshot"]["status"] == "ready"
    assert summary["snapshot"]["readyAuthors"] == ["Dmitry Shane"]
    assert repo.db.activity_day_summary_snapshots.find_one({"date": day_date}) is not None
    assert [author["rawAuthor"] for author in summary["authors"]] == [
        "Denis Ostrovskiy",
        "Dmitry Shane",
        "Евгений Доценко",
        "Igor Mats",
    ]
    assert "Ketchapp" not in {author["rawAuthor"] for author in summary["authors"]}
    assert [item["rawAuthor"] for item in summary["hourlyActivityByAuthor"]] == [
        "Denis Ostrovskiy",
        "Dmitry Shane",
        "Евгений Доценко",
        "Igor Mats",
    ]

    for raw_author in ("Igor Mats", "Denis Ostrovskiy", "Евгений Доценко"):
        author = next(item for item in summary["authors"] if item["rawAuthor"] == raw_author)
        assert author["activeSeconds"] == 0
        assert author["idleSeconds"] == 0
        assert author["meetingSeconds"] == 0
        assert author["breakSeconds"] == 0
        assert author["overtimeActiveSeconds"] == 0
        assert author["productivity"] == 0

def test_historical_hourly_summary_uses_day_snapshot_payload():
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
        view="activity-hourly",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=False,
    )

    assert first["snapshot"] == {
        "hit": False,
        "status": "preparing",
        "date": "2026-04-29",
        "partial": True,
        "readyAuthors": [],
        "pendingAuthors": ["Future Artist"],
        "preparingAuthors": ["Future Artist"],
        "liveAuthors": [],
    }
    assert first["hourlyActivityByAuthor"][0]["rawAuthor"] == "Future Artist"
    assert len(list(repo.db.activity_day_summary_snapshots.find({"date": "2026-04-29"}))) == 0

def test_activity_day_summary_snapshot_is_ignored_for_live_and_ranges():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Today Author", "displayName": "Today Author", "timeZoneId": "UTC"})
    today = dt.datetime.now(dt.UTC).date().isoformat()
    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": today,
            "view": "activity-day",
            "snapshotVersion": repo.activity_day_summary_snapshot_version(),
            "payload": {"totals": {"activeSeconds": 999}, "authors": [], "hourlyActivityByAuthor": []},
        }
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": "2026-04-29",
            "view": "activity-day",
            "snapshotVersion": repo.activity_day_summary_snapshot_version(),
            "payload": {"totals": {"activeSeconds": 999}, "authors": [], "hourlyActivityByAuthor": []},
        }
    )

    live = repo.cached_activity_summary(
        view="activity",
        start_date=today,
        end_date=today,
        date_mode="authorLocalToday",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )
    range_summary = repo.cached_activity_summary(
        view="activity",
        start_date="2026-04-28",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert "snapshot" not in live
    assert live["totals"]["activeSeconds"] != 999
    assert "snapshot" not in range_summary
    assert range_summary["totals"]["activeSeconds"] != 999

def test_activity_day_summary_snapshot_uses_author_timezone_without_utc_today_fallback():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "Europe/Madrid"})
    version = repo.activity_day_summary_snapshot_version()
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "projectId": "unity",
            "author": "Dmitry Shane",
            "date": "2026-06-09",
            "activeSeconds": 60,
        }
    )
    repo.db.activity_author_day_summary_snapshots.insert_one(
        {
            "date": "2026-06-09",
            "rawAuthor": "Dmitry Shane",
            "snapshotVersion": version,
            "payload": {"author": {"rawAuthor": "Dmitry Shane"}, "hourlyActivity": empty_hourly_activity()},
        }
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": "2026-06-09",
            "view": "activity-day",
            "snapshotVersion": version,
            "payload": {"totals": {"activeSeconds": 999}, "authors": [], "hourlyActivityByAuthor": []},
        }
    )

    snapshot_date, snapshot_doc = repo.activity_day_summary_snapshot_for_request(
        view="activity",
        start_date="2026-06-09",
        end_date="2026-06-09",
        date_mode=None,
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
        now=dt.datetime(2026, 6, 9, 23, 28, tzinfo=dt.UTC),
    )

    assert snapshot_date == "2026-06-09"
    assert snapshot_doc is not None

def test_activity_day_summary_snapshot_is_skipped_when_any_author_local_day_is_live():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "Europe/Madrid"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Vancouver Author", "displayName": "Vancouver Author", "timeZoneId": "America/Vancouver"})
    version = repo.activity_day_summary_snapshot_version()
    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": "2026-06-09",
            "view": "activity-day",
            "snapshotVersion": version,
            "payload": {"totals": {"activeSeconds": 999}, "authors": [], "hourlyActivityByAuthor": []},
        }
    )

    snapshot_date, snapshot_doc = repo.activity_day_summary_snapshot_for_request(
        view="activity",
        start_date="2026-06-09",
        end_date="2026-06-09",
        date_mode=None,
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
        now=dt.datetime(2026, 6, 9, 23, 28, tzinfo=dt.UTC),
    )

    assert snapshot_date is None
    assert snapshot_doc is None

def test_activity_day_summary_snapshot_invalidation_removes_selected_dates():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    repo.db.activity_day_summary_snapshots.insert_one({"date": "2026-04-28", "view": "activity-day", "snapshotVersion": version})
    repo.db.activity_day_summary_snapshots.insert_one({"date": "2026-04-29", "view": "activity-day", "snapshotVersion": version})
    repo.db.activity_author_day_summary_snapshots.insert_one({"date": "2026-04-29", "rawAuthor": "Future Artist", "snapshotVersion": version})

    repo.invalidate_activity_summary_cache(["2026-04-29"])

    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-04-28"}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-04-29"}) is None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": "2026-04-29"}) is None

def test_activity_author_day_snapshot_materializes_one_author_per_call():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Alpha Artist", "displayName": "Alpha Artist", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Beta Artist", "displayName": "Beta Artist", "timeZoneId": "UTC"})

    for author, seconds in (("Beta Artist", 240), ("Alpha Artist", 120)):
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": seconds,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    first = repo.materialize_next_completed_author_day_snapshot(now=now)
    second = repo.materialize_next_completed_author_day_snapshot(now=now)
    third = repo.materialize_next_completed_author_day_snapshot(now=now)

    assert first["processed"] is True
    assert first["rawAuthor"] == "Alpha Artist"
    assert first["composed"] is False
    assert second["processed"] is True
    assert second["rawAuthor"] == "Beta Artist"
    assert second["composed"] is True
    assert third["processed"] is False
    assert repo.db.activity_author_day_summary_snapshots.count_documents({"date": "2026-04-29"}) == 2
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-04-29"}) is not None

def test_activity_author_day_snapshot_candidates_include_active_devices_and_publishers():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Alpha Artist", "displayName": "Alpha Artist", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Publisher QA", "displayName": "Publisher QA", "profileType": "publisher"})

    for author in ("Alpha Artist", "Device5", "Publisher QA"):
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    first = repo.materialize_next_completed_author_day_snapshot(now=now)
    second = repo.materialize_next_completed_author_day_snapshot(now=now)
    third = repo.materialize_next_completed_author_day_snapshot(now=now)
    fourth = repo.materialize_next_completed_author_day_snapshot(now=now)
    status = repo.activity_snapshot_materialization_status(now=now)

    assert first["processed"] is True
    assert first["rawAuthor"] == "Alpha Artist"
    assert second["processed"] is True
    assert second["rawAuthor"] == "Device5"
    assert third["processed"] is True
    assert third["rawAuthor"] == "Publisher QA"
    assert third["composed"] is True
    assert fourth["processed"] is False
    assert repo.db.activity_author_day_summary_snapshots.count_documents({"date": "2026-04-29"}) == 3
    assert [row["rawAuthor"] for row in status["rows"]] == ["Alpha Artist", "Device5", "Publisher QA"]

def test_historical_activity_summary_adds_zero_rows_for_inactive_people_on_selected_day():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Alpha Artist", "displayName": "Alpha Artist", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Beta Artist", "displayName": "Beta Artist", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Inactive Publisher", "displayName": "Inactive Publisher", "profileType": "publisher"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Publisher QA", "displayName": "Publisher QA", "profileType": "publisher"})

    for author in ("Alpha Artist", "Device5", "Publisher QA"):
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", date_mode=None, now=now)

    assert [item["rawAuthor"] for item in summary["authors"]] == ["Alpha Artist", "Beta Artist", "Device5", "Publisher QA"]
    assert [item["rawAuthor"] for item in summary["hourlyActivityByAuthor"]] == ["Alpha Artist", "Beta Artist", "Device5", "Publisher QA"]
    beta = next(item for item in summary["authors"] if item["rawAuthor"] == "Beta Artist")
    assert beta["activeSeconds"] == 0
    assert beta["idleSeconds"] == 0
    assert beta["productivity"] == 0
    assert "Inactive Publisher" not in {item["rawAuthor"] for item in summary["authors"]}


def test_author_local_today_hides_inactive_publisher_after_twenty_four_hours():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 15, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Ketchapp", "displayName": "Ketchapp", "profileType": "publisher"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device8", "targetRawAuthor": "Ketchapp"})
    repo.db.report_rows.insert_one(
        {
            "source": "dev-android",
            "author": "Device8",
            "date": "2026-05-13",
            "recordedAt": "2026-05-13T18:59:53+02:00",
            "receivedAt": dt.datetime(2026, 5, 13, 16, 59, 53, tzinfo=dt.UTC),
            "activeDeltaSeconds": 1,
            "idleDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=now)

    assert "Ketchapp" not in {item["rawAuthor"] for item in summary["authors"]}
    assert "Ketchapp" not in {item["rawAuthor"] for item in summary["hourlyActivityByAuthor"]}


def test_author_local_today_keeps_recent_stale_publisher_with_last_seen():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 15, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Ketchapp", "displayName": "Ketchapp", "profileType": "publisher"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device8", "targetRawAuthor": "Ketchapp"})
    repo.db.report_rows.insert_one(
        {
            "source": "dev-android",
            "pluginVersion": "0.1.7",
            "author": "Device8",
            "date": "2026-05-14",
            "recordedAt": "2026-05-14T15:30:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 14, 13, 30, tzinfo=dt.UTC),
            "activeDeltaSeconds": 1,
            "idleDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=now)
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Ketchapp")

    assert author["status"] == "stale"
    assert author["stalePresence"] == "device"
    assert author["lastReceivedAt"] == "2026-05-14T13:30:00+00:00"
    assert author["lastRecordedAt"] == "2026-05-14T15:30:00+02:00"


def test_author_local_today_aggregates_publisher_device_activity_today():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 15, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Ketchapp", "displayName": "Ketchapp", "profileType": "publisher"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device8", "targetRawAuthor": "Ketchapp"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev-android",
            "author": "Device8",
            "projectId": "Bike Rush 2",
            "date": "2026-05-15",
            "timeZoneId": "UTC",
            "lastRecordedAt": "2026-05-15T11:59:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 5, 15, 11, 59, tzinfo=dt.UTC),
            "activeSeconds": 120,
            "idleSeconds": 30,
            "activityCounts": [{"type": "click", "count": 2}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(date_mode="authorLocalToday", now=now)
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Ketchapp")

    assert author["activeSeconds"] == 120
    assert author["rawPluginDaySeconds"] == 150
    assert author["activityMix"] == [{"type": "click", "count": 2, "percent": 100}]


def test_activity_author_day_snapshot_skips_live_local_day():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "Live Artist", "displayName": "Live Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Live Artist",
            "projectId": "al",
            "date": "2026-05-02",
            "timeZoneId": "UTC",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    result = repo.materialize_next_completed_author_day_snapshot(now=now)

    assert result["processed"] is False
    assert repo.db.activity_author_day_summary_snapshots.count_documents({}) == 0

def test_day_summary_snapshot_waits_for_live_author_after_completed_authors_are_ready():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    day_date = "2026-05-02"
    early_now = dt.datetime(2026, 5, 3, 1, tzinfo=dt.UTC)
    late_now = dt.datetime(2026, 5, 3, 8, 1, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "UTC Artist", "displayName": "UTC Artist", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Vancouver Artist", "displayName": "Vancouver Artist", "timeZoneId": "America/Vancouver"}
    )

    for author, time_zone_id in (("UTC Artist", "UTC"), ("Vancouver Artist", "America/Vancouver")):
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": day_date,
                "timeZoneId": time_zone_id,
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    first = repo.materialize_activity_author_day_summary_snapshots(limit=10, now=early_now)
    early_status = repo.activity_snapshot_materialization_status(now=early_now)
    rows = {(row["date"], row["rawAuthor"]): row for row in early_status["rows"]}

    assert [item["rawAuthor"] for item in first["processed"]] == ["UTC Artist"]
    assert first["remaining"] is False
    assert repo.db.activity_author_day_summary_snapshots.count_documents({"date": day_date}) == 1
    assert repo.db.activity_author_day_summary_snapshots.find_one(
        {"date": day_date, "rawAuthor": "UTC Artist", "snapshotVersion": version}
    ) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": day_date, "snapshotVersion": version}) is None
    assert rows[(day_date, "UTC Artist")]["status"] == "ready"
    assert rows[(day_date, "Vancouver Artist")]["status"] == "live"
    assert rows[(day_date, "UTC Artist")]["daySnapshotReady"] is False
    assert rows[(day_date, "Vancouver Artist")]["daySnapshotReady"] is False
    assert repo.activity_day_summary_snapshot_status(day_date, now=early_now) == {
        "readyAuthors": ["UTC Artist"],
        "pendingAuthors": [],
        "liveAuthors": ["Vancouver Artist"],
    }

    second = repo.materialize_activity_author_day_summary_snapshots(limit=10, now=late_now)
    late_status = repo.activity_snapshot_materialization_status(now=late_now)
    late_rows = {(row["date"], row["rawAuthor"]): row for row in late_status["rows"]}

    assert [item["rawAuthor"] for item in second["processed"]] == ["Vancouver Artist"]
    assert second["processed"][0]["composed"] is True
    assert repo.db.activity_author_day_summary_snapshots.count_documents({"date": day_date}) == 2
    assert repo.db.activity_day_summary_snapshots.find_one({"date": day_date, "snapshotVersion": version}) is not None
    assert late_rows[(day_date, "UTC Artist")]["status"] == "ready"
    assert late_rows[(day_date, "Vancouver Artist")]["status"] == "ready"
    assert late_rows[(day_date, "UTC Artist")]["daySnapshotReady"] is True
    assert late_rows[(day_date, "Vancouver Artist")]["daySnapshotReady"] is True
    assert repo.activity_day_summary_snapshot_status(day_date, now=late_now) == {
        "readyAuthors": ["UTC Artist", "Vancouver Artist"],
        "pendingAuthors": [],
        "liveAuthors": [],
    }

def test_activity_author_day_payload_handles_missing_hourly_row():
    repo = fake_repository()

    payload = repo._author_day_payload_from_summary(
        {
            "authors": [{"rawAuthor": "No Hourly Artist", "displayName": "No Hourly Artist", "activeSeconds": 60}],
            "hourlyActivityByAuthor": [],
        },
        "No Hourly Artist",
    )

    assert payload["hourlyActivity"]["rawAuthor"] == "No Hourly Artist"
    assert payload["hourlyActivity"]["hourlyActivity"] == empty_hourly_activity()

def test_activity_author_day_snapshot_maintenance_pass_is_sequential_and_limited():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)

    for author in ("Alpha Artist", "Beta Artist", "Gamma Artist"):
        repo.db.author_profiles.insert_one({"rawAuthor": author, "displayName": author, "timeZoneId": "UTC"})
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    result = repo.materialize_activity_author_day_summary_snapshots(limit=2, now=now)

    assert [item["rawAuthor"] for item in result["processed"]] == ["Alpha Artist", "Beta Artist"]
    assert result["remaining"] is True
    assert repo.db.activity_author_day_summary_snapshots.count_documents({"date": "2026-04-29"}) == 2
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-04-29"}) is None

def test_activity_snapshot_materialization_status_reports_author_progress():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    version = repo.activity_day_summary_snapshot_version()

    for author in ("Alpha Artist", "Beta Artist"):
        repo.db.author_profiles.insert_one({"rawAuthor": author, "displayName": author, "timeZoneId": "UTC"})
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Alpha Artist",
            "projectId": "al",
            "date": "2026-05-02",
            "timeZoneId": "UTC",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.activity_author_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "rawAuthor": "Alpha Artist", "snapshotVersion": version, "builtAt": now}
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "view": "activity-day", "snapshotVersion": version}
    )

    status = repo.activity_snapshot_materialization_status(now=now)
    rows = {(row["date"], row["rawAuthor"]): row for row in status["rows"]}

    assert rows[("2026-04-29", "Alpha Artist")]["status"] == "ready"
    assert rows[("2026-04-29", "Alpha Artist")]["daySnapshotReady"] is True
    assert rows[("2026-04-29", "Beta Artist")]["status"] == "next"
    assert rows[("2026-05-02", "Alpha Artist")]["status"] == "live"
    assert status["totals"]["ready"] == 1
    assert status["totals"]["next"] == 1
    assert status["totals"]["live"] == 1

def test_activity_snapshot_materialization_status_reports_processing_separately_from_next():
    repo = fake_repository()
    now = dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC)
    version = repo.activity_day_summary_snapshot_version()

    for author in ("Alpha Artist", "Beta Artist"):
        repo.db.author_profiles.insert_one({"rawAuthor": author, "displayName": author, "timeZoneId": "UTC"})
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-04-29",
                "activeSeconds": 120,
                "idleSeconds": 0,
                "activityCounts": [],
                "savedPrefabs": [],
                "overtimeActivityCounts": [],
                "overtimeSavedPrefabs": [],
                "hourlyActivity": empty_hourly_activity(),
            }
        )

    repo.db.activity_snapshot_maintenance_state.insert_one(
        {
            "kind": "author-day",
            "date": "2026-04-29",
            "rawAuthor": "Alpha Artist",
            "snapshotVersion": version,
            "startedAt": now,
        }
    )

    status = repo.activity_snapshot_materialization_status(now=now)
    rows = {(row["date"], row["rawAuthor"]): row for row in status["rows"]}

    assert rows[("2026-04-29", "Alpha Artist")]["status"] == "processing"
    assert rows[("2026-04-29", "Beta Artist")]["status"] == "next"
    assert status["processing"] == {"date": "2026-04-29", "rawAuthor": "Alpha Artist", "startedAt": "2026-05-02T12:00:00+00:00"}
    assert status["next"] == {"date": "2026-04-29", "rawAuthor": "Beta Artist"}
    assert status["totals"]["processing"] == 1
    assert status["totals"]["next"] == 1

def test_activity_snapshot_remake_range_deletes_selected_dates_and_processing_state():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    built_at = dt.datetime(2026, 5, 14, tzinfo=dt.UTC)

    for day in ("2026-05-10", "2026-05-11", "2026-05-12"):
        repo.db.activity_author_day_summary_snapshots.insert_one(
            {"date": day, "rawAuthor": "Snapshot Artist", "snapshotVersion": version, "builtAt": built_at}
        )
        repo.db.activity_day_summary_snapshots.insert_one(
            {"date": day, "view": "activity-day", "snapshotVersion": version, "payload": {}, "builtAt": built_at}
        )

    repo.db.activity_snapshot_maintenance_state.insert_one(
        {
            "kind": "author-day",
            "date": "2026-05-11",
            "rawAuthor": "Snapshot Artist",
            "snapshotVersion": version,
            "startedAt": built_at,
        }
    )

    result = repo.remake_activity_day_summary_snapshots_for_range("2026-05-11", "2026-05-12")

    assert result == {"ok": True, "dates": ["2026-05-11", "2026-05-12"], "deletedDates": 2}
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": "2026-05-10"}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-10"}) is not None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": "2026-05-11"}) is None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-12"}) is None
    assert repo.db.activity_snapshot_maintenance_state.find_one({"date": "2026-05-11"}) is None

def test_activity_snapshot_store_removes_old_day_versions_for_same_date():
    repo = fake_repository()
    current_version = repo.activity_day_summary_snapshot_version()
    old_version = current_version - 1

    repo.db.activity_day_summary_snapshots.insert_one(
        {"date": "2026-05-11", "view": "activity-day", "snapshotVersion": old_version, "payload": {"authors": []}}
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {"date": "2026-05-10", "view": "activity-day", "snapshotVersion": old_version, "payload": {"authors": []}}
    )

    repo.store_activity_day_summary_snapshot("2026-05-11", {"authors": [{"rawAuthor": "Alpha Artist"}]})

    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-11", "snapshotVersion": old_version}) is None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-11", "snapshotVersion": current_version}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-10", "snapshotVersion": old_version}) is not None

def test_activity_snapshot_cleanup_old_versions_removes_stale_snapshot_documents():
    repo = fake_repository()
    current_version = repo.activity_day_summary_snapshot_version()
    old_version = current_version - 1

    repo.db.activity_day_summary_snapshots.insert_one({"date": "2026-05-11", "view": "activity-day", "snapshotVersion": old_version})
    repo.db.activity_day_summary_snapshots.insert_one({"date": "2026-05-12", "view": "activity-day", "snapshotVersion": current_version})
    repo.db.activity_author_day_summary_snapshots.insert_one({"date": "2026-05-11", "rawAuthor": "Alpha", "snapshotVersion": old_version})
    repo.db.activity_author_day_summary_snapshots.insert_one({"date": "2026-05-12", "rawAuthor": "Alpha", "snapshotVersion": current_version})
    repo.db.activity_snapshot_maintenance_state.insert_one({"kind": "author-day", "date": "2026-05-11", "snapshotVersion": old_version})

    result = repo.cleanup_old_activity_day_summary_snapshot_versions()

    assert result["deletedDaySnapshots"] == 1
    assert result["deletedAuthorDaySnapshots"] == 1
    assert repo.db.activity_day_summary_snapshots.find_one({"snapshotVersion": old_version}) is None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"snapshotVersion": old_version}) is None
    assert repo.db.activity_snapshot_maintenance_state.find_one({"snapshotVersion": old_version}) is None
    assert repo.db.activity_day_summary_snapshots.find_one({"snapshotVersion": current_version}) is not None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"snapshotVersion": current_version}) is not None

def test_report_ingest_invalidates_only_affected_day_snapshots():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()
    old_day = "2026-04-29"
    changed_day = "2026-05-14"

    for day in (old_day, changed_day):
        repo.db.activity_author_day_summary_snapshots.insert_one(
            {"date": day, "rawAuthor": "Snapshot Artist", "snapshotVersion": version, "builtAt": dt.datetime(2026, 5, 14, tzinfo=dt.UTC)}
        )
        repo.db.activity_day_summary_snapshots.insert_one(
            {"date": day, "view": "activity-day", "snapshotVersion": version, "payload": {}, "builtAt": dt.datetime(2026, 5, 14, tzinfo=dt.UTC)}
        )

    repo.save_report(
        "ual",
        "1.0.0",
        "packet",
        {
            "author": "Snapshot Artist",
            "projectId": "al",
            "sessionId": "snapshot-session",
            "date": changed_day,
            "activeSeconds": 60,
            "idleSeconds": 0,
            "recordedAt": "2026-05-14T12:00:00+00:00",
            "timeZoneId": "UTC",
        },
        "challenge",
    )

    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": old_day}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": old_day}) is not None
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": changed_day}) is None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": changed_day}) is None

def test_empty_date_invalidation_does_not_clear_snapshots():
    repo = fake_repository()
    version = repo.activity_day_summary_snapshot_version()

    repo.db.activity_author_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "rawAuthor": "Snapshot Artist", "snapshotVersion": version}
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "view": "activity-day", "snapshotVersion": version, "payload": {}}
    )

    repo.invalidate_activity_summary_cache([])

    assert repo.db.activity_author_day_summary_snapshots.count_documents({}) == 1
    assert repo.db.activity_day_summary_snapshots.count_documents({}) == 1

def test_activity_day_summary_snapshot_version_mismatch_returns_preparing():
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
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {
            "date": "2026-04-29",
            "view": "activity-day",
            "snapshotVersion": repo.activity_day_summary_snapshot_version() - 1,
            "payload": {"totals": {"activeSeconds": 999}, "authors": [], "hourlyActivityByAuthor": []},
        }
    )

    summary = repo.cached_activity_summary(
        view="activity",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=True,
    )

    assert summary["snapshot"] == {
        "hit": False,
        "status": "preparing",
        "date": "2026-04-29",
        "partial": True,
        "readyAuthors": [],
        "pendingAuthors": ["Future Artist"],
        "preparingAuthors": ["Future Artist"],
        "liveAuthors": [],
    }
    assert summary["totals"]["activeSeconds"] == 0
    assert repo.db.activity_day_summary_snapshots.find_one(
        {"date": "2026-04-29", "snapshotVersion": repo.activity_day_summary_snapshot_version()}
    ) is None

def test_full_activity_rebuild_removes_day_summary_snapshots():
    repo = fake_repository()
    repo.db.activity_author_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "rawAuthor": "Future Artist", "snapshotVersion": repo.activity_day_summary_snapshot_version()}
    )
    repo.db.activity_day_summary_snapshots.insert_one(
        {"date": "2026-04-29", "view": "activity-day", "snapshotVersion": repo.activity_day_summary_snapshot_version()}
    )

    repo.rebuild_aggregates_if_needed(force=True)

    assert repo.db.activity_author_day_summary_snapshots.count_documents({}) == 0
    assert repo.db.activity_day_summary_snapshots.count_documents({}) == 0

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

    repo.record_break_event("dmitryshane", "online", "2026-04-29T07:06:17+02:00")

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

def test_activity_summary_regular_single_day_keeps_only_authors_active_on_selected_day():
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
    assert "Utc Author" not in authors

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
