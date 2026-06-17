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
    add_break_interval_to_buckets,
    apply_breaks_to_hourly_activity,
    empty_hourly_activity,
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

def test_discord_meeting_reduces_idle_for_any_activity_source():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 0,
            "idleSeconds": 3600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 0, "idleSeconds": 3600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 45, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1800,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "idleSeconds") == 1800
    assert _hour_metric(author, "meetingSeconds") == 1800
    assert author["productivity"] == 50.0
    assert _hour_metric(hour, "idleSeconds") == 1800
    assert _hour_metric(hour, "meetingSeconds") == 1800

def test_discord_meeting_hides_active_from_hourly_chart_without_replacing_summary_active_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 600,
            "idleSeconds": 600,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 600, "idleSeconds": 600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1200,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "activeSeconds") == 1200
    assert _hour_metric(author, "idleSeconds") == 0
    assert _hour_metric(author, "meetingSeconds") == 1200
    assert _hour_metric(summary["totals"], "activeSeconds") == 1200
    assert author["productivity"] == 100
    assert _hour_metric(hour, "activeSeconds") == 0
    assert _hour_metric(hour, "idleSeconds") == 0
    assert _hour_metric(hour, "meetingSeconds") == 1200

def test_discord_meeting_overlay_is_not_applied_twice_across_sources():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    for source in ("first-plugin", "second-plugin"):
        repo.db.daily_author_activity.insert_one(
            {
                "source": source,
                "author": "Future Artist",
                "projectId": source,
                "date": "2026-04-29",
                "activeSeconds": 0,
                "idleSeconds": 3600,
                "workWindowSeconds": 32400,
                "hourlyActivity": [
                    {"hour": 10, "activeSeconds": 0, "idleSeconds": 3600, "breakSeconds": 0, "overtimeActiveSeconds": 0}
                ],
            }
        )

    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "meetingSeconds": 1800,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 11, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "meetingSeconds") == 1800
    assert _hour_metric(author, "idleSeconds") == 3600
    assert _hour_metric(hour, "meetingSeconds") == 1800
    assert _hour_metric(hour, "idleSeconds") == 1800

def test_live_discord_meeting_session_is_included_in_summary():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "meetingSeconds") == 1200
    assert _hour_metric(hour, "meetingSeconds") == 1200

def test_live_discord_meeting_session_is_counted_with_empty_daily_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": [],
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "meetingSeconds") == 1200
    assert _hour_metric(summary["totals"], "meetingSeconds") == 1200
    assert _hour_metric(hour, "meetingSeconds") == 1200

def test_live_discord_closed_meeting_interval_is_counted_without_daily_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "Europe/Madrid"})
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 28, 22, 29, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 28, 23, 26, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 3420,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 29, 1, 0, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_0 = next(item for item in hourly if item["hour"] == 0)
    hour_1 = next(item for item in hourly if item["hour"] == 1)

    assert _hour_metric(author, "meetingSeconds") == 3420
    assert _hour_metric(summary["totals"], "meetingSeconds") == 3420
    assert _hour_metric(hour_0, "meetingSeconds") == 31 * 60
    assert _hour_metric(hour_1, "meetingSeconds") == 26 * 60

def test_live_discord_cross_midnight_meeting_fills_selected_local_day_hours():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "Europe/Madrid"})
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 30, 21, 46, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 30, 22, 29, tzinfo=dt.UTC),
            "date": "2026-04-30",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 43 * 60,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 30, 22, 29, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 30, 23, 26, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Madrid",
            "meetingSeconds": 57 * 60,
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-01",
        end_date="2026-05-01",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 1, 1, 0, tzinfo=dt.UTC),
    )
    hourly = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")["hourlyActivity"]
    hour_0 = next(item for item in hourly if item["hour"] == 0)
    hour_1 = next(item for item in hourly if item["hour"] == 1)

    assert _hour_metric(hour_0, "meetingSeconds") == 3600
    assert _hour_metric(hour_1, "meetingSeconds") == 26 * 60

