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
    INTERNAL_MISSED_END_SECONDS,
    add_break_interval_to_buckets,
    add_meeting_interval_to_buckets,
    add_visual_missed_seconds,
    apply_overtime_start_boundary,
    apply_workday_idle_fill,
    apply_breaks_to_hourly_activity,
    empty_hourly_activity,
    hourly_activity_has_workday_signal,
    public_hour,
    public_hourly_activity,
    transfer_summary_idle_to_auto_break,
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



def _hour_metric(item: dict, key: str) -> int:
    if "totals" not in item:
        return int(item.get(key, 0))
    if key == "overtimeActiveSeconds":
        return sum(
            segment["endSecond"] - segment["startSecond"]
            for segment in item.get("fillSegments", [])
            if segment.get("kind") == "overtime"
        )
    mapping = {
        "activeSeconds": "activeSeconds",
        "idleSeconds": "idleSeconds",
        "breakSeconds": "afkSeconds",
        "meetingSeconds": "meetingSeconds",
        "missedSeconds": "missedSeconds",
        "telegramToFirstActivityIdleSeconds": "idleSeconds",
    }
    return int(item["totals"].get(mapping[key], 0))


def _hour_segments(item: dict, kind: str) -> list[dict[str, int]]:
    return [
        {"startSecond": segment["startSecond"], "endSecond": segment["endSecond"]}
        for segment in item.get("fillSegments", [])
        if segment.get("kind") == kind
    ]


def _missed_start_seconds(item: dict) -> int:
    return sum(segment["endSecond"] - segment["startSecond"] for segment in item.get("fillSegments", []) if segment.get("kind") == "missed" and segment.get("startSecond") == 0)


def _missed_end_seconds(item: dict) -> int:
    return sum(segment["endSecond"] - segment["startSecond"] for segment in item.get("fillSegments", []) if segment.get("kind") == "missed" and segment.get("endSecond") == 3600)


def _overtime_fill_seconds(item: dict) -> int:
    if "fillSegments" in item:
        return sum(
            segment["endSecond"] - segment["startSecond"]
            for segment in item.get("fillSegments", [])
            if segment.get("kind") == "overtime-fill"
        )
    return int(item.get("_visualOvertimeSeconds", 0))


def _public_empty_hourly_activity() -> list[dict]:
    return [{"hour": hour, "totals": {"activeSeconds": 0, "overtimeSeconds": 0, "afkSeconds": 0, "meetingSeconds": 0, "idleSeconds": 0, "missedSeconds": 0}, "fillSegments": []} for hour in range(24)]

def test_activity_summary_fills_hourly_idle_from_telegram_online_to_first_raw_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "telegramUsername": "dmitryshane",
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.record_break_event("dmitryshane", "online", "2026-05-05T09:02:59Z")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "projectId": "unity",
            "date": "2026-05-05",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "author": "Dmitry Shane",
            "date": "2026-05-05",
            "source": "cur",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 5, 5, 1, 55, 29, tzinfo=dt.UTC),
            "receivedAt": dt.datetime(2026, 5, 5, 1, 56, 1, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "author": "Dmitry Shane",
            "date": "2026-05-05",
            "source": "ual",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 5, 5, 10, 31, 7, tzinfo=dt.UTC),
            "receivedAt": dt.datetime(2026, 5, 5, 10, 54, 32, tzinfo=dt.UTC),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "date": "2026-05-05",
            "recordedAt": "2026-05-05T10:54:32Z",
            "receivedAt": dt.datetime(2026, 5, 5, 10, 54, 32, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-05", end_date="2026-05-05")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Dmitry Shane")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 88 * 60 + 8
    assert _hour_metric(hourly_by_hour[11], "idleSeconds") == 57 * 60 + 1
    assert _missed_start_seconds(hourly_by_hour[11]) == 2 * 60 + 59
    assert _hour_metric(hourly_by_hour[12], "idleSeconds") == 31 * 60 + 7
    assert _hour_segments(hourly_by_hour[12], "telegram-idle")[0] == {"startSecond": 0, "endSecond": 31 * 60 + 7}

def test_activity_summary_marks_visual_missed_time_before_online_hour_without_affecting_totals():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:17:30Z")
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[9], "missedSeconds") == 17 * 60 + 30
    assert _missed_start_seconds(hourly_by_hour[9]) == 17 * 60 + 30
    assert _missed_end_seconds(hourly_by_hour[9]) == 0
    assert _hour_metric(hourly_by_hour[9], "idleSeconds") == 0
    assert _hour_metric(author, "idleSeconds") == 0
    assert author["pluginDaySeconds"] == 60

def test_activity_summary_marks_visual_missed_time_after_offline_hour_without_affecting_totals():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T19:20:00Z")
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[19], "missedSeconds") == 40 * 60
    assert _missed_start_seconds(hourly_by_hour[19]) == 0
    assert _missed_end_seconds(hourly_by_hour[19]) == 40 * 60
    assert _hour_metric(hourly_by_hour[19], "idleSeconds") == 0
    assert _hour_metric(author, "idleSeconds") == 0
    assert author["pluginDaySeconds"] == 60

def test_activity_summary_current_plugin_hour_gap_is_not_filled():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 60
    hourly_activity[10]["activeMicroseconds"] = 60 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "projectId": "AL",
            "date": "2026-05-03",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "date": "2026-05-03",
            "recordedAt": "2026-05-03T10:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 3, 10, 2, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-03", end_date="2026-05-03")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Dmitry Shane")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Dmitry Shane")["hourlyActivity"]
    hour_10 = next(item for item in hourly if item["hour"] == 10)

    assert _hour_metric(hour_10, "idleSeconds") == 0
    assert _hour_metric(author, "idleSeconds") == 0
    assert author["pluginDaySeconds"] == 60
    assert _hour_metric(summary["totals"], "idleSeconds") == 0
    assert summary["totals"]["pluginDaySeconds"] == 60

def test_activity_summary_previous_plugin_hour_gap_is_visual_only_after_next_hour_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 60
    hourly_activity[10]["activeMicroseconds"] = 60 * 1_000_000
    hourly_activity[11]["activeSeconds"] = 30
    hourly_activity[11]["activeMicroseconds"] = 30 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "projectId": "AL",
            "date": "2026-05-03",
            "activeSeconds": 90,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "date": "2026-05-03",
            "recordedAt": "2026-05-03T11:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 3, 11, 2, tzinfo=dt.UTC),
            "activeDeltaSeconds": 30,
            "idleDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-03", end_date="2026-05-03")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Dmitry Shane")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Dmitry Shane")["hourlyActivity"]
    hour_10 = next(item for item in hourly if item["hour"] == 10)
    hour_11 = next(item for item in hourly if item["hour"] == 11)

    assert _hour_metric(hour_10, "idleSeconds") == 3540
    assert _hour_metric(hour_10, "activeSeconds") + _hour_metric(hour_10, "idleSeconds") == 3600
    assert _hour_metric(hour_11, "idleSeconds") == 0
    assert _hour_metric(author, "idleSeconds") == 0
    assert author["pluginDaySeconds"] == 90
    assert _hour_metric(summary["totals"], "idleSeconds") == 0
    assert summary["totals"]["pluginDaySeconds"] == 90

def test_activity_summary_previous_plugin_hour_gap_is_not_limited_by_day_budget():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "UTC"})
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 60
    hourly_activity[10]["activeMicroseconds"] = 60 * 1_000_000
    hourly_activity[11]["activeSeconds"] = 30
    hourly_activity[11]["activeMicroseconds"] = 30 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "projectId": "AL",
            "date": "2026-05-03",
            "activeSeconds": 9 * 3600,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "date": "2026-05-03",
            "recordedAt": "2026-05-03T11:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 3, 11, 2, tzinfo=dt.UTC),
            "activeDeltaSeconds": 30,
            "idleDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-03", end_date="2026-05-03")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Dmitry Shane")["hourlyActivity"]
    hour_10 = next(item for item in hourly if item["hour"] == 10)

    assert _hour_metric(hour_10, "idleSeconds") == 3540
    assert _hour_metric(hour_10, "activeSeconds") + _hour_metric(hour_10, "idleSeconds") == 3600

def test_activity_summary_marks_visual_missed_time_after_latest_plugin_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T19:20:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T21:37:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 21, 37, 5, tzinfo=dt.UTC),
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _missed_end_seconds(hourly_by_hour[19]) == 0
    assert _hour_metric(hourly_by_hour[21], "missedSeconds") == 23 * 60
    assert _missed_end_seconds(hourly_by_hour[21]) == 23 * 60
    assert author["telegramToFirstActivitySeconds"] == 12 * 3600 + 37 * 60

def test_activity_summary_uses_offline_only_as_visual_missed_end_trigger():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T21:30:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T20:10:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 20, 10, 5, tzinfo=dt.UTC),
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _missed_end_seconds(hourly_by_hour[20]) == 50 * 60
    assert _missed_end_seconds(hourly_by_hour[21]) == 0

def test_activity_summary_visual_missed_end_fills_last_report_hour_to_sixty_minutes():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "date": "2026-05-01",
            "startedAt": dt.datetime(2026, 5, 1, 15, 0, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 1, 22, 30, tzinfo=dt.UTC),
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.break_events.insert_one(
        {
            "telegramUsername": "igormats",
            "rawAuthor": "Igor Mats",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 5, 9, 1, 17, 33, tzinfo=dt.UTC),
            "date": "2026-05-08",
            "timeZoneId": "America/Vancouver",
        }
    )
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
    hourly_activity = empty_hourly_activity()
    hourly_activity[14]["overtimeActiveSeconds"] = 21 * 60 + 31
    hourly_activity[14]["overtimeActiveMicroseconds"] = (21 * 60 + 31) * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "AL",
            "date": "2026-05-01",
            "activeSeconds": 32400,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 21 * 60 + 31,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Igor Mats")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[14], "overtimeActiveSeconds") == 21 * 60 + 31
    assert _overtime_fill_seconds(hourly_by_hour[14]) == 0
    assert _missed_end_seconds(hourly_by_hour[14]) == 3600 - (21 * 60 + 31)

def test_activity_summary_visual_missed_end_does_not_override_real_overtime_segments():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "America/Vancouver"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "date": "2026-05-08",
            "startedAt": dt.datetime(2026, 5, 8, 15, 16, 19, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 9, 1, 17, 33, tzinfo=dt.UTC),
            "reminderAction": "overtime",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-08",
            "recordedAt": "2026-05-08T23:59:18.717-07:00",
            "receivedAt": dt.datetime(2026, 5, 9, 7, 4, 32, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 3,
        }
    )
    overtime_segments = [
        {"kind": "overtime", "startSecond": 684, "endSecond": 1658},
        {"kind": "overtime", "startSecond": 1819, "endSecond": 2739},
        {"kind": "overtime", "startSecond": 3540, "endSecond": 3543},
    ]
    overtime_seconds = sum(segment["endSecond"] - segment["startSecond"] for segment in overtime_segments)
    hourly_activity = empty_hourly_activity()
    hourly_activity[23]["overtimeActiveSeconds"] = overtime_seconds
    hourly_activity[23]["overtimeActiveMicroseconds"] = overtime_seconds * 1_000_000
    hourly_activity[23]["fillSegments"].extend(overtime_segments)
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "unity-bike-rush-2",
            "date": "2026-05-08",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": overtime_seconds,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-08",
            "recordedAt": "2026-05-08T18:34:29-07:00",
            "receivedAt": dt.datetime(2026, 5, 9, 1, 34, 29, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 87,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-08", end_date="2026-05-08")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Igor Mats")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[23], "overtimeActiveSeconds") == overtime_seconds

def test_overtime_boundary_hour_shows_idle_only_before_overtime_start():
    hourly_activity = empty_hourly_activity()
    hourly_activity[18]["idleSeconds"] = 809
    hourly_activity[18]["idleMicroseconds"] = 809_000_000
    hourly_activity[18]["overtimeActiveSeconds"] = 868
    hourly_activity[18]["overtimeActiveMicroseconds"] = 868_000_000
    hourly_activity[18]["fillSegments"] = [
        {"kind": "idle", "startSecond": 0, "endSecond": 809},
        {"kind": "overtime", "startSecond": 1412, "endSecond": 1799},
        {"kind": "overtime", "startSecond": 2578, "endSecond": 2827},
        {"kind": "overtime", "startSecond": 2827, "endSecond": 2887},
        {"kind": "overtime", "startSecond": 2942, "endSecond": 3002},
        {"kind": "overtime", "startSecond": 3488, "endSecond": 3600},
    ]

    apply_overtime_start_boundary(
        hourly_activity,
        dt.datetime(2026, 5, 9, 1, 17, 33, tzinfo=dt.UTC),
        "America/Vancouver",
    )
    hour_18 = public_hour(hourly_activity[18])

    assert _hour_metric(hour_18, "idleSeconds") == 17 * 60 + 33
    assert all(segment["endSecond"] <= 17 * 60 + 33 for segment in _hour_segments(hour_18, "idle"))
    assert _hour_segments(hour_18, "overtime")[0]["startSecond"] == 17 * 60 + 33
    assert _hour_segments(hour_18, "overtime-fill")[-1]["endSecond"] == 3600

def test_overtime_heartbeat_idle_after_boundary_is_ignored():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "date": "2026-05-08",
            "startedAt": dt.datetime(2026, 5, 8, 15, 16, 19, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 9, 1, 17, 33, tzinfo=dt.UTC),
            "reminderAction": "overtime",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.break_events.insert_one(
        {
            "telegramUsername": "igormats",
            "rawAuthor": "Igor Mats",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 5, 9, 1, 17, 33, tzinfo=dt.UTC),
            "date": "2026-05-08",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo._apply_raw_event_to_aggregates(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "unity-bike-rush-2",
            "sessionId": "vsc-session",
            "date": "2026-05-08",
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T01:20:00Z",
            "occurredAtLocal": "2026-05-08T18:20:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 9, 1, 20, tzinfo=dt.UTC),
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "unity-bike-rush-2",
            "sessionId": "vsc-session",
            "date": "2026-05-08",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-09T01:40:00Z",
            "occurredAtLocal": "2026-05-08T18:40:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 9, 1, 40, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaSeconds"] == 0
    report_rows = list(repo.db.report_rows.find({"author": "Igor Mats", "source": "vsc"}))
    assert all(row.get("idleDeltaSeconds", 0) == 0 for row in report_rows)
    assert all(row.get("overtimeActiveDeltaSeconds", 0) == 0 for row in report_rows)

def test_night_overtime_active_counts_on_same_calendar_day():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Night Worker", "displayName": "Night Worker", "timeZoneId": "UTC"})
    base_event = {
        "source": "vsc",
        "author": "Night Worker",
        "projectId": "night-project",
        "sessionId": "night-session",
        "date": "2026-05-09",
        "timeZoneId": "UTC",
        "receivedAt": dt.datetime(2026, 5, 9, 3, 0, tzinfo=dt.UTC),
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T03:00:00Z",
            "occurredAtLocal": "2026-05-09T03:00:00+00:00",
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "file_saved",
            "occurredAtUtc": "2026-05-09T03:02:00Z",
            "occurredAtLocal": "2026-05-09T03:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 3, 2, tzinfo=dt.UTC),
            "metadata": {"path": "Assets/Night.cs", "name": "Night.cs"},
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Night Worker", "date": "2026-05-09", "source": "vsc"})
    hour_3 = public_hour(daily["hourlyActivity"][3])

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaSeconds"] == 120
    assert deltas["overtimeActivityCountDeltas"] == [{"type": "file_saved", "count": 1}]
    assert deltas["overtimeSavedPrefabDeltas"] == [{"path": "Assets/Night.cs", "name": "Night.cs", "saveCount": 1}]
    assert hour_3["totals"]["overtimeSeconds"] == 120
    assert hour_3["totals"]["idleSeconds"] == 0

def test_night_overtime_heartbeat_idle_is_ignored():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Night Worker", "displayName": "Night Worker", "timeZoneId": "UTC"})
    base_event = {
        "source": "vsc",
        "author": "Night Worker",
        "projectId": "night-project",
        "sessionId": "night-session",
        "date": "2026-05-09",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T03:00:00Z",
            "occurredAtLocal": "2026-05-09T03:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 3, 0, tzinfo=dt.UTC),
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-09T03:10:00Z",
            "occurredAtLocal": "2026-05-09T03:10:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 3, 10, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaSeconds"] == 0
    assert list(repo.db.report_rows.find({"author": "Night Worker", "date": "2026-05-09"})) == []

def test_night_overtime_interval_splits_at_seven_am():
    repo = fake_repository()
    set_idle_threshold(repo, 3600)
    repo.db.author_profiles.insert_one({"rawAuthor": "Night Worker", "displayName": "Night Worker", "timeZoneId": "UTC"})
    base_event = {
        "source": "vsc",
        "author": "Night Worker",
        "projectId": "night-project",
        "sessionId": "night-session",
        "date": "2026-05-09",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T06:50:00Z",
            "occurredAtLocal": "2026-05-09T06:50:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 6, 50, tzinfo=dt.UTC),
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T07:10:00Z",
            "occurredAtLocal": "2026-05-09T07:10:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 7, 10, tzinfo=dt.UTC),
        }
    )

    assert deltas["overtimeActiveDeltaSeconds"] == 10 * 60
    assert deltas["activeDeltaSeconds"] == 10 * 60

def test_morning_work_after_night_overtime_is_normal_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Night Worker", "displayName": "Night Worker", "timeZoneId": "UTC"})
    base_event = {
        "source": "vsc",
        "author": "Night Worker",
        "projectId": "night-project",
        "sessionId": "morning-session",
        "date": "2026-05-09",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T10:00:00Z",
            "occurredAtLocal": "2026-05-09T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 10, 0, tzinfo=dt.UTC),
        }
    )

    deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-05-09T10:02:00Z",
            "occurredAtLocal": "2026-05-09T10:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 10, 2, tzinfo=dt.UTC),
        }
    )

    assert deltas["activeDeltaSeconds"] == 120
    assert deltas["overtimeActiveDeltaSeconds"] == 0