def test_live_discord_meeting_session_marks_offline_author_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.break_events.insert_one(
        {
            "rawAuthor": "Future Artist",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "online"
    assert "stalePresence" not in author
    assert _hour_metric(author, "meetingSeconds") == 1200

def test_live_discord_meeting_session_compensates_idle_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "projectId": "future",
            "date": "2026-04-29",
            "activeSeconds": 600,
            "idleSeconds": 1500,
            "workWindowSeconds": 32400,
            "hourlyActivity": [
                {"hour": 10, "activeSeconds": 600, "idleSeconds": 1500, "breakSeconds": 0, "overtimeActiveSeconds": 0}
            ],
        }
    )
    repo.db.meeting_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hour = next(item for item in summary["hourlyActivityByAuthor"][0]["hourlyActivity"] if item["hour"] == 10)

    assert _hour_metric(author, "activeSeconds") == 1500
    assert _hour_metric(author, "idleSeconds") == 600
    assert _hour_metric(author, "meetingSeconds") == 900
    assert _hour_metric(hour, "activeSeconds") == 600
    assert _hour_metric(hour, "idleSeconds") == 600
    assert _hour_metric(hour, "meetingSeconds") == 900
    assert _hour_metric(summary["totals"], "idleSeconds") == 600

def test_discord_meeting_graph_uses_full_interval_bucket_over_active_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "displayName": "Evgeniy Dotsenko",
            "discordUserId": "123",
            "timeZoneId": "Europe/Sofia",
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Евгений Доценко",
            "projectId": "bike-rush-2",
            "date": "2026-05-01",
            "activeSeconds": 2597,
            "idleSeconds": 1003,
            "hourlyActivity": [
                {"hour": 17, "activeSeconds": 2597, "idleSeconds": 1003},
                {"hour": 18, "activeSeconds": 0, "idleSeconds": 396},
            ],
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 1, 14, 2, 50, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 14, 11, 5, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Sofia",
            "meetingSeconds": 495,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Евгений Доценко",
            "discordUserId": "123",
            "startedAt": dt.datetime(2026, 5, 1, 14, 27, 58, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 15, 3, 48, tzinfo=dt.UTC),
            "date": "2026-05-01",
            "timeZoneId": "Europe/Sofia",
            "meetingSeconds": 2150,
        }
    )

    summary = repo.activity_summary(start_date="2026-05-01", end_date="2026-05-01", now=dt.datetime(2026, 5, 1, 15, 30, tzinfo=dt.UTC))
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Евгений Доценко")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Евгений Доценко")["hourlyActivity"]
    hour_17 = next(item for item in hourly if item["hour"] == 17)
    hour_18 = next(item for item in hourly if item["hour"] == 18)

    assert _hour_metric(hour_17, "meetingSeconds") == 2417
    assert _hour_metric(hour_17, "activeSeconds") == 1183
    assert _hour_metric(hour_17, "idleSeconds") == 0
    assert _hour_metric(hour_18, "meetingSeconds") == 228
    assert _hour_metric(author, "activeSeconds") == 3828
    assert _hour_metric(author, "meetingSeconds") == 2645

def test_discord_voice_events_open_and_close_meeting_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    join = repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")
    leave = repo.record_discord_voice_event("123", "future", "leave", timestamp="2026-04-29T10:25:00+00:00")

    assert join["status"] == "meeting_started"
    assert leave["status"] == "meeting_closed"
    assert _hour_metric(leave, "meetingSeconds") == 1500
    assert repo.db.meeting_sessions.items == []
    assert _hour_metric(repo.db.meeting_intervals.items[0], "meetingSeconds") == 1500
    assert repo.db.report_rows.items[-1]["source"] == "discord"
    assert repo.db.report_rows.items[-1]["reportType"] == "meeting"


def test_discord_leave_closes_recent_unmatched_join_event():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_events.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "eventType": "join",
            "guildId": "guild",
            "channelId": "meeting",
            "timestamp": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "createdAt": dt.datetime(2026, 4, 29, 10, 0, 1, tzinfo=dt.UTC),
        }
    )

    leave = repo.record_discord_voice_event("123", "future", "leave", guild_id="guild", channel_id="meeting", timestamp="2026-04-29T10:00:02+00:00")

    assert leave["status"] == "meeting_closed"
    assert _hour_metric(leave, "meetingSeconds") == 2
    assert repo.db.meeting_sessions.items == []
    assert len(repo.db.meeting_intervals.items) == 1
    assert repo.db.meeting_intervals.items[0]["startedAt"] == dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC)
    assert repo.db.meeting_intervals.items[0]["endedAt"] == dt.datetime(2026, 4, 29, 10, 0, 2, tzinfo=dt.UTC)