def test_night_overtime_summary_fills_remainder_with_overtime_fill_not_missed():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Night Worker", "displayName": "Night Worker", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Night Worker",
            "date": "2026-05-09",
            "startedAt": dt.datetime(2026, 5, 9, 0, 0, 22, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 9, 1, 30, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[0]["overtimeActiveSeconds"] = 120
    hourly_activity[0]["overtimeActiveMicroseconds"] = 120_000_000
    hourly_activity[0]["fillSegments"] = [{"kind": "overtime", "startSecond": 2590, "endSecond": 2710}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Night Worker",
            "projectId": "night-project",
            "date": "2026-05-09",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 120,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Night Worker",
            "date": "2026-05-09",
            "recordedAt": "2026-05-09T00:45:10+00:00",
            "receivedAt": dt.datetime(2026, 5, 9, 0, 45, 10, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 120,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-09", end_date="2026-05-09")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Night Worker")
    hour_0 = next(hour for hour in hourly_author["hourlyActivity"] if hour["hour"] == 0)

    assert hour_0["totals"]["overtimeSeconds"] == 3600
    assert hour_0["totals"]["idleSeconds"] == 0
    assert hour_0["totals"]["missedSeconds"] == 0
    assert _overtime_fill_seconds(hour_0) == 3480
    assert _missed_end_seconds(hour_0) == 0

def test_activity_summary_visual_missed_end_moves_to_next_partial_hour_when_report_hour_is_full():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Евгений Доценко", "displayName": "Evgeniy Dotsenko", "timeZoneId": "Europe/Sofia"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "date": "2026-05-01",
            "startedAt": dt.datetime(2026, 5, 1, 8, 0, 49, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 1, 16, 3, 53, tzinfo=dt.UTC),
            "timeZoneId": "Europe/Sofia",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Евгений Доценко",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T18:36:36.5849075+03:00",
            "receivedAt": dt.datetime(2026, 5, 1, 15, 36, 41, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 300,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[18]["idleSeconds"] = 3372
    hourly_activity[18]["meetingSeconds"] = 228
    hourly_activity[19]["idleSeconds"] = 232
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Евгений Доценко",
            "projectId": "unity",
            "date": "2026-05-01",
            "activeSeconds": 3877,
            "idleSeconds": 23875,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Евгений Доценко")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _missed_end_seconds(hourly_by_hour[18]) == 0
    assert _hour_metric(hourly_by_hour[19], "idleSeconds") == 465
    assert _missed_end_seconds(hourly_by_hour[19]) == 3600 - 465

def test_activity_summary_visual_missed_end_uses_one_next_empty_hour_when_offline_hour_is_full():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Denis Ostrovskiy", "displayName": "Denis Ostrovskiy", "timeZoneId": "Europe/Kyiv"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "date": "2026-05-07",
            "startedAt": dt.datetime(2026, 5, 7, 8, 23, 3, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 7, 17, 18, 36, tzinfo=dt.UTC),
            "timeZoneId": "Europe/Kyiv",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "date": "2026-05-07",
            "recordedAt": "2026-05-07T20:18:46+03:00",
            "receivedAt": dt.datetime(2026, 5, 7, 17, 18, 47, tzinfo=dt.UTC),
            "activeDeltaSeconds": 54,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 10,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[20]["activeSeconds"] = 146
    hourly_activity[20]["activeMicroseconds"] = 146 * 1_000_000
    hourly_activity[20]["idleSeconds"] = 1016
    hourly_activity[20]["idleMicroseconds"] = 1016 * 1_000_000
    hourly_activity[20]["overtimeActiveSeconds"] = 2438
    hourly_activity[20]["overtimeActiveMicroseconds"] = 2438 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "projectId": "unity",
            "date": "2026-05-07",
            "activeSeconds": 146,
            "idleSeconds": 1016,
            "overtimeActiveSeconds": 2438,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-07", end_date="2026-05-07")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Denis Ostrovskiy")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Denis Ostrovskiy")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _missed_end_seconds(hourly_by_hour[20]) == 0
    assert _hour_metric(hourly_by_hour[21], "missedSeconds") == 3600
    assert _missed_end_seconds(hourly_by_hour[21]) == 3600
    assert _hour_metric(hourly_by_hour[22], "missedSeconds") == 0
    assert _hour_metric(author, "activeSeconds") == 146
    assert _hour_metric(author, "idleSeconds") == 1016

def test_activity_summary_does_not_mark_visual_end_missed_before_offline():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T14:37:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 14, 37, 5, tzinfo=dt.UTC),
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _missed_end_seconds(hourly_by_hour[14]) == 0
    assert _hour_metric(hourly_by_hour[14], "missedSeconds") == 0

def test_activity_summary_counts_latest_report_to_offline_gap_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T20:10:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-start",
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T20:07:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 20, 7, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 7 * 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[20]["idleSeconds"] = 7 * 60
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 7 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[20], "idleSeconds") == 10 * 60
    assert _missed_end_seconds(hourly_by_hour[20]) == 50 * 60
    assert _hour_metric(hourly_by_hour[20], "missedSeconds") == 50 * 60
    assert _hour_metric(author, "idleSeconds") == 40200
    assert author["pluginDaySeconds"] == 40200
    assert _hour_metric(summary["totals"], "idleSeconds") == 40200
    assert summary["totals"]["pluginDaySeconds"] == 40200

def test_activity_summary_counts_unaccounted_latest_report_hour_gap_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T20:10:00Z")
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "activity-start",
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T20:07:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 20, 7, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 6 * 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[20]["idleSeconds"] = 6 * 60
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 0,
            "idleSeconds": 6 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert _hour_metric(hourly_by_hour[20], "idleSeconds") == 10 * 60
    assert _missed_end_seconds(hourly_by_hour[20]) == 50 * 60
    assert _hour_metric(author, "idleSeconds") == 40200
    assert author["pluginDaySeconds"] == 40200
    assert _hour_metric(summary["totals"], "idleSeconds") == 40200
    assert summary["totals"]["pluginDaySeconds"] == 40200

def test_auto_break_does_not_use_telegram_to_first_activity_gap():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-04-28",
        }
    )
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T10:17:30Z",
            "receivedAt": dt.datetime(2026, 4, 28, 10, 17, 31, tzinfo=dt.UTC),
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
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 77 * 60 + 30
    assert _hour_metric(author, "idleSeconds") == 77 * 60 + 30
    assert _hour_metric(author, "breakSeconds") == 0
    assert _hour_metric(hourly_by_hour[9], "idleSeconds") == 3600
    assert _hour_metric(hourly_by_hour[9], "breakSeconds") == 0
    assert _hour_metric(hourly_by_hour[9], "telegramToFirstActivityIdleSeconds") == 3600
    assert _hour_metric(hourly_by_hour[10], "idleSeconds") == 17 * 60 + 30
    assert _hour_metric(hourly_by_hour[10], "telegramToFirstActivityIdleSeconds") == 17 * 60 + 30
    assert _hour_segments(hourly_by_hour[10], "telegram-idle")[0] == {"startSecond": 0, "endSecond": 17 * 60 + 30}

def test_plugin_hour_gap_after_telegram_to_first_activity_extends_active_fill():
    hourly_activity = empty_hourly_activity()
    hourly_activity[12]["activeSeconds"] = 2739
    hourly_activity[12]["activeMicroseconds"] = 2739 * 1_000_000
    hourly_activity[12]["idleSeconds"] = 962
    hourly_activity[12]["idleMicroseconds"] = 962 * 1_000_000
    hourly_activity[12]["telegramToFirstActivityIdleSeconds"] = 860
    hourly_activity[12]["pluginHourGapIdleSeconds"] = 102
    hourly_activity[12]["fillSegments"] = [{"kind": "idle", "startSecond": 0, "endSecond": 860}]

    public_hourly = public_hourly_activity(hourly_activity)

    assert _hour_segments(public_hourly[12], "telegram-idle") == [{"startSecond": 0, "endSecond": 860}]
    assert _hour_segments(public_hourly[12], "active") == [{"startSecond": 860, "endSecond": 3600}]
    assert _hour_segments(public_hourly[12], "idle") == []

def test_public_hourly_missed_does_not_override_overtime_segments():
    hourly_activity = empty_hourly_activity()
    overtime_segments = [
        {"kind": "overtime", "startSecond": 684, "endSecond": 1658},
        {"kind": "overtime", "startSecond": 1819, "endSecond": 2739},
        {"kind": "overtime", "startSecond": 3540, "endSecond": 3543},
    ]
    overtime_seconds = sum(segment["endSecond"] - segment["startSecond"] for segment in overtime_segments)
    hourly_activity[23]["overtimeActiveSeconds"] = overtime_seconds
    hourly_activity[23]["overtimeActiveMicroseconds"] = overtime_seconds * 1_000_000
    hourly_activity[23]["fillSegments"].extend(overtime_segments)
    add_visual_missed_seconds(hourly_activity, 23, 3600 - overtime_seconds, INTERNAL_MISSED_END_SECONDS)

    public = public_hourly_activity(hourly_activity)

    assert _hour_metric(public[23], "overtimeActiveSeconds") == overtime_seconds
    assert _hour_metric(public[23], "missedSeconds") == 3600 - overtime_seconds

def test_public_hourly_missed_still_fills_empty_tail_after_activity():
    hourly_activity = empty_hourly_activity()
    hourly_activity[18]["activeSeconds"] = 1200
    hourly_activity[18]["activeMicroseconds"] = 1_200_000_000
    hourly_activity[18]["fillSegments"].append({"kind": "active", "startSecond": 0, "endSecond": 1200})
    add_visual_missed_seconds(hourly_activity, 18, 600, INTERNAL_MISSED_END_SECONDS)

    public = public_hourly_activity(hourly_activity)

    assert _hour_metric(public[18], "activeSeconds") == 1200
    assert _hour_segments(public[18], "missed") == [{"startSecond": 1200, "endSecond": 3600}]

def test_public_hourly_missed_still_fills_empty_start_before_activity():
    hourly_activity = empty_hourly_activity()
    hourly_activity[9]["activeSeconds"] = 900
    hourly_activity[9]["activeMicroseconds"] = 900_000_000
    hourly_activity[9]["fillSegments"].append({"kind": "active", "startSecond": 600, "endSecond": 1500})
    add_visual_missed_seconds(hourly_activity, 9, 600, "_visualMissedStartSeconds")

    public = public_hourly_activity(hourly_activity)

    assert _hour_segments(public[9], "missed") == [{"startSecond": 0, "endSecond": 600}]
    assert _hour_segments(public[9], "active") == [{"startSecond": 600, "endSecond": 1500}]

def test_plugin_hour_gap_after_telegram_to_first_activity_keeps_real_idle_visible():
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 1462
    hourly_activity[10]["activeMicroseconds"] = 1462 * 1_000_000
    hourly_activity[10]["idleSeconds"] = 1028
    hourly_activity[10]["idleMicroseconds"] = 1028 * 1_000_000
    hourly_activity[10]["missedSeconds"] = 1122
    hourly_activity[10]["_visualMissedStartSeconds"] = 1122
    hourly_activity[10]["telegramToFirstActivityIdleSeconds"] = 39
    hourly_activity[10]["pluginHourGapIdleSeconds"] = 208
    hourly_activity[10]["fillSegments"] = [
        {"kind": "missed", "startSecond": 0, "endSecond": 1122},
        {"kind": "idle", "startSecond": 1122, "endSecond": 1161},
        {"kind": "active", "startSecond": 1368, "endSecond": 1709},
        {"kind": "active", "startSecond": 1697, "endSecond": 2309},
        {"kind": "active", "startSecond": 2404, "endSecond": 2913},
    ]

    public_hourly = public_hourly_activity(hourly_activity)

    assert sum(public_hourly[10]["totals"].values()) == 3600
    assert _hour_metric(public_hourly[10], "idleSeconds") == 820


def test_plugin_hour_gap_fills_empty_in_workday_hour_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Los_Angeles",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[17]["activeSeconds"] = 60
    hourly_activity[17]["activeMicroseconds"] = 60 * 1_000_000
    hourly_activity[17]["fillSegments"] = [{"kind": "active", "startSecond": 0, "endSecond": 60}]
    hourly_activity[22]["activeSeconds"] = 60
    hourly_activity[22]["activeMicroseconds"] = 60 * 1_000_000
    hourly_activity[22]["fillSegments"] = [{"kind": "active", "startSecond": 0, "endSecond": 60}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "Bike Rush 2",
            "date": "2026-05-07",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
            "lastRecordedAt": "2026-05-07T22:01:00-07:00",
            "lastReceivedAt": dt.datetime(2026, 5, 8, 5, 1, tzinfo=dt.UTC),
            "timeZoneId": "America/Los_Angeles",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "author": "Igor Mats",
            "date": "2026-05-07",
            "source": "vsc",
            "reportType": "auto",
            "recordedAt": "2026-05-07T22:01:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 8, 5, 1, tzinfo=dt.UTC),
            "timeZoneId": "America/Los_Angeles",
        }
    )

    summary = repo.activity_summary(start_date="2026-05-07", end_date="2026-05-07")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Igor Mats")["hourlyActivity"]

    assert _hour_metric(hourly[16], "idleSeconds") == 0
    assert _hour_metric(hourly[18], "idleSeconds") == 3600
    assert _hour_segments(hourly[18], "idle") == [{"startSecond": 0, "endSecond": 3600}]