def test_discord_join_does_not_leave_live_session_when_later_leave_already_exists():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_events.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "eventType": "leave",
            "guildId": "guild",
            "channelId": "meeting",
            "timestamp": dt.datetime(2026, 4, 29, 10, 0, 2, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "createdAt": dt.datetime(2026, 4, 29, 10, 0, 2, 100000, tzinfo=dt.UTC),
        }
    )

    join = repo.record_discord_voice_event("123", "future", "join", guild_id="guild", channel_id="meeting", timestamp="2026-04-29T10:00:00+00:00")

    assert join["status"] == "meeting_closed"
    assert _hour_metric(join, "meetingSeconds") == 2
    assert repo.db.meeting_sessions.items == []
    assert len(repo.db.meeting_intervals.items) == 1


def test_scoped_rebuild_replays_discord_meeting_events_without_stale_live_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_sessions.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "guildId": "guild",
            "channelId": "meeting",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )
    repo.db.meeting_events.insert_many(
        [
            {
                "discordUserId": "123",
                "discordUsername": "future",
                "rawAuthor": "Future Artist",
                "eventType": "join",
                "guildId": "guild",
                "channelId": "meeting",
                "timestamp": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
                "date": "2026-04-29",
                "timeZoneId": "UTC",
                "createdAt": dt.datetime(2026, 4, 29, 10, 0, 1, tzinfo=dt.UTC),
            },
            {
                "discordUserId": "123",
                "discordUsername": "future",
                "rawAuthor": "Future Artist",
                "eventType": "leave",
                "guildId": "guild",
                "channelId": "meeting",
                "timestamp": dt.datetime(2026, 4, 29, 10, 0, 2, tzinfo=dt.UTC),
                "date": "2026-04-29",
                "timeZoneId": "UTC",
                "createdAt": dt.datetime(2026, 4, 29, 10, 0, 2, 100000, tzinfo=dt.UTC),
            },
        ]
    )

    repo.rebuild_aggregates_for_dates("2026-04-29", dates=["2026-04-29"], authors=["Future Artist"])

    statuses = [item.get("discordStatus") for item in repo.db.report_rows.items if item.get("source") == "discord"]
    assert statuses == ["meeting_started", "meeting_closed"]
    assert repo.db.meeting_sessions.items == []
    assert len(repo.db.meeting_intervals.items) == 1
    assert repo.db.meeting_intervals.items[0]["meetingSeconds"] == 2


def test_live_summary_ignores_stale_discord_session_with_later_leave_event():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.meeting_sessions.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "guildId": "guild",
            "channelId": "meeting",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )
    repo.db.meeting_events.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "eventType": "leave",
            "guildId": "guild",
            "channelId": "meeting",
            "timestamp": dt.datetime(2026, 4, 29, 10, 0, 2, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
            "createdAt": dt.datetime(2026, 4, 29, 10, 0, 2, tzinfo=dt.UTC),
        }
    )

    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
    )

    author = next((item for item in summary["authors"] if item["rawAuthor"] == "Future Artist"), None)
    assert author is None or author.get("activeMeeting") is not True


def test_open_discord_meeting_does_not_create_live_report_rows_but_counts_live_time():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "sendIntervalSeconds": 300})
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    inserted = repo.materialize_live_meeting_reports(dt.datetime(2026, 4, 29, 10, 12, tzinfo=dt.UTC))

    live_reports = [item for item in repo.db.report_rows.items if item.get("discordStatus") == "meeting_live"]
    session = repo.db.meeting_sessions.find_one({"discordUserId": "123"})
    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 29, 10, 12, tzinfo=dt.UTC),
    )
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")

    assert inserted == 0
    assert live_reports == []
    assert session.get("lastLiveReportAt") is None
    assert repo.db.meeting_events.count_documents({"eventType": "live"}) == 0
    assert author["meetingSeconds"] == 720


def test_open_discord_meeting_live_time_survives_scoped_rebuild_without_live_rows():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "sendIntervalSeconds": 300})
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")
    repo.materialize_live_meeting_reports(dt.datetime(2026, 4, 29, 10, 12, tzinfo=dt.UTC))

    repo.rebuild_aggregates_for_dates("2026-04-29", dates=["2026-04-29"])

    live_reports = [item for item in repo.db.report_rows.items if item.get("discordStatus") == "meeting_live"]
    summary = repo.activity_summary(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=dt.datetime(2026, 4, 29, 10, 12, tzinfo=dt.UTC),
    )
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")

    assert live_reports == []
    assert author["meetingSeconds"] == 720