def test_fractional_meeting_start_does_not_leave_active_sliver_after_missed():
    repo = fake_repository()
    buckets = {("Igor Mats", "2026-05-07"): empty_hourly_activity()}
    add_meeting_interval_to_buckets(
        buckets,
        "Igor Mats",
        dt.datetime(2026, 5, 7, 14, 48, 11, 740000, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 7, 16, 1, 21, 352000, tzinfo=dt.UTC),
        "America/Vancouver",
    )
    hourly_activity = buckets[("Igor Mats", "2026-05-07")]
    hourly_activity[7]["missedSeconds"] = 2891
    hourly_activity[7]["_visualMissedStartSeconds"] = 2891
    hourly_activity[7]["fillSegments"].append({"kind": "missed", "startSecond": 0, "endSecond": 2891})

    public_hourly = public_hourly_activity(hourly_activity)

    assert _hour_metric(public_hourly[7], "activeSeconds") == 0
    assert _hour_segments(public_hourly[7], "missed") == [{"startSecond": 0, "endSecond": 2891}]
    assert _hour_segments(public_hourly[7], "meeting") == [{"startSecond": 2891, "endSecond": 3600}]


def test_visual_missed_start_overrides_meeting_before_telegram_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )
    repo.record_break_event("future_artist", "online", "2026-04-28T08:01:05Z")
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
    buckets = {("Future Artist", "2026-04-28"): empty_hourly_activity()}
    add_meeting_interval_to_buckets(
        buckets,
        "Future Artist",
        dt.datetime(2026, 4, 28, 7, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 28, 8, 5, tzinfo=dt.UTC),
        "UTC",
    )
    repo._meeting_buckets_for_daily_items = lambda daily_items, now=None: buckets

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_segments(hourly[8], "missed") == [{"startSecond": 0, "endSecond": 65}]
    assert _hour_segments(hourly[8], "meeting")[0] == {"startSecond": 65, "endSecond": 300}


def test_online_prompt_confirm_before_first_activity_keeps_meeting_after_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )
    first_activity_at = dt.datetime(2026, 4, 28, 7, 50, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("Future Artist", "2026-04-28", "ual", first_activity_at)
    reminder_id = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"
    repo.close_telegram_online_prompt(reminder_id, "confirm_online", "2026-04-28T08:01:00Z", "future_artist")
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
    buckets = {("Future Artist", "2026-04-28"): empty_hourly_activity()}
    add_meeting_interval_to_buckets(
        buckets,
        "Future Artist",
        first_activity_at,
        dt.datetime(2026, 4, 28, 8, 5, tzinfo=dt.UTC),
        "UTC",
    )
    repo._meeting_buckets_for_daily_items = lambda daily_items, now=None: buckets

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    telegram_row = next(row for row in repo.db.report_rows.items if row.get("source") == "telegram")

    assert telegram_row["recordedAt"] == "2026-04-28T07:49:00+00:00"
    assert _hour_segments(hourly[7], "missed") == [{"startSecond": 0, "endSecond": 49 * 60}]
    assert _hour_segments(hourly[7], "meeting") == [{"startSecond": 49 * 60, "endSecond": 59 * 60}]
    assert _hour_segments(hourly[8], "missed") == []
    assert _hour_segments(hourly[8], "meeting")[0] == {"startSecond": 0, "endSecond": 5 * 60}


def test_meeting_to_first_plugin_gap_counts_as_idle_with_auto_break_enabled():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-04-28",
        }
    )
    first_activity_at = dt.datetime(2026, 4, 28, 7, 50, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("Future Artist", "2026-04-28", "ual", first_activity_at)
    reminder_id = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"
    repo.close_telegram_online_prompt(reminder_id, "confirm_online", "2026-04-28T08:01:00Z", "future_artist")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T09:27:00Z",
            "receivedAt": dt.datetime(2026, 4, 28, 9, 27, tzinfo=dt.UTC),
            "activeDeltaSeconds": 45,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[9]["activeSeconds"] = 45
    hourly_activity[9]["activeMicroseconds"] = 45 * 1_000_000
    hourly_activity[9]["fillSegments"] = [{"kind": "active", "startSecond": 27 * 60, "endSecond": 27 * 60 + 45}]
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 45,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    buckets = {("Future Artist", "2026-04-28"): empty_hourly_activity()}
    add_meeting_interval_to_buckets(
        buckets,
        "Future Artist",
        first_activity_at,
        dt.datetime(2026, 4, 28, 8, 5, tzinfo=dt.UTC),
        "UTC",
    )
    repo._meeting_buckets_for_daily_items = lambda daily_items, now=None: buckets

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    expected_idle = (60) + (55 * 60) + (27 * 60)
    assert _hour_metric(author, "idleSeconds") == expected_idle
    assert _hour_metric(author, "breakSeconds") == 0
    assert _hour_metric(summary["totals"], "idleSeconds") == expected_idle
    assert _hour_metric(hourly[8], "idleSeconds") == 55 * 60
    assert _hour_metric(hourly[9], "idleSeconds") == 27 * 60


def test_auto_break_skips_telegram_gap_and_uses_plugin_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-04-28",
        }
    )
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "date": "2026-04-28",
            "recordedAt": "2026-04-28T10:17:30Z",
            "receivedAt": dt.datetime(2026, 4, 28, 10, 17, 31, tzinfo=dt.UTC),
            "activeDeltaSeconds": 60,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[11]["idleSeconds"] = 3600
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 60,
            "idleSeconds": 3600,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 77 * 60 + 30
    assert _hour_metric(author, "idleSeconds") == 77 * 60 + 30
    assert _hour_metric(author, "breakSeconds") == 3600
    assert _hour_metric(hourly_by_hour[9], "idleSeconds") == 3600
    assert _hour_metric(hourly_by_hour[9], "breakSeconds") == 0
    assert _hour_metric(hourly_by_hour[10], "idleSeconds") == 17 * 60 + 30
    assert _hour_metric(hourly_by_hour[10], "breakSeconds") == 0
    assert _hour_metric(hourly_by_hour[11], "idleSeconds") == 0
    assert _hour_metric(hourly_by_hour[11], "breakSeconds") == 3600

def test_auto_break_disabled_keeps_idle_as_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "autoBreakEnabled": False})
    repo._save_event_batch(
        "cur",
        "1.0.0",
        {
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "timeZoneId": "UTC",
            "events": [
                {"eventId": "selection-1", "eventType": "selection", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
                {"eventId": "heartbeat-1", "eventType": "heartbeat", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T10:00:00Z"},
            ],
        },
        "raw-1",
        "auto",
        dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
        "challenge-1",
        None,
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert _hour_metric(daily, "idleSeconds") == 7200
    assert daily.get("breakSeconds", 0) == 0

def test_auto_break_moves_first_idle_hour_to_break_in_summary_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    repo._save_event_batch(
        "cur",
        "1.0.0",
        {
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "timeZoneId": "UTC",
            "events": [
                {"eventId": "selection-1", "eventType": "selection", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
                {"eventId": "heartbeat-1", "eventType": "heartbeat", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T10:00:00Z"},
            ],
        },
        "raw-1",
        "auto",
        dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
        "challenge-1",
        None,
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert _hour_metric(daily, "idleSeconds") == 7200
    assert daily.get("breakSeconds", 0) == 0
    assert daily.get("autoBreakSeconds", 0) == 0
    assert sum(_hour_metric(hour, "idleSeconds") for hour in daily["hourlyActivity"]) == 7200
    assert sum(hour.get("breakSeconds", 0) for hour in daily["hourlyActivity"]) == 0

    report_page = repo.reports_page(start_date="2026-05-02", end_date="2026-05-02", author="Future Artist")
    assert report_page["reports"][0]["idleDeltaSeconds"] == 7200
    assert report_page["reports"][0].get("breakDeltaSeconds", 0) == 0

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    assert _hour_metric(author, "idleSeconds") == 3600
    assert _hour_metric(author, "breakSeconds") == 3600
    assert author["pluginDaySeconds"] == 3600
    assert author["rawPluginDaySeconds"] == 3600
    assert sum(_hour_metric(hour, "idleSeconds") for hour in hourly) == 3600
    assert sum(_hour_metric(hour, "breakSeconds") for hour in hourly) == 3600
    assert summary["totals"]["pluginDaySeconds"] == 3600
    assert summary["totals"]["rawPluginDaySeconds"] == 3600

    repo.rebuild_aggregates_if_needed(force=True)
    rebuilt = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert _hour_metric(rebuilt, "idleSeconds") == 7200
    assert rebuilt.get("breakSeconds", 0) == 0
    assert rebuilt.get("autoBreakSeconds", 0) == 0

def test_auto_break_adds_break_segment_at_end_of_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[13]["idleSeconds"] = 240
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "projectId": "AL",
            "timeZoneId": "UTC",
            "activeSeconds": 0,
            "idleSeconds": 240,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(author, "breakSeconds") == 240
    assert _hour_metric(hourly[13], "breakSeconds") == 240
    assert _hour_segments(hourly[13], "auto-afk") == [{"startSecond": 0, "endSecond": 240}]

def test_break_interval_segments_can_cross_hour_boundary():
    repo = fake_repository()
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 5, 2, 13, 56, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 2, 14, 3, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo._save_event_batch(
        "cur",
        "1.0.0",
        {
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "timeZoneId": "UTC",
            "events": [
                {"eventId": "selection-1", "eventType": "selection", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T13:00:00Z"},
                {"eventId": "heartbeat-1", "eventType": "heartbeat", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T14:10:00Z"},
            ],
        },
        "raw-1",
        "auto",
        dt.datetime(2026, 5, 2, 14, 10, tzinfo=dt.UTC),
        "challenge-1",
        None,
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(hourly[13], "breakSeconds") == 240
    assert _hour_segments(hourly[13], "afk") == [{"startSecond": 3360, "endSecond": 3600}]
    assert _hour_metric(hourly[14], "breakSeconds") == 180
    assert _hour_segments(hourly[14], "afk") == [{"startSecond": 0, "endSecond": 180}]

def test_real_break_does_not_convert_remaining_idle_to_break_segment():
    hourly_activity = empty_hourly_activity()
    hourly_activity[16]["activeSeconds"] = 1900
    hourly_activity[16]["idleSeconds"] = 93
    break_buckets = empty_hourly_activity()
    break_buckets[16]["breakSeconds"] = 53
    break_buckets[16]["fillSegments"] = [{"kind": "afk", "startSecond": 0, "endSecond": 53}]

    hourly = apply_breaks_to_hourly_activity(hourly_activity, break_buckets)

    assert _hour_metric(hourly[16], "activeSeconds") == 1900
    assert _hour_metric(hourly[16], "breakSeconds") == 53
    assert _hour_metric(hourly[16], "idleSeconds") == 40
    assert _hour_segments(hourly[16], "afk") == [{"startSecond": 0, "endSecond": 53}]

def test_auto_break_adds_full_daily_limit_even_with_real_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 5, 2, 7, 30, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "breakSeconds": 1800,
        }
    )
    repo._save_event_batch(
        "cur",
        "1.0.0",
        {
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "timeZoneId": "UTC",
            "events": [
                {"eventId": "selection-1", "eventType": "selection", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
                {"eventId": "heartbeat-1", "eventType": "heartbeat", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T10:00:00Z"},
            ],
        },
        "raw-1",
        "auto",
        dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
        "challenge-1",
        None,
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert _hour_metric(daily, "idleSeconds") == 7200
    assert daily.get("breakSeconds", 0) == 0
    assert daily.get("autoBreakSeconds", 0) == 0

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    assert _hour_metric(author, "idleSeconds") == 5400
    assert _hour_metric(author, "breakSeconds") == 5400

def test_auto_break_uses_one_daily_limit_across_sources():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    cur_hourly = empty_hourly_activity()
    vsc_hourly = empty_hourly_activity()
    cur_hourly[8]["idleSeconds"] = 3600
    vsc_hourly[9]["idleSeconds"] = 3600
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "activeSeconds": 0,
            "idleSeconds": 3600,
            "daySeconds": 0,
            "hourlyActivity": cur_hourly,
            "lastRecordedAt": "2026-05-02T10:00:00Z",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "vsc",
            "activeSeconds": 0,
            "idleSeconds": 3600,
            "daySeconds": 0,
            "hourlyActivity": vsc_hourly,
            "lastRecordedAt": "2026-05-02T11:00:00Z",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 11, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(author, "idleSeconds") == 3600
    assert _hour_metric(author, "breakSeconds") == 3600
    assert sum(_hour_metric(hour, "idleSeconds") for hour in hourly) == 3600
    assert sum(_hour_metric(hour, "breakSeconds") for hour in hourly) == 3600

def test_auto_break_uses_completed_plugin_hour_idle_gaps():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[8]["activeSeconds"] = 60
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "daySeconds": 0,
            "hourlyActivity": hourly_activity,
            "lastRecordedAt": "2026-05-02T10:00:00Z",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "reportType": "auto",
            "recordedAt": "2026-05-02T10:00:00Z",
            "receivedAt": dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-02",
            "startedAt": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(author, "breakSeconds") == 0
    assert _hour_metric(hourly[8], "idleSeconds") == 3540
    assert _hour_metric(hourly[8], "breakSeconds") == 0

def test_auto_break_skips_incomplete_plugin_hour_idle_gaps():
    repo = fake_repository()
    hourly_activity = empty_hourly_activity()
    hourly_activity[8]["activeSeconds"] = 60
    hourly_activity[8]["idleSeconds"] = 1800
    hourly_activity[8]["pluginHourGapIdleSeconds"] = 1800

    transferred_seconds = transfer_summary_idle_to_auto_break(hourly_activity, 3600)

    assert transferred_seconds == 0
    assert _hour_metric(hourly_activity[8], "idleSeconds") == 1800
    assert _hour_metric(hourly_activity[8], "breakSeconds") == 0

def test_auto_break_places_partial_break_after_remaining_idle():
    repo = fake_repository()
    hourly_activity = empty_hourly_activity()
    hourly_activity[13]["activeSeconds"] = 2190
    hourly_activity[13]["idleSeconds"] = 1410

    transferred_seconds = transfer_summary_idle_to_auto_break(hourly_activity, 1149)

    assert transferred_seconds == 1149
    assert _hour_metric(hourly_activity[13], "idleSeconds") == 261
    assert _hour_metric(hourly_activity[13], "breakSeconds") == 1149
    public_hourly = public_hourly_activity(hourly_activity)
    assert _hour_segments(public_hourly[13], "auto-afk") == [{"startSecond": 2190, "endSecond": 3339}]
    assert _hour_segments(public_hourly[13], "idle") == [{"startSecond": 3339, "endSecond": 3600}]

def test_auto_break_does_not_overflow_hour_with_visual_missed_start():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[12]["activeSeconds"] = 1800
    hourly_activity[11]["idleSeconds"] = 3600
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "activeSeconds": 1800,
            "idleSeconds": 3600,
            "daySeconds": 0,
            "hourlyActivity": hourly_activity,
            "lastRecordedAt": "2026-05-02T12:30:00Z",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 12, 30, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-02",
            "source": "cur",
            "reportType": "auto",
            "recordedAt": "2026-05-02T12:30:00Z",
            "receivedAt": dt.datetime(2026, 5, 2, 12, 30, tzinfo=dt.UTC),
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-02",
            "startedAt": dt.datetime(2026, 5, 2, 11, 16, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(hourly[11], "breakSeconds") == 2640
    assert _hour_metric(hourly[11], "missedSeconds") == 960
    assert _missed_start_seconds(hourly[11]) == 960
    assert (
        _hour_metric(hourly[11], "activeSeconds")
        + _hour_metric(hourly[11], "idleSeconds")
        + _hour_metric(hourly[11], "breakSeconds")
        + _hour_metric(hourly[11], "meetingSeconds")
        + _hour_metric(hourly[11], "overtimeActiveSeconds")
        + _hour_metric(hourly[11], "missedSeconds")
    ) == 3600

def test_auto_break_waits_until_effective_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-03",
        }
    )
    repo._save_event_batch(
        "cur",
        "1.0.0",
        {
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "timeZoneId": "UTC",
            "events": [
                {"eventId": "selection-1", "eventType": "selection", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
                {"eventId": "heartbeat-1", "eventType": "heartbeat", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T10:00:00Z"},
            ],
        },
        "raw-1",
        "auto",
        dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
        "challenge-1",
        None,
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert _hour_metric(daily, "idleSeconds") == 7200
    assert daily.get("breakSeconds", 0) == 0

def test_auto_break_preserves_meeting_priority():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "date": "2026-05-02",
            "activeSeconds": 0,
            "idleSeconds": 3600,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 0, "idleSeconds": 3600, "breakSeconds": 0, "meetingSeconds": 0, "overtimeActiveSeconds": 0}
            ],
            "timeZoneId": "UTC",
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 2, 10, 30, tzinfo=dt.UTC),
            "date": "2026-05-02",
            "timeZoneId": "UTC",
            "meetingSeconds": 1800,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02", now=dt.datetime(2026, 5, 2, 11, tzinfo=dt.UTC))
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "idleSeconds") == 0
    assert _hour_metric(author, "meetingSeconds") == 1800
    assert _hour_metric(author, "breakSeconds") == 1800
    assert _hour_metric(hour, "idleSeconds") == 0
    assert _hour_metric(hour, "meetingSeconds") == 1800
    assert _hour_metric(hour, "breakSeconds") == 1800

def test_auto_break_skips_hour_fully_used_by_meeting():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["idleSeconds"] = 3600
    hourly_activity[11]["idleSeconds"] = 1800
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "date": "2026-05-02",
            "activeSeconds": 0,
            "idleSeconds": 5400,
            "hourlyActivity": hourly_activity,
            "timeZoneId": "UTC",
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 2, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 2, 11, 0, tzinfo=dt.UTC),
            "date": "2026-05-02",
            "timeZoneId": "UTC",
            "meetingSeconds": 3600,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02", now=dt.datetime(2026, 5, 2, 12, tzinfo=dt.UTC))
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]

    assert _hour_metric(author, "idleSeconds") == 0
    assert _hour_metric(author, "meetingSeconds") == 3600
    assert _hour_metric(author, "breakSeconds") == 1800
    assert _hour_metric(hourly[10], "meetingSeconds") == 3600
    assert _hour_metric(hourly[10], "breakSeconds") == 0
    assert _hour_metric(hourly[11], "breakSeconds") == 1800

def test_overtime_activity_after_telegram_offline_is_allowed():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 32400,
            "activeMicroseconds": 32400 * 1_000_000,
            "idleSeconds": 0,
            "idleMicroseconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
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
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:00:15Z")

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:30Z",
            "occurredAtLocal": "2026-04-28T18:00:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, 30, tzinfo=dt.UTC),
        }
    )

    assert deltas["activeDeltaSeconds"] == 15
    assert deltas["overtimeActiveDeltaSeconds"] == 15
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert _hour_metric(daily, "activeSeconds") == 32415
    assert _hour_metric(daily, "overtimeActiveSeconds") == 15

def test_vacation_day_plugin_activity_is_overtime_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.calendar_marks.insert_one(
        {"rawAuthor": "Future Artist", "date": "2026-05-06", "reasonId": "vacation", "note": "Vacation"}
    )

    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 1800
    repo._apply_snapshot_to_aggregates(
        {
            "source": "cur",
            "pluginVersion": "1.0.0",
            "author": "Future Artist",
            "projectId": "AL",
            "sessionId": "session-1",
            "deviceId": "mac-mini",
            "date": "2026-05-06",
            "recordedAt": "2026-05-06T10:30:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 6, 10, 30, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-06T10:30:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 5, 6, 10, 30, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
            "activeSeconds": 1800,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [{"type": "select", "count": 1}],
            "savedPrefabs": [],
            "hourlyActivity": hourly_activity,
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-06", "source": "cur"})
    summary = repo.activity_summary(start_date="2026-05-06", end_date="2026-05-06")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_10 = next(item for item in hourly if item["hour"] == 10)

    assert _hour_metric(daily, "activeSeconds") == 0
    assert _hour_metric(daily, "overtimeActiveSeconds") == 1800
    assert author["dayOverride"]["type"] == "vacation"
    assert _hour_metric(author, "activeSeconds") == 0
    assert _hour_metric(author, "idleSeconds") == 0
    assert _hour_metric(author, "breakSeconds") == 0
    assert _hour_metric(author, "meetingSeconds") == 0
    assert _hour_metric(author, "overtimeActiveSeconds") == 1800
    assert _hour_metric(hour_10, "activeSeconds") == 0
    assert _hour_metric(hour_10, "idleSeconds") == 0
    assert _hour_metric(hour_10, "breakSeconds") == 0
    assert _hour_metric(hour_10, "meetingSeconds") == 0
    assert _hour_metric(hour_10, "missedSeconds") == 0
    assert _hour_metric(hour_10, "overtimeActiveSeconds") == 1800
    assert repo.db.telegram_online_prompts.count_documents({"rawAuthor": "Future Artist"}) == 0

def test_vacation_day_meeting_is_overtime_in_summary():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.calendar_marks.insert_one(
        {"rawAuthor": "Future Artist", "date": "2026-05-06", "reasonId": "vacation", "note": "Vacation"}
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-06",
            "startedAt": dt.datetime(2026, 5, 6, 11, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 6, 11, 45, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "meetingSeconds": 2700,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-06", end_date="2026-05-06", now=dt.datetime(2026, 5, 6, 12, tzinfo=dt.UTC))
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_11 = next(item for item in hourly if item["hour"] == 11)

    assert author["calendarDayMark"]["reasonId"] == "vacation"
    assert _hour_metric(author, "meetingSeconds") == 0
    assert _hour_metric(author, "overtimeActiveSeconds") == 2700
    assert _hour_metric(hour_11, "meetingSeconds") == 0
    assert _hour_metric(hour_11, "missedSeconds") == 0
    assert _hour_metric(hour_11, "overtimeActiveSeconds") == 2700

def test_activity_after_work_window_without_offline_stays_normal():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "activeSeconds": 32400,
            "activeMicroseconds": 32400 * 1_000_000,
            "idleSeconds": 0,
            "idleMicroseconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
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

    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T18:00:30Z",
            "occurredAtLocal": "2026-04-28T18:00:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 0, 30, tzinfo=dt.UTC),
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaSeconds"] == 0
    assert _hour_metric(daily, "activeSeconds") == 32430
    assert _hour_metric(daily, "overtimeActiveSeconds") == 0

def test_overtime_window_stops_at_author_local_midnight():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T23:59:30Z")
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "sessionId": "unity-session",
            "date": "2026-04-28",
            "eventType": "selection",
            "occurredAtUtc": "2026-04-28T23:59:45Z",
            "occurredAtLocal": "2026-04-28T23:59:45+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 23, 59, 45, tzinfo=dt.UTC),
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
            "occurredAtUtc": "2026-04-29T00:00:15Z",
            "occurredAtLocal": "2026-04-29T00:00:15+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 0, 0, 15, tzinfo=dt.UTC),
        }
    )

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["overtimeActiveDeltaSeconds"] == 0

def test_overtime_hourly_graph_fills_gap_when_overtime_continues_next_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[19]["overtimeActiveSeconds"] = 52 * 60
    hourly_activity[19]["overtimeActiveMicroseconds"] = 52 * 60 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "projectId": "unity",
            "date": "2026-05-01",
            "activeSeconds": 32400,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 52 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "startedAt": dt.datetime(2026, 5, 1, 19, 10, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 19, 14, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "UTC",
            "meetingSeconds": 4 * 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T18:58:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 1, 18, 58, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T20:03:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 3, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Denis Ostrovskiy")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Denis Ostrovskiy")["hourlyActivity"]
    hour_19 = next(item for item in hourly if item["hour"] == 19)

    assert _hour_metric(hour_19, "meetingSeconds") == 4 * 60
    assert _hour_metric(hour_19, "overtimeActiveSeconds") == 2880
    assert _hour_metric(author, "overtimeActiveSeconds") == 52 * 60
    assert _hour_metric(summary["totals"], "overtimeActiveSeconds") == 52 * 60

def test_overtime_hourly_graph_fills_between_actual_overtime_buckets():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "date": "2026-05-06",
            "startedAt": dt.datetime(2026, 5, 6, 15, 0, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 7, 2, 28, 16, tzinfo=dt.UTC),
            "reminderAction": "overtime",
            "timeZoneId": "America/Vancouver",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[17]["overtimeActiveSeconds"] = 474
    hourly_activity[17]["overtimeActiveMicroseconds"] = 474_000_000
    hourly_activity[18]["overtimeActiveSeconds"] = 3370
    hourly_activity[18]["overtimeActiveMicroseconds"] = 3_370_000_000
    hourly_activity[19]["overtimeActiveSeconds"] = 1904
    hourly_activity[19]["overtimeActiveMicroseconds"] = 1_904_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "projectId": "unity",
            "date": "2026-05-06",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 5748,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "date": "2026-05-06",
            "recordedAt": "2026-05-06T18:01:08-07:00",
            "receivedAt": dt.datetime(2026, 5, 7, 1, 1, 8, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 120,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "date": "2026-05-06",
            "recordedAt": "2026-05-06T19:08:40-07:00",
            "receivedAt": dt.datetime(2026, 5, 7, 2, 8, 40, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 120,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-06", end_date="2026-05-06")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Igor Mats")["hourlyActivity"]
    hour_18 = next(item for item in hourly if item["hour"] == 18)
    hour_19 = next(item for item in hourly if item["hour"] == 19)

    assert _hour_metric(hour_18, "overtimeActiveSeconds") == 3370
    assert _hour_metric(hour_18, "overtimeActiveSeconds") == 3370
    assert _hour_metric(hour_18, "idleSeconds") == 0
    assert _hour_metric(hour_19, "overtimeActiveSeconds") == 1904
    assert _overtime_fill_seconds(hour_19) == 0
    assert _hour_segments(hour_19, "overtime")[0]["startSecond"] >= (28 * 60 + 16)
    assert _missed_end_seconds(hour_19) == 0

def test_overtime_hourly_graph_does_not_fill_gap_without_next_overtime_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[19]["overtimeActiveSeconds"] = 52 * 60
    hourly_activity[19]["overtimeActiveMicroseconds"] = 52 * 60 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "projectId": "unity",
            "date": "2026-05-01",
            "activeSeconds": 32400,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 52 * 60,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "startedAt": dt.datetime(2026, 5, 1, 19, 10, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 19, 14, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "UTC",
            "meetingSeconds": 4 * 60,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Denis Ostrovskiy",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T19:52:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 1, 19, 52, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 60,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Denis Ostrovskiy")["hourlyActivity"]
    hour_19 = next(item for item in hourly if item["hour"] == 19)

    assert _hour_metric(hour_19, "meetingSeconds") == 4 * 60
    assert _hour_metric(hour_19, "overtimeActiveSeconds") == 2880

def test_overtime_hourly_graph_does_not_fill_from_reports_only_inside_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[14]["overtimeActiveSeconds"] = 1665
    hourly_activity[14]["overtimeActiveMicroseconds"] = 1_665_473_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "projectId": "unity",
            "date": "2026-05-01",
            "activeSeconds": 32400,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 1665,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T14:00:29.6226620-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 21, 0, 29, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 137,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T14:27:35.5434240-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 21, 27, 35, tzinfo=dt.UTC),
            "overtimeActiveDeltaSeconds": 56,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Igor Mats")["hourlyActivity"]
    hour_14 = next(item for item in hourly if item["hour"] == 14)

    assert _hour_metric(hour_14, "overtimeActiveSeconds") == 1665

def test_overtime_hourly_graph_fills_normal_to_overtime_transition_gap():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[13]["activeSeconds"] = 125
    hourly_activity[13]["activeMicroseconds"] = 125 * 1_000_000
    hourly_activity[13]["overtimeActiveSeconds"] = 3250
    hourly_activity[13]["overtimeActiveMicroseconds"] = 3250 * 1_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "projectId": "AL",
            "date": "2026-05-01",
            "activeSeconds": 125,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 3250,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "vsc",
            "author": "Igor Mats",
            "date": "2026-05-01",
            "recordedAt": "2026-05-01T13:04:24.965-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 7, 38, tzinfo=dt.UTC),
            "activeDeltaSeconds": 153,
            "overtimeActiveDeltaSeconds": 173,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Igor Mats")["hourlyActivity"]
    hour_13 = next(item for item in hourly if item["hour"] == 13)

    assert _hour_metric(hour_13, "activeSeconds") == 153
    assert _hour_metric(hour_13, "overtimeActiveSeconds") == 3250

def test_activity_hourly_cache_keeps_heavy_hourly_separate():
    repo = fake_repository()
    hourly = empty_hourly_activity()
    hourly[10]["activeSeconds"] = 60
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": hourly,
        }
    )

    lite = repo.cached_activity_summary(
        view="activity-lite",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=False,
        include_breakdowns=False,
    )
    hourly_summary = repo.cached_activity_summary(
        view="activity-hourly",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=False,
    )

    assert lite["hourlyActivityByAuthor"] == []
    assert hourly_summary["hourlyActivityByAuthor"][0]["rawAuthor"] == "Future Artist"
    assert _hour_metric(hourly_summary["hourlyActivityByAuthor"][0]["hourlyActivity"][10], "activeSeconds") == 60

def test_activity_hourly_public_items_only_use_canonical_schema():
    repo = fake_repository()
    hourly = empty_hourly_activity()
    hourly[10]["activeSeconds"] = 60
    hourly[10]["fillSegments"] = [{"kind": "active", "startSecond": 0, "endSecond": 60}]
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "hourlyActivity": hourly,
        }
    )

    summary = repo.cached_activity_summary(
        view="activity-hourly",
        start_date="2026-04-29",
        end_date="2026-04-29",
        include_profiles=False,
        include_hourly=True,
        include_breakdowns=False,
    )
    hour = summary["hourlyActivityByAuthor"][0]["hourlyActivity"][10]

    assert set(hour) == {"hour", "totals", "fillSegments"}
    assert set(hour["totals"]) == {"activeSeconds", "overtimeSeconds", "afkSeconds", "meetingSeconds", "idleSeconds", "missedSeconds"}

def test_analytics_summary_includes_day_hourly_activity():
    repo = fake_repository()
    today = dt.date.today()
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["activeSeconds"] = 1800
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Dmitry Shane",
            "date": today.isoformat(),
            "activeSeconds": 1800,
            "idleSeconds": 900,
            "hourlyActivity": hourly_activity,
        }
    )

    summary = repo.analytics_summary()
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Dmitry Shane")
    month = next(item for item in author["months"] if item["month"] == today.month)
    day = next(
        item
        for week in month["weeks"]
        for item in week["days"]
        if item["date"] == today.isoformat()
    )
    empty_day = next(
        item
        for week in month["weeks"]
        for item in week["days"]
        if item["date"] != today.isoformat()
    )

    assert len(day["hourlyActivity"]) == 24
    assert _hour_metric(day["hourlyActivity"][10], "activeSeconds") == 1800
    assert len(empty_day["hourlyActivity"]) == 24
    assert empty_day["hourlyActivity"] == empty_hourly_activity()