def test_scoped_rebuild_ignores_existing_live_discord_report_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.db.report_rows.insert_one(
        {
            "reportId": "discord-meeting-live:123:2026-04-29T10:05:00+00:00",
            "source": "discord",
            "pluginVersion": "discord-bot",
            "author": "Future Artist",
            "projectId": "discord",
            "sessionId": "123",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:05:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 5, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "reportType": "meeting",
            "activityType": "meeting_live",
            "discordEventType": "live",
            "discordStatus": "meeting_live",
            "discordUserId": "123",
            "discordUsername": "future",
            "meetingSeconds": 300,
            "metadata": {
                "guildId": "guild",
                "channelId": "channel",
                "live": True,
                "startedAt": "2026-04-29T10:00:00+00:00",
                "endedAt": "2026-04-29T10:05:00+00:00",
            },
        }
    )

    result = repo.rebuild_aggregates_for_dates("2026-04-29", dates=["2026-04-29"])

    live_events = repo.db.meeting_events.find({"eventType": "live"}).items
    live_reports = [item for item in repo.db.report_rows.items if item.get("discordStatus") == "meeting_live"]
    daily = repo.db.daily_author_activity.find_one({"source": "discord", "author": "Future Artist", "date": "2026-04-29"})

    assert result["backfilledLiveMeetingEvents"] == 0
    assert live_events == []
    assert live_reports == []
    assert daily is None


def test_plugin_reports_show_discord_start_and_end_without_live_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")
    open_page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", author="Future Artist")

    repo.record_discord_voice_event("123", "future", "leave", timestamp="2026-04-29T10:25:00+00:00")
    closed_page = repo.reports_page(start_date="2026-04-29", end_date="2026-04-29", author="Future Artist")

    open_statuses = [item.get("discordStatus") for item in open_page["reports"]]
    closed_statuses = [item.get("discordStatus") for item in closed_page["reports"]]

    assert open_statuses == ["meeting_started"]
    assert closed_statuses == ["meeting_closed", "meeting_started"]
    assert "meeting_live" not in closed_statuses


def test_discord_meeting_schedules_telegram_online_prompt_before_day_start():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )

    result = repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    assert result["status"] == "meeting_started"
    assert len(repo.db.telegram_online_prompts.items) == 1
    prompt = repo.db.telegram_online_prompts.items[0]
    assert prompt["rawAuthor"] == "Future Artist"
    assert prompt["date"] == "2026-04-29"
    assert prompt["telegramUsername"] == "future_artist"
    assert prompt["firstReportReceivedAt"] == dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC)

def test_discord_meeting_does_not_schedule_telegram_online_prompt_after_day_start():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "discordUserId": "123",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "date": "2026-04-29",
            "startedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )

    result = repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    assert result["status"] == "meeting_started"
    assert repo.db.telegram_online_prompts.items == []

def test_discord_auto_afk_closes_meeting_at_solo_start_and_schedules_notification():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.record_discord_voice_event("123", "future", "join", guild_id="guild", channel_id="meeting", timestamp="2026-04-29T10:00:00+00:00")

    result = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        guild_id="guild",
        meeting_channel_id="meeting",
        afk_channel_id="afk",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:00+00:00",
        threshold_seconds=60,
    )

    assert result["status"] == "meeting_auto_afk"
    assert _hour_metric(result, "meetingSeconds") == 1500
    assert repo.db.meeting_sessions.items == []
    assert repo.db.meeting_intervals.items[0]["endedAt"] == dt.datetime(2026, 4, 29, 10, 25, tzinfo=dt.UTC)
    assert _hour_metric(repo.db.meeting_intervals.items[0], "meetingSeconds") == 1500
    assert repo.db.meeting_events.items[-1]["eventType"] == "auto_afk"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["telegramUsername"] == "future_artist"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["excludedSeconds"] == 600
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["thresholdSeconds"] == 60

def test_discord_settings_default_and_save():
    repo = fake_repository()

    assert repo.get_discord_settings()["meetingAutoAfkTimeoutSeconds"] == 600

    result = repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=900,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=120,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="Custom summary prompt.",
    )

    assert result["meetingAutoAfkTimeoutSeconds"] == 900
    assert result["meetingSummariesEnabled"] is True
    assert result["meetingSummaryPrompt"] == "Custom summary prompt."
    assert result["meetingSummaryTelegramTemplate"] == DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE
    assert repo.get_discord_settings()["meetingAutoAfkTimeoutSeconds"] == 900

def test_discord_settings_include_default_meeting_summary_prompt():
    repo = fake_repository()

    prompt = repo.get_discord_settings()["meetingSummaryPrompt"]

    assert prompt == DEFAULT_MEETING_SUMMARY_PROMPT
    assert "{transcript}" not in prompt