def test_overtime_activity_summary_splits_mix_and_saved_files():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "telegramUsername": "dmitryshane"})
    base_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "authorEmail": "dmitry@example.com",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-29",
        "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
    }
    normal_activity = {
        **base_event,
        "eventType": "selection",
        "occurredAtUtc": "2026-04-29T10:00:00Z",
        "occurredAtLocal": "2026-04-29T10:00:00+00:00",
    }
    normal_save = {
        **base_event,
        "eventType": "prefab_saved",
        "occurredAtUtc": "2026-04-29T10:00:10Z",
        "occurredAtLocal": "2026-04-29T10:00:10+00:00",
        "metadata": {"path": "Assets/Normal.prefab", "name": "Normal"},
    }

    repo._apply_raw_event_to_aggregates(normal_activity)
    repo._apply_raw_event_to_aggregates(normal_save)
    repo.db.daily_author_activity.insert_one(
        {
            "source": "seed",
            "projectId": "seed",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 9 * 3600,
            "idleSeconds": 0,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.record_break_event("dmitryshane", "online", "2026-04-29T09:00:00Z")
    repo.record_break_event("dmitryshane", "offline", "2026-04-29T18:59:00Z")

    overtime_activity = {
        **base_event,
        "eventType": "play_mode",
        "occurredAtUtc": "2026-04-29T19:00:00Z",
        "occurredAtLocal": "2026-04-29T19:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 29, 19, 0, tzinfo=dt.UTC),
    }
    overtime_save = {
        **base_event,
        "eventType": "prefab_saved",
        "occurredAtUtc": "2026-04-29T19:00:10Z",
        "occurredAtLocal": "2026-04-29T19:00:10+00:00",
        "receivedAt": dt.datetime(2026, 4, 29, 19, 0, 10, tzinfo=dt.UTC),
        "metadata": {"path": "Assets/Overtime.prefab", "name": "Overtime"},
    }

    repo._apply_raw_event_to_aggregates(overtime_activity)
    repo._apply_raw_event_to_aggregates(overtime_save)

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")

    assert author["activityMix"] == [
        {"type": "select", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]
    assert author["savedPrefabs"] == [{"path": "Assets/Normal.prefab", "name": "Normal", "saveCount": 1}]
    assert author["overtimeActivityMix"] == [
        {"type": "play_mode", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]
    assert author["overtimeSavedPrefabs"] == [{"path": "Assets/Overtime.prefab", "name": "Overtime", "saveCount": 1}]
    assert summary["overtimeActivityMix"] == [
        {"type": "play_mode", "count": 1, "percent": 50},
        {"type": "prefab_saved", "count": 1, "percent": 50},
    ]

def test_activity_summary_returnsempty_hourly_activity_for_telegram_only_author():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "telegramUsername": "igormats",
            "date": "2026-04-29",
            "startedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 29, 9, 10, tzinfo=dt.UTC),
    )

    hourly_by_author = {author["rawAuthor"]: author for author in summary["hourlyActivityByAuthor"]}
    assert len(hourly_by_author["Igor Mats"]["hourlyActivity"]) == 24
    assert hourly_by_author["Igor Mats"]["hourlyActivity"] == _public_empty_hourly_activity()

def test_hourly_break_subtracts_idle_with_active_priority():
    source = empty_hourly_activity()
    source[16]["activeSeconds"] = 10 * 60
    source[16]["idleSeconds"] = 50 * 60
    breaks = empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60

    hourly = apply_breaks_to_hourly_activity(source, breaks)

    assert _hour_metric(hourly[16], "activeSeconds") == 10 * 60
    assert _hour_metric(hourly[16], "breakSeconds") == 30 * 60
    assert _hour_metric(hourly[16], "idleSeconds") == 20 * 60

def test_hourly_break_consumption_prevents_double_counting_across_sources():
    first_source = empty_hourly_activity()
    first_source[16]["idleSeconds"] = 50 * 60
    second_source = empty_hourly_activity()
    second_source[16]["idleSeconds"] = 50 * 60
    breaks = empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60
    consumed = empty_hourly_activity()

    first_hourly = apply_breaks_to_hourly_activity(first_source, breaks, consumed)
    second_hourly = apply_breaks_to_hourly_activity(second_source, breaks, consumed)

    assert _hour_metric(first_hourly[16], "breakSeconds") == 30 * 60
    assert _hour_metric(second_hourly[16], "breakSeconds") == 0
    assert _hour_metric(consumed[16], "breakSeconds") == 30 * 60

def test_totals_should_use_report_aggregates_not_hourly_buckets():
    source = empty_hourly_activity()
    source[16]["activeSeconds"] = 60
    source[16]["idleSeconds"] = 60
    breaks = empty_hourly_activity()
    hourly = apply_breaks_to_hourly_activity(source, breaks)

    report_active_seconds = 20 * 60
    report_idle_seconds = 60 * 60
    break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly)
    effective_active_seconds = report_active_seconds
    effective_idle_seconds = max(0, report_idle_seconds - break_seconds)

    assert effective_active_seconds == report_active_seconds
    assert effective_idle_seconds == report_idle_seconds

def test_hourly_break_splits_across_hours():
    buckets = {("Dmitry Shane", "2026-04-28"): empty_hourly_activity()}

    add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 16, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 28, 17, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert _hour_metric(buckets[("Dmitry Shane", "2026-04-28")][16], "breakSeconds") == 10 * 60
    assert _hour_metric(buckets[("Dmitry Shane", "2026-04-28")][17], "breakSeconds") == 20 * 60

def test_hourly_break_uses_exact_break_on_and_off_positions():
    source = empty_hourly_activity()
    source[15]["activeSeconds"] = 3600
    source[16]["activeSeconds"] = 3600
    break_buckets = {("Future Artist", "2026-05-02"): empty_hourly_activity()}
    add_break_interval_to_buckets(
        break_buckets,
        "Future Artist",
        dt.datetime(2026, 5, 2, 15, 40, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 2, 16, 40, tzinfo=dt.UTC),
        "UTC",
    )

    hourly = apply_breaks_to_hourly_activity(source, break_buckets[("Future Artist", "2026-05-02")])
    public_hourly = public_hourly_activity(hourly)

    assert _hour_segments(public_hourly[15], "afk") == [{"startSecond": 40 * 60, "endSecond": 3600}]
    assert _hour_segments(public_hourly[16], "afk") == [{"startSecond": 0, "endSecond": 40 * 60}]
    assert _hour_metric(public_hourly[15], "activeSeconds") == 40 * 60
    assert _hour_metric(public_hourly[16], "activeSeconds") == 20 * 60

def test_hourly_break_splits_across_midnight():
    buckets = {
        ("Dmitry Shane", "2026-04-28"): empty_hourly_activity(),
        ("Dmitry Shane", "2026-04-29"): empty_hourly_activity(),
    }

    add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 23, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 0, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert _hour_metric(buckets[("Dmitry Shane", "2026-04-28")][23], "breakSeconds") == 10 * 60
    assert _hour_metric(buckets[("Dmitry Shane", "2026-04-29")][0], "breakSeconds") == 20 * 60

def test_hourly_break_uses_author_time_zone():
    buckets = {("Dmitry", "2026-04-29"): empty_hourly_activity()}

    add_break_interval_to_buckets(
        buckets,
        "Dmitry",
        dt.datetime(2026, 4, 29, 9, 36, 39, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 10, 31, 59, tzinfo=dt.UTC),
        "Europe/Madrid",
    )

    assert _hour_metric(buckets[("Dmitry", "2026-04-29")][9], "breakSeconds") == 0
    assert _hour_metric(buckets[("Dmitry", "2026-04-29")][10], "breakSeconds") == 0
    assert _hour_metric(buckets[("Dmitry", "2026-04-29")][11], "breakSeconds") == 1401
    assert _hour_metric(buckets[("Dmitry", "2026-04-29")][12], "breakSeconds") == 1919

def test_hourly_break_suppresses_small_idle_artifact():
    source = empty_hourly_activity()
    break_buckets = empty_hourly_activity()
    source[15]["activeSeconds"] = 553
    source[15]["idleSeconds"] = 2991
    break_buckets[15]["breakSeconds"] = 2806

    hourly = apply_breaks_to_hourly_activity(source, break_buckets)

    assert _hour_metric(hourly[15], "activeSeconds") == 553
    assert _hour_metric(hourly[15], "breakSeconds") == 2806
    assert _hour_metric(hourly[15], "idleSeconds") == 185

def test_hourly_activity_does_not_infer_small_report_gap_as_idle():
    source = empty_hourly_activity()
    source[14]["activeSeconds"] = 3379

    hourly = apply_breaks_to_hourly_activity(source, empty_hourly_activity())

    assert _hour_metric(hourly[14], "activeSeconds") == 3379
    assert _hour_metric(hourly[14], "idleSeconds") == 0

def test_hourly_activity_does_not_infer_past_hour_gap_as_idle():
    source = empty_hourly_activity()
    source[10]["activeSeconds"] = 1743
    source[10]["idleSeconds"] = 1143

    hourly = apply_breaks_to_hourly_activity(source, empty_hourly_activity())

    assert _hour_metric(hourly[10], "activeSeconds") == 1743
    assert _hour_metric(hourly[10], "idleSeconds") == 1143

def test_hourly_activity_keeps_dmitriy_zero_idle_gap_zero():
    source = empty_hourly_activity()
    source[18]["activeSeconds"] = 3446

    hourly = apply_breaks_to_hourly_activity(source, empty_hourly_activity())

    assert _hour_metric(hourly[18], "activeSeconds") == 3446
    assert _hour_metric(hourly[18], "idleSeconds") == 0
    assert _hour_metric(hourly[18], "breakSeconds") == 0

def test_hourly_activity_preserves_fractional_active_segments():
    batch = _empty_event_deltas()
    local_start = dt.datetime(2026, 4, 29, 18, 0, 0, tzinfo=dt.UTC)
    segment_microseconds = [899_600_000, 900_400_000, 899_600_000, 900_400_000]
    cursor = local_start

    for microseconds in segment_microseconds:
        segment_end = cursor + dt.timedelta(microseconds=microseconds)
        deltas = _interval_deltas(cursor, segment_end, cursor, segment_end, True, 0)
        _merge_batch_deltas(batch, deltas)
        cursor = segment_end

    assert batch["activeDeltaSeconds"] == 3600
    assert _hour_metric(batch["hourlyActivityDelta"][18], "activeSeconds") == 3600
    assert _hour_metric(batch["hourlyActivityDelta"][18], "idleSeconds") == 0

    hourly = apply_breaks_to_hourly_activity(batch["hourlyActivityDelta"], empty_hourly_activity())

    assert _hour_metric(hourly[18], "activeSeconds") == 3600
    assert _hour_metric(hourly[18], "idleSeconds") == 0

def test_hourly_activity_does_not_infer_current_hour_idle():
    source = empty_hourly_activity()
    source[18]["activeSeconds"] = 1800

    hourly = apply_breaks_to_hourly_activity(source, empty_hourly_activity())

    assert _hour_metric(hourly[18], "activeSeconds") == 1800
    assert _hour_metric(hourly[18], "idleSeconds") == 0
    assert _hour_metric(hourly[19], "idleSeconds") == 0

def test_workday_idle_fill_fills_empty_workday_gaps_when_meeting_is_signal():
    hourly = empty_hourly_activity()
    buckets = {("Future Artist", "2026-05-07"): empty_hourly_activity()}
    add_meeting_interval_to_buckets(
        buckets,
        "Future Artist",
        dt.datetime(2026, 5, 7, 12, 30, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 7, 13, 0, tzinfo=dt.UTC),
        "UTC",
    )
    hourly = buckets[("Future Artist", "2026-05-07")]

    assert hourly_activity_has_workday_signal(hourly) is True

    apply_workday_idle_fill(
        hourly,
        dt.datetime(2026, 5, 7, 12, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 7, 14, 0, tzinfo=dt.UTC),
        "UTC",
        hourly_activity_has_workday_signal(hourly),
    )
    public = public_hourly_activity(hourly)

    assert _hour_metric(public[12], "idleSeconds") == 1800
    assert _hour_metric(public[12], "meetingSeconds") == 1800
    assert _hour_metric(public[13], "idleSeconds") == 3600
    assert _hour_metric(public[11], "idleSeconds") == 0
    assert _hour_metric(public[14], "idleSeconds") == 0


def test_open_workday_idle_fill_uses_later_discord_meeting_as_gap_boundary():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-08",
            "startedAt": dt.datetime(2026, 5, 8, 10, 28, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = empty_hourly_activity()
    hourly_activity[10]["idleSeconds"] = 1222
    hourly_activity[10]["idleMicroseconds"] = 1_222_000_000
    hourly_activity[11]["idleSeconds"] = 2209
    hourly_activity[11]["idleMicroseconds"] = 2_209_000_000
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "date": "2026-05-08",
            "activeSeconds": 0,
            "idleSeconds": 3431,
            "workWindowSeconds": 32400,
            "hourlyActivity": hourly_activity,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 5, 8, 17, 16, 19, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 8, 17, 43, 32, tzinfo=dt.UTC),
            "date": "2026-05-08",
            "timeZoneId": "UTC",
            "meetingSeconds": 27 * 60 + 13,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "reportType": "meeting",
            "author": "Future Artist",
            "date": "2026-05-08",
            "recordedAt": "2026-05-08T17:43:32+00:00",
            "receivedAt": dt.datetime(2026, 5, 8, 17, 43, 32, tzinfo=dt.UTC),
            "activityType": "meeting_leave",
            "discordEventType": "leave",
        }
    )

    summary = repo.activity_summary(start_date="2026-05-08", end_date="2026-05-08", now=dt.datetime(2026, 5, 8, 18, tzinfo=dt.UTC))
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")

    assert _hour_metric(hourly[12], "idleSeconds") == 3600
    assert _hour_metric(hourly[13], "idleSeconds") == 3600
    assert _hour_metric(hourly[14], "idleSeconds") == 3600
    assert _hour_metric(hourly[15], "idleSeconds") == 3600
    assert _hour_metric(hourly[16], "idleSeconds") == 3600
    assert _hour_metric(hourly[17], "idleSeconds") == 16 * 60 + 19
    assert _hour_metric(hourly[17], "meetingSeconds") == 27 * 60 + 13
    assert author["idleSeconds"] >= 5 * 3600


def test_workday_idle_fill_requires_real_activity_signal():
    hourly = empty_hourly_activity()
    hourly[12]["idleSeconds"] = 900
    hourly[12]["idleMicroseconds"] = 900_000_000
    hourly[12]["telegramToFirstActivityIdleSeconds"] = 900

    assert hourly_activity_has_workday_signal(hourly) is False

    apply_workday_idle_fill(
        hourly,
        dt.datetime(2026, 5, 7, 12, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 7, 14, 0, tzinfo=dt.UTC),
        "UTC",
        hourly_activity_has_workday_signal(hourly),
    )

    assert _hour_metric(public_hourly_activity(hourly)[13], "idleSeconds") == 0

def test_workday_idle_fill_preserves_existing_missed_and_active_segments():
    hourly = empty_hourly_activity()
    hourly[9]["activeSeconds"] = 1200
    hourly[9]["activeMicroseconds"] = 1_200_000_000
    hourly[9]["fillSegments"].append({"kind": "active", "startSecond": 1200, "endSecond": 2400})
    add_visual_missed_seconds(hourly, 9, 600, INTERNAL_MISSED_END_SECONDS)

    apply_workday_idle_fill(
        hourly,
        dt.datetime(2026, 5, 7, 9, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 7, 10, 0, tzinfo=dt.UTC),
        "UTC",
        hourly_activity_has_workday_signal(hourly),
    )
    public = public_hourly_activity(hourly)

    assert _hour_metric(public[9], "idleSeconds") == 1800
    assert _hour_metric(public[9], "activeSeconds") == 1200
    assert _hour_segments(public[9], "missed") == [{"startSecond": 3000, "endSecond": 3600}]