def test_discord_settings_save_meeting_summary_telegram_template():
    repo = fake_repository()

    result = repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=900,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=120,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="Custom summary prompt.",
        meeting_summary_telegram_template="Summary for {date}\n{summary}",
    )

    assert result["meetingSummaryTelegramTemplate"] == "Summary for {date}\n{summary}"

def test_discord_auto_afk_is_idempotent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "discordUserId": "123",
            "timeZoneId": "UTC",
        }
    )
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    first = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:00+00:00",
    )
    second = repo.record_discord_meeting_auto_afk(
        "123",
        "future",
        solo_started_at="2026-04-29T10:25:00+00:00",
        moved_at="2026-04-29T10:35:30+00:00",
    )

    assert first["status"] == "meeting_auto_afk"
    assert second["status"] == "auto_afk_already_recorded"
    assert len(repo.db.meeting_intervals.items) == 1
    assert len(repo.db.telegram_meeting_auto_afk_notifications.items) == 1

def test_discord_recording_state_refreshes_settings_before_min_participant_check(monkeypatch):
    class FakeMember:
        id = 123
        name = "solo"
        bot = False

    class FakeVoiceChannel:
        members = [FakeMember()]

    client = MeetingClient.__new__(MeetingClient)
    client.config = type("Config", (), {"meeting_channel_id": 1})()
    client.recording = None
    client.meeting_summaries_enabled = True
    client.meeting_summary_min_participants = 1
    refresh_forces = []
    starts = []

    async def fake_refresh_settings_if_needed(*, force=False):
        refresh_forces.append(force)
        client.meeting_summary_min_participants = 5

    async def fake_start_recording(channel, human_members):
        starts.append(human_members)

    client.get_channel = lambda channel_id: FakeVoiceChannel()
    client.refresh_settings_if_needed = fake_refresh_settings_if_needed
    client.start_recording = fake_start_recording
    monkeypatch.setattr(discord_bot_module.discord, "VoiceChannel", FakeVoiceChannel)

    import asyncio

    asyncio.run(client.refresh_recording_state())

    assert refresh_forces == [True]
    assert starts == []

def test_discord_recording_fail_and_status_are_public_bot_paths():
    assert "/api/v1/discord/meeting-recordings/fail" in PUBLIC_API_PATHS
    assert "/api/v1/discord/meeting-recordings/status" in PUBLIC_API_PATHS

def test_discord_author_mappings_update_known_telegram_profiles_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko", "telegramUsername": "ama_deus"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats"})

    result = apply_discord_author_mappings(repo)
    evgeniy = repo.db.author_profiles.find_one({"telegramUsername": "ama_deus"})
    igor = repo.db.author_profiles.find_one({"telegramUsername": "igormats"})

    assert evgeniy["discordUserId"] == "645196366494171139"
    assert evgeniy["discordUsername"] == "Evgeniy Dotsenko"
    assert igor["discordUserId"] == "689526024857321504"
    assert igor["discordUsername"] == "Igor Mats"
    assert [item["telegramUsername"] for item in result["updated"]] == ["ama_deus", "igormats"]
    assert "dmitryshane" in result["missingTelegramUsernames"]
    assert "vedamir_infinum" in result["missingTelegramUsernames"]
    assert "zhdamarovich" in result["missingTelegramUsernames"]

def test_idle_only_reports_during_discord_meeting_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "meeting",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert [report["source"] for report in reports] == ["discord"]

def test_active_reports_during_discord_meeting_remain_visible_in_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 300,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.meeting_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert [report["source"] for report in reports] == ["future-plugin"]

def test_plugin_reports_during_break_interval_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:15:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 15, tzinfo=dt.UTC),
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 60,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "telegram",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "telegram",
            "telegramEventType": "afk",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:20:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 20, tzinfo=dt.UTC),
            "reportType": "meeting",
            "idleDeltaSeconds": 0,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.break_intervals.insert_one(
        {
            "rawAuthor": "Future Artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 4, 29, 10, 30, tzinfo=dt.UTC),
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert {report["source"] for report in reports} == {"discord", "telegram"}

def test_idle_only_reports_during_open_break_are_hidden_from_latest_reports():
    repo = fake_repository()
    repo.db.report_rows.insert_one(
        {
            "source": "future-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC),
            "idleDeltaSeconds": 300,
            "activeDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "startedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "date": "2026-04-29",
            "timeZoneId": "UTC",
        }
    )

    reports = repo.latest_reports(start_date="2026-04-29", end_date="2026-04-29")

    assert reports == []