def test_workday_idle_fill_extends_plugin_gap_to_latest_workday_signal():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-11",
            "startedAt": dt.datetime(2026, 5, 11, 13, 10, tzinfo=dt.UTC),
            "lastOnlineAt": dt.datetime(2026, 5, 11, 15, 1, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-11",
            "source": "ual",
            "recordedAt": "2026-05-11T13:50:15+00:00",
            "activeDeltaSeconds": 2,
            "idleDeltaSeconds": 858,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-11",
            "source": "telegram",
            "reportType": "telegram",
            "recordedAt": dt.datetime(2026, 5, 11, 15, 1, tzinfo=dt.UTC),
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-05-11",
            "startedAt": dt.datetime(2026, 5, 11, 14, 8, 1, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 11, 15, 1, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "breakSeconds": 3179,
        }
    )
    hourly = empty_hourly_activity()
    hourly[13]["activeSeconds"] = 540
    hourly[13]["activeMicroseconds"] = 540_000_000
    hourly[13]["idleSeconds"] = 1542
    hourly[13]["idleMicroseconds"] = 1_542_000_000
    hourly[13]["fillSegments"] = [
        {"kind": "active", "startSecond": 933, "endSecond": 1473},
        {"kind": "idle", "startSecond": 1473, "endSecond": 3015},
    ]
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Future Artist",
            "date": "2026-05-11",
            "source": "ual",
            "activeSeconds": 540,
            "activeMicroseconds": 540_000_000,
            "idleSeconds": 1542,
            "idleMicroseconds": 1_542_000_000,
            "hourlyActivity": hourly,
            "lastRecordedAt": "2026-05-11T13:50:15+00:00",
            "lastReceivedAt": dt.datetime(2026, 5, 11, 13, 50, 15, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-11",
        end_date="2026-05-11",
        now=dt.datetime(2026, 5, 11, 15, 5, tzinfo=dt.UTC),
    )
    hourly_summary = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")[
        "hourlyActivity"
    ]

    assert sum(hourly_summary[13]["totals"].values()) == 3600
    assert _hour_segments(hourly_summary[14], "idle") == [{"startSecond": 0, "endSecond": 8 * 60 + 1}]
    assert _hour_segments(hourly_summary[14], "afk") == [{"startSecond": 8 * 60 + 1, "endSecond": 3600}]

def test_public_hourly_collapses_tiny_active_noise_to_idle_visually_only():
    hourly = empty_hourly_activity()
    hourly[11]["activeSeconds"] = 5
    hourly[11]["activeMicroseconds"] = 5_000_000
    hourly[11]["idleSeconds"] = 2212
    hourly[11]["idleMicroseconds"] = 2_212_000_000
    hourly[11]["telegramToFirstActivityIdleSeconds"] = 1978
    hourly[11]["fillSegments"] = [
        {"kind": "missed", "startSecond": 0, "endSecond": 1383},
        {"kind": "idle", "startSecond": 1383, "endSecond": 3361},
        {"kind": "active", "startSecond": 3361, "endSecond": 3366},
        {"kind": "idle", "startSecond": 3366, "endSecond": 3600},
    ]
    hourly[11]["missedSeconds"] = 1383
    hourly[11]["_visualMissedStartSeconds"] = 1383

    public = public_hourly_activity(hourly)

    assert hourly[11]["activeSeconds"] == 5
    assert _hour_metric(public[11], "activeSeconds") == 0
    assert _hour_metric(public[11], "idleSeconds") == 2217
    assert _hour_segments(public[11], "idle") == [{"startSecond": 3361, "endSecond": 3600}]
