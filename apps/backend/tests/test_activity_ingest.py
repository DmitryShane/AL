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


def test_interval_settings_include_independent_idle_threshold():
    repo = fake_repository()

    assert repo.get_interval_settings()["defaultSendIntervalSeconds"] == 60
    assert repo.get_interval_settings()["idleThresholdSeconds"] == 300
    assert repo.get_interval_settings()["telegramOnlinePromptDelayMinutes"] == 15

    result = repo.upsert_interval_settings(
        default_send_interval_seconds=120,
        device_send_interval_seconds=5,
        idle_threshold_seconds=450,
        device_idle_threshold_seconds=1,
        plugin_ingest_enabled=None,
    )

    assert result["defaultSendIntervalSeconds"] == 120
    assert result["deviceSendIntervalSeconds"] == 5
    assert result["idleThresholdSeconds"] == 450
    assert result["deviceIdleThresholdSeconds"] == 1
    assert result["pluginIngestEnabled"] is True

def test_interval_settings_include_global_plugin_ingest_toggle():
    repo = fake_repository()

    result = repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=False,
    )

    assert result["pluginIngestEnabled"] is False
    assert repo.get_plugin_ingest_enabled() is False
    assert repo.is_plugin_enabled_for_author("Future Artist") is False

def test_interval_settings_store_plugin_ingest_resume_timestamp_when_re_enabled():
    repo = fake_repository()

    repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=False,
    )
    repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=True,
    )

    doc = repo.db.system_settings.find_one({"kind": "plugins"}) or {}
    assert doc.get("pluginIngestResumedAtUtc") is not None

def test_save_event_batch_drops_events_before_global_plugin_ingest_resume_cutoff():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    cutoff = dt.datetime(2026, 5, 2, 8, 3, 0, tzinfo=dt.UTC)
    repo.db.system_settings.insert_one(
        {
            "kind": "plugins",
            "pluginIngestEnabled": True,
            "pluginIngestResumedAtUtc": cutoff,
        }
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    received_at = dt.datetime(2026, 5, 2, 8, 10, tzinfo=dt.UTC)
    payload = {
        "author": "Future Artist",
        "authorEmail": "future@example.com",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "old-1",
                "eventType": "focus",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:00:00Z",
                "occurredAtLocal": "2026-05-02T08:00:00+00:00",
            },
            {
                "eventId": "new-1",
                "eventType": "file_saved",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:04:00Z",
                "occurredAtLocal": "2026-05-02T08:04:00+00:00",
            },
        ],
    }
    repo._save_event_batch("cur", "1.0.0", payload, "raw-1", "auto", received_at, "challenge-1", None)
    assert len(repo.db.raw_activity_events.items) == 1
    assert repo.db.raw_activity_events.items[0]["eventId"] == "new-1"

def test_save_event_batch_drops_events_before_author_plugin_ingest_resume_cutoff():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    cutoff = dt.datetime(2026, 5, 2, 8, 3, 0, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "pluginIngestResumedAtUtc": cutoff,
        }
    )
    received_at = dt.datetime(2026, 5, 2, 8, 10, tzinfo=dt.UTC)
    payload = {
        "author": "Future Artist",
        "authorEmail": "future@example.com",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "old-1",
                "eventType": "focus",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:00:00Z",
                "occurredAtLocal": "2026-05-02T08:00:00+00:00",
            },
            {
                "eventId": "new-1",
                "eventType": "file_saved",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:04:00Z",
                "occurredAtLocal": "2026-05-02T08:04:00+00:00",
            },
        ],
    }
    repo._save_event_batch("cur", "1.0.0", payload, "raw-1", "auto", received_at, "challenge-1", None)
    assert len(repo.db.raw_activity_events.items) == 1
    assert repo.db.raw_activity_events.items[0]["eventId"] == "new-1"

def test_save_event_batch_uses_latest_of_global_and_author_plugin_ingest_resume_cutoffs():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    global_cutoff = dt.datetime(2026, 5, 2, 8, 3, 0, tzinfo=dt.UTC)
    author_cutoff = dt.datetime(2026, 5, 2, 8, 5, 0, tzinfo=dt.UTC)
    repo.db.system_settings.insert_one(
        {
            "kind": "plugins",
            "pluginIngestEnabled": True,
            "pluginIngestResumedAtUtc": global_cutoff,
        }
    )
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "pluginIngestResumedAtUtc": author_cutoff,
        }
    )
    received_at = dt.datetime(2026, 5, 2, 8, 10, tzinfo=dt.UTC)
    payload = {
        "author": "Future Artist",
        "authorEmail": "future@example.com",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "between-1",
                "eventType": "focus",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:04:00Z",
                "occurredAtLocal": "2026-05-02T08:04:00+00:00",
            },
            {
                "eventId": "after-both-1",
                "eventType": "file_saved",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:06:00Z",
                "occurredAtLocal": "2026-05-02T08:06:00+00:00",
            },
        ],
    }
    repo._save_event_batch("cur", "1.0.0", payload, "raw-1", "auto", received_at, "challenge-1", None)
    assert len(repo.db.raw_activity_events.items) == 1
    assert repo.db.raw_activity_events.items[0]["eventId"] == "after-both-1"

def test_interval_settings_persist_telegram_online_prompt_delay_minutes():
    repo = fake_repository()

    result = repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=None,
        telegram_online_prompt_delay_minutes=42,
    )

    assert result["telegramOnlinePromptDelayMinutes"] == 42
    assert repo.get_interval_settings()["telegramOnlinePromptDelayMinutes"] == 42
    assert repo.get_telegram_online_prompt_delay_seconds() == 42 * 60

def test_submit_report_ignores_without_writes_when_global_plugin_ingest_disabled():
    from al_backend.models import ReportIn
    from al_backend.routers.reports import submit_report

    repo = fake_repository()
    repo.db.system_settings.insert_one({"kind": "plugins", "pluginIngestEnabled": False})

    result = submit_report(
        ReportIn(source="cur", pluginVersion="1.0.0", challengeId="challenge-1", encryptedPacket="not-decoded"),
        service=repo,
    )

    assert result.ok
    assert result.ignored
    assert result.report_id == ""
    assert repo.db.raw_reports.items == []
    assert repo.db.raw_event_batches.items == []
    assert repo.db.raw_activity_events.items == []
    assert repo.db.activity_snapshots.items == []
    assert repo.db.report_rows.items == []

def test_raw_event_state_isolated_by_source_project_and_device():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T01:19:47.078Z",
            "occurredAtLocal": "2026-05-02T03:19:47.078+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 1, 20, 2, tzinfo=dt.UTC),
        }
    )

    unity_deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T02:08:31.280916Z",
            "occurredAtLocal": "2026-05-02T04:08:31.280916+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 8, 31, 816000, tzinfo=dt.UTC),
        }
    )

    assert unity_deltas["idleDeltaSeconds"] == 0
    assert unity_deltas["activeDeltaSeconds"] == 0
    aggregate_state_items = getattr(repo.db.aggregate_session_state, "items")
    assert len(aggregate_state_items) == 3
    assert {
        item["_id"] for item in aggregate_state_items
    } == {
        "author_source_project_device_day_v2|Future Artist|2026-05-02|cur|al|mac-mini",
        "author_source_project_device_day_v2|Future Artist|2026-05-02|ual|bike-rush-2|mac-mini",
        "author_day_activity_v1|Future Artist|2026-05-02",
    }

    unity_daily = repo.db.daily_author_activity.find_one(
        {"author": "Future Artist", "date": "2026-05-02", "source": "ual", "projectId": "bike-rush-2"}
    )
    assert unity_daily is not None
    assert unity_daily["idleSeconds"] == 0

def test_late_activity_event_does_not_roll_back_raw_event_state():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T02:00:00Z",
            "occurredAtLocal": "2026-05-02T04:00:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 0, tzinfo=dt.UTC),
        }
    )
    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T02:10:00Z",
            "occurredAtLocal": "2026-05-02T04:10:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 10, tzinfo=dt.UTC),
        }
    )

    late_deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T02:05:00Z",
            "occurredAtLocal": "2026-05-02T04:05:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 12, tzinfo=dt.UTC),
        }
    )

    assert late_deltas["activeDeltaSeconds"] == 0
    assert late_deltas["idleDeltaSeconds"] == 0
    assert late_deltas["activityCountDeltas"] == [{"type": "focus", "count": 1}]

    state = repo.db.aggregate_session_state.find_one(
        {"_id": "author_source_project_device_day_v2|Future Artist|2026-05-02|cur|al|mac-mini"}
    )
    assert state is not None
    state_values = state["state"]
    assert state_values["lastActivityAt"] == "2026-05-02T02:10:00+00:00"
    assert state_values["lastAccountingAt"] == "2026-05-02T02:10:00+00:00"

def test_heartbeat_idle_still_counts_within_same_raw_event_source_state():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "deviceId": "mac-mini",
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
            "deviceId": "mac-mini",
            "date": "2026-04-28",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-04-28T18:10:00Z",
            "occurredAtLocal": "2026-04-28T18:10:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 600
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})
    assert daily is not None
    assert daily["idleSeconds"] == 600

def test_device_click_counts_as_activity_and_heartbeat_counts_idle():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})

    click_deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "deviceId": "advertising-id-1",
            "date": "2026-05-04",
            "eventType": "click",
            "occurredAtUtc": "2026-05-04T10:00:00Z",
            "occurredAtLocal": "2026-05-04T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
        }
    )

    idle_deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "deviceId": "advertising-id-1",
            "date": "2026-05-04",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-04T10:10:00Z",
            "occurredAtLocal": "2026-05-04T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 10, tzinfo=dt.UTC),
        }
    )

    assert click_deltas["activityCountDeltas"] == [{"type": "click", "count": 1}]
    assert idle_deltas["idleDeltaSeconds"] == 600
    daily = repo.db.daily_author_activity.find_one({"author": "Device1", "date": "2026-05-04", "source": "dev"})
    assert daily is not None
    assert daily["idleSeconds"] == 600

def test_device_hold_counts_as_activity_during_long_press():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "idleThresholdSeconds": 300, "deviceIdleThresholdSeconds": 10}
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})

    base_event = {
        "source": "dev",
        "author": "Device1",
        "projectId": "Bike Rush 2",
        "deviceId": "advertising-id-1",
        "date": "2026-05-04",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "click",
            "occurredAtUtc": "2026-05-04T10:00:00Z",
            "occurredAtLocal": "2026-05-04T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
        }
    )

    hold_deltas = []

    for second in (5, 10, 15):
        hold_deltas.append(
            repo._apply_raw_event_to_aggregates(
                {
                    **base_event,
                    "eventType": "hold",
                    "occurredAtUtc": f"2026-05-04T10:00:{second:02d}Z",
                    "occurredAtLocal": f"2026-05-04T10:00:{second:02d}+00:00",
                    "receivedAt": dt.datetime(2026, 5, 4, 10, 0, second, tzinfo=dt.UTC),
                    "metadata": {"holdDurationSeconds": second, "touchCount": 1},
                }
            )
        )

    assert [item["idleDeltaSeconds"] for item in hold_deltas] == [0, 0, 0]
    assert [item["activeDeltaSeconds"] for item in hold_deltas] == [5, 5, 5]
    assert hold_deltas[-1]["activityCountDeltas"] == [{"type": "hold", "count": 1}]
    daily = repo.db.daily_author_activity.find_one({"author": "Device1", "date": "2026-05-04", "source": "dev"})
    assert daily is not None
    assert daily["activeSeconds"] == 15
    assert daily["idleSeconds"] == 0
    assert {item["type"]: item["count"] for item in daily["activityCounts"]} == {"click": 1, "hold": 3}

def test_device_hold_duration_counts_each_held_second_even_when_reports_exceed_threshold():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "idleThresholdSeconds": 300, "deviceIdleThresholdSeconds": 5}
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})

    base_event = {
        "source": "dev",
        "author": "Device1",
        "projectId": "Bike Rush 2",
        "deviceId": "advertising-id-1",
        "date": "2026-05-04",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "click",
            "occurredAtUtc": "2026-05-04T02:00:01Z",
            "occurredAtLocal": "2026-05-04T02:00:01+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 2, 0, 1, tzinfo=dt.UTC),
        }
    )
    hold_deltas = []

    for offset, duration in ((6, 5.01), (11, 10.02), (16, 15.03)):
        hold_deltas.append(
            repo._apply_raw_event_to_aggregates(
                {
                    **base_event,
                    "eventType": "hold",
                    "occurredAtUtc": f"2026-05-04T02:00:{offset:02d}Z",
                    "occurredAtLocal": f"2026-05-04T02:00:{offset:02d}+00:00",
                    "receivedAt": dt.datetime(2026, 5, 4, 2, 0, offset, tzinfo=dt.UTC),
                    "metadata": {
                        "holdDurationSeconds": duration,
                        "firstHoldAtUtc": "2026-05-04T02:00:01Z",
                        "touchCount": 1,
                    },
                }
            )
        )

    assert [item["overtimeActiveDeltaSeconds"] for item in hold_deltas] == [5, 5, 5]
    daily = repo.db.daily_author_activity.find_one({"author": "Device1", "date": "2026-05-04", "source": "dev"})
    assert daily is not None
    assert daily["overtimeActiveSeconds"] == 15
    assert daily["idleSeconds"] == 0

def test_device_editor_activity_counts_for_evgeniy_dotsenko_alias():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Evgeniy Dotsenko", "displayName": "Evgeniy Dotsenko"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device1", "targetRawAuthor": "Evgeniy Dotsenko"})

    repo.save_report(
        source="dev",
        plugin_version="0.1.0",
        encrypted_packet="packet",
        challenge_id="challenge",
        device_id="editor-device-1",
        payload={
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "sessionId": "editor-session",
            "deviceId": "editor-device-1",
            "events": [
                {
                    "eventId": "editor-click-1",
                    "eventType": "click",
                    "occurredAtUtc": "2026-05-04T10:00:00Z",
                    "occurredAtLocal": "2026-05-04T10:00:00+00:00",
                    "date": "2026-05-04",
                    "metadata": {"isEditor": True, "isEditorPlayMode": True},
                },
                {
                    "eventId": "editor-hold-1",
                    "eventType": "hold",
                    "occurredAtUtc": "2026-05-04T10:00:05Z",
                    "occurredAtLocal": "2026-05-04T10:00:05+00:00",
                    "date": "2026-05-04",
                    "metadata": {"isEditor": True, "isEditorPlayMode": True, "holdDurationSeconds": 5},
                },
            ],
        },
    )

    raw_events = list(repo.db.raw_activity_events.find({"author": "Evgeniy Dotsenko"}))
    assert len(raw_events) == 2
    daily = repo.db.daily_author_activity.find_one(
        {"author": "Evgeniy Dotsenko", "date": "2026-05-04", "source": "dev"}
    )
    assert daily is not None
    assert daily["activeSeconds"] == 5
    assert daily["idleSeconds"] == 0
    assert {item["type"]: item["count"] for item in daily["activityCounts"]} == {"click": 1, "hold": 1}
    assert repo.db.report_rows.count_documents({"author": "Evgeniy Dotsenko", "source": "dev"}) == 1

def test_device_editor_activity_is_raw_only_for_other_authors():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Other Author", "displayName": "Other Author"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device2", "targetRawAuthor": "Other Author"})

    repo.save_report(
        source="dev",
        plugin_version="0.1.0",
        encrypted_packet="packet",
        challenge_id="challenge",
        device_id="editor-device-2",
        payload={
            "source": "dev",
            "author": "Device2",
            "projectId": "Bike Rush 2",
            "sessionId": "editor-session",
            "deviceId": "editor-device-2",
            "events": [
                {
                    "eventId": "editor-click-2",
                    "eventType": "click",
                    "occurredAtUtc": "2026-05-04T10:00:00Z",
                    "occurredAtLocal": "2026-05-04T10:00:00+00:00",
                    "date": "2026-05-04",
                    "metadata": {"isEditor": True, "isEditorPlayMode": True},
                },
                {
                    "eventId": "editor-hold-2",
                    "eventType": "hold",
                    "occurredAtUtc": "2026-05-04T10:00:05Z",
                    "occurredAtLocal": "2026-05-04T10:00:05+00:00",
                    "date": "2026-05-04",
                    "metadata": {"isEditor": True, "isEditorPlayMode": True, "holdDurationSeconds": 5},
                },
            ],
        },
    )

    raw_events = list(repo.db.raw_activity_events.find({"author": "Other Author"}))
    assert len(raw_events) == 2
    assert repo.db.daily_author_activity.find_one({"author": "Other Author", "source": "dev"}) is None
    assert repo.db.report_rows.count_documents({"author": "Other Author", "source": "dev"}) == 0

def test_device_summary_uses_application_name_as_saved_item():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "date": "2026-05-04",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "click", "count": 3}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-05-04", end_date="2026-05-04")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Device1")
    source_group = next(item for item in author["savedPrefabsBySource"] if item["source"] == "dev")

    assert source_group["savedPrefabs"] == [
        {"path": "device:bike rush 2", "name": "Bike Rush 2", "projectId": "Bike Rush 2", "saveCount": 3}
    ]

def test_device_report_author_is_stable_for_same_device_id():
    repo = fake_repository()

    first = repo.resolve_device_report_author("dev", "advertising-id-1")
    second = repo.resolve_device_report_author("dev", "advertising-id-1")
    third = repo.resolve_device_report_author("dev", "advertising-id-2")

    assert first == "Device1"
    assert second == "Device1"
    assert third == "Device2"

def test_device_report_author_ignores_zero_advertising_id():
    repo = fake_repository()

    assert repo.resolve_device_report_author("dev", "00000000-0000-0000-0000-000000000000") == "Device"
    assert repo.db.device_report_identities.count_documents({"source": "dev"}) == 0

def test_device_report_uses_fallback_id_when_advertising_id_is_zero():
    repo = fake_repository()

    repo.save_report(
        source="dev",
        plugin_version="0.1.0",
        encrypted_packet="packet",
        challenge_id="challenge",
        device_id="00000000-0000-0000-0000-000000000000",
        payload={
            "source": "dev",
            "author": "",
            "projectId": "Bike Rush 2",
            "sessionId": "session-1",
            "deviceId": "00000000-0000-0000-0000-000000000000",
            "events": [
                {
                    "eventId": "device-click-1",
                    "eventType": "click",
                    "occurredAtUtc": "2026-05-04T10:00:00Z",
                    "occurredAtLocal": "2026-05-04T10:00:00+00:00",
                    "date": "2026-05-04",
                    "metadata": {"deviceFallbackId": "keychain-device-id"},
                }
            ],
        },
    )

    raw_report = repo.db.raw_reports.items[0]
    raw_event = repo.db.raw_activity_events.items[0]

    assert raw_report["deviceId"] == "keychain-device-id"
    assert raw_event["deviceId"] == "keychain-device-id"
    assert raw_event["author"] == "Device1"
    assert repo.db.device_report_identities.items[0]["rawAuthor"] == "Device1"

def test_author_profiles_exclude_device_only_profiles():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    repo.db.device_report_identities.insert_one(
        {"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"}
    )
    repo.db.raw_event_batches.insert_one(
        {
            "source": "dev",
            "author": "Device1",
            "deviceId": "keychain-device-id",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
        }
    )

    assert all(item["rawAuthor"] != "Device1" for item in repo.author_profiles())


def test_author_profiles_exclude_stale_device_named_rows_without_identity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device7", "displayName": "Device7"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})

    raw_authors = {item["rawAuthor"] for item in repo.author_profiles()}

    assert "Device7" not in raw_authors
    assert "Dmitry Shane" in raw_authors

def test_device_source_uses_device_idle_threshold():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "idleThresholdSeconds": 300, "deviceIdleThresholdSeconds": 60}
    )
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "deviceId": "advertising-id-1",
            "date": "2026-05-04",
            "eventType": "click",
            "occurredAtUtc": "2026-05-04T10:00:00Z",
            "occurredAtLocal": "2026-05-04T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 0, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "deviceId": "advertising-id-1",
            "date": "2026-05-04",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-04T10:01:30Z",
            "occurredAtLocal": "2026-05-04T10:01:30+00:00",
            "receivedAt": dt.datetime(2026, 5, 4, 10, 1, 30, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 90

def test_author_local_today_includes_explicit_ui_date_for_device_local_timezone():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "date": "2026-05-05",
            "timeZoneId": "Local",
            "activeSeconds": 120,
            "idleSeconds": 60,
            "activityCounts": [{"type": "click", "count": 2}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-05",
        end_date="2026-05-05",
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 5, 4, 22, 15, tzinfo=dt.UTC),
    )
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Device1")

    assert author["activeSeconds"] == 120
    assert author["rawPluginDaySeconds"] == 180


def test_activity_summary_hides_deleted_device_profile_authors():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev",
            "author": "Device12",
            "projectId": "Bike Rush 2",
            "date": "2026-05-10",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.raw_activity_events.insert_one({"source": "dev", "author": "Device12", "date": "2026-05-10"})

    summary = repo.activity_summary(start_date="2026-05-10", end_date="2026-05-10")

    assert "Device12" not in {author["rawAuthor"] for author in summary["authors"]}
    assert "Device12" not in {author["rawAuthor"] for author in summary["hourlyActivityByAuthor"]}

def test_heartbeat_idle_does_not_account_entire_delivery_gap_when_huge():
    repo = fake_repository()
    set_idle_threshold(repo, 120)

    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-02T01:18:47Z",
            "occurredAtLocal": "2026-05-02T03:18:47+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 1, 18, 47, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-02T02:06:53Z",
            "occurredAtLocal": "2026-05-02T04:06:53+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 10, 54, tzinfo=dt.UTC),
        }
    )

    assert deltas["idleDeltaSeconds"] == 240
    assert deltas["activeDeltaSeconds"] == 0

def test_rebuild_keeps_cross_source_idle_out_of_unity_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "cursor-focus",
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 5, 2, 1, 19, 47, 78000, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-05-02T03:19:47.078+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 1, 20, 2, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "unity-focus",
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 5, 2, 2, 8, 31, 280916, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-05-02T04:08:31.280916+02:00",
            "receivedAt": dt.datetime(2026, 5, 2, 2, 8, 31, 816000, tzinfo=dt.UTC),
        }
    )

    repo.rebuild_aggregates_if_needed(force=True)

    report_rows = getattr(repo.db.report_rows, "items")
    assert not [
        row
        for row in report_rows
        if row.get("source") == "ual" and row.get("idleDeltaSeconds", 0) > 0
    ]
    unity_daily = repo.db.daily_author_activity.find_one(
        {"author": "Future Artist", "date": "2026-05-02", "source": "ual", "projectId": "bike-rush-2"}
    )
    assert unity_daily is not None
    assert unity_daily["idleSeconds"] == 0

def test_scoped_rebuild_rebuilds_only_selected_date():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-05-01",
            "activeSeconds": 123,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-05-01",
            "activeDeltaSeconds": 123,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-05-02",
            "activeSeconds": 999,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "date": "2026-05-02",
            "activeDeltaSeconds": 999,
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "scoped-focus",
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-05-02T08:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 2, 8, 0, 1, tzinfo=dt.UTC),
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "scoped-save",
            "source": "cur",
            "author": "Future Artist",
            "projectId": "al",
            "deviceId": "mac-mini",
            "date": "2026-05-02",
            "eventType": "file_saved",
            "occurredAtUtc": dt.datetime(2026, 5, 2, 8, 2, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-05-02T08:02:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 2, 8, 2, 1, tzinfo=dt.UTC),
        }
    )

    result = repo.rebuild_aggregates_for_dates("2026-05-02")

    previous_day = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-01"})
    rebuilt_day = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    previous_rows = list(repo.db.report_rows.find({"author": "Future Artist", "date": "2026-05-01"}))
    rebuilt_rows = list(repo.db.report_rows.find({"author": "Future Artist", "date": "2026-05-02", "source": "cur"}))

    assert result["dates"] == ["2026-05-02"]
    assert previous_day["activeSeconds"] == 123
    assert len(previous_rows) == 1
    assert rebuilt_day["activeSeconds"] == 120
    assert len(rebuilt_rows) == 1
    assert rebuilt_rows[0]["activeDeltaSeconds"] == 120
    assert repo.db.aggregate_day_state.find_one({"author": "Future Artist", "date": "2026-05-02"}) is not None

def test_scoped_rebuild_can_limit_authors():
    repo = fake_repository()
    set_idle_threshold(repo, 300)

    for author in ("Future Artist", "Other Artist"):
        repo.db.author_profiles.insert_one({"rawAuthor": author, "displayName": author})
        repo.db.daily_author_activity.insert_one(
            {
                "source": "cur",
                "author": author,
                "projectId": "al",
                "date": "2026-05-02",
                "activeSeconds": 999,
            }
        )
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"{author}-focus",
                "source": "cur",
                "author": author,
                "projectId": "al",
                "deviceId": "mac-mini",
                "date": "2026-05-02",
                "eventType": "focus",
                "occurredAtUtc": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
                "occurredAtLocal": "2026-05-02T08:00:00+00:00",
                "receivedAt": dt.datetime(2026, 5, 2, 8, 0, 1, tzinfo=dt.UTC),
            }
        )
        repo.db.raw_activity_events.insert_one(
            {
                "eventId": f"{author}-save",
                "source": "cur",
                "author": author,
                "projectId": "al",
                "deviceId": "mac-mini",
                "date": "2026-05-02",
                "eventType": "file_saved",
                "occurredAtUtc": dt.datetime(2026, 5, 2, 8, 2, tzinfo=dt.UTC),
                "occurredAtLocal": "2026-05-02T08:02:00+00:00",
                "receivedAt": dt.datetime(2026, 5, 2, 8, 2, 1, tzinfo=dt.UTC),
            }
        )

    repo.rebuild_aggregates_for_dates("2026-05-02", authors=["Future Artist"])

    rebuilt_author = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02"})
    untouched_author = repo.db.daily_author_activity.find_one({"author": "Other Artist", "date": "2026-05-02"})

    assert rebuilt_author["activeSeconds"] == 120
    assert untouched_author["activeSeconds"] == 999

def test_rebuild_restores_raw_event_batches_as_single_report_rows():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    received_at = dt.datetime(2026, 5, 2, 8, 6, tzinfo=dt.UTC)
    payload = {
        "author": "Future Artist",
        "authorEmail": "future@example.com",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "focus-1",
                "eventType": "focus",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:00:00Z",
                "occurredAtLocal": "2026-05-02T08:00:00+00:00",
            },
            {
                "eventId": "save-1",
                "eventType": "file_saved",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:00:30Z",
                "occurredAtLocal": "2026-05-02T08:00:30+00:00",
            },
            {
                "eventId": "heartbeat-1",
                "eventType": "heartbeat",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T08:05:00Z",
                "occurredAtLocal": "2026-05-02T08:05:00+00:00",
            },
        ],
    }
    repo._save_event_batch("cur", "1.0.0", payload, "raw-1", "auto", received_at, "challenge-1", None)
    original_rows = list(repo.db.report_rows.items)

    assert len(original_rows) == 1
    assert len(repo.db.raw_activity_events.items) == 3

    repo.rebuild_aggregates_if_needed(force=True)

    rebuilt_rows = repo.db.report_rows.items
    assert len(rebuilt_rows) == 1
    assert rebuilt_rows[0]["batchId"] == original_rows[0]["batchId"]
    assert rebuilt_rows[0]["activeDeltaSeconds"] == original_rows[0]["activeDeltaSeconds"]
    assert rebuilt_rows[0]["idleDeltaSeconds"] == original_rows[0]["idleDeltaSeconds"]
    assert rebuilt_rows[0]["lastRecordedAt"] == original_rows[0]["lastRecordedAt"]

def test_full_rebuild_does_not_schedule_telegram_online_prompts_from_snapshots():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.db.activity_snapshots.insert_one(
        {
            "source": "ual",
            "pluginVersion": "1",
            "author": "Future Artist",
            "authorEmail": "future@example.com",
            "projectId": "unity",
            "sessionId": "session",
            "deviceId": "device",
            "date": "2026-05-02",
            "recordedAt": "2026-05-02T08:00:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-02T08:00:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [{"type": "focus", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    repo.rebuild_aggregates_if_needed(force=True)

    assert repo.db.report_rows.items
    assert repo.db.telegram_online_prompts.items == []

def test_scoped_rebuild_does_not_schedule_telegram_prompts_from_snapshots():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "date": "2026-05-02",
            "startedAt": dt.datetime(2026, 5, 2, 8, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
        }
    )
    repo.db.activity_snapshots.insert_one(
        {
            "source": "ual",
            "pluginVersion": "1",
            "author": "Future Artist",
            "authorEmail": "future@example.com",
            "projectId": "unity",
            "sessionId": "session",
            "deviceId": "device",
            "date": "2026-05-02",
            "recordedAt": "2026-05-02T09:05:00+00:00",
            "receivedAt": dt.datetime(2026, 5, 2, 9, 5, tzinfo=dt.UTC),
            "lastRecordedAt": "2026-05-02T09:05:00+00:00",
            "lastReceivedAt": dt.datetime(2026, 5, 2, 9, 5, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [{"type": "focus", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    repo.rebuild_aggregates_for_dates("2026-05-02", dates=["2026-05-02"], authors=["Future Artist"])

    assert repo.db.report_rows.items
    assert repo.db.telegram_online_prompts.items == []
    assert repo.db.telegram_break_activity_prompts.items == []

def test_cross_midnight_raw_event_batch_splits_report_rows_by_local_date():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "telegramUsername": "future_artist",
            "timeZoneId": "UTC",
        }
    )
    repo.record_break_event("future_artist", "online", "2026-05-02T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-05-02T23:59:40Z")
    payload = {
        "author": "Future Artist",
        "authorEmail": "future@example.com",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "focus-before-midnight",
                "eventType": "focus",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T23:59:30Z",
                "occurredAtLocal": "2026-05-02T23:59:30+00:00",
            },
            {
                "eventId": "save-before-midnight",
                "eventType": "file_saved",
                "date": "2026-05-02",
                "occurredAtUtc": "2026-05-02T23:59:50Z",
                "occurredAtLocal": "2026-05-02T23:59:50+00:00",
            },
            {
                "eventId": "focus-after-midnight",
                "eventType": "focus",
                "date": "2026-05-03",
                "occurredAtUtc": "2026-05-03T00:00:10Z",
                "occurredAtLocal": "2026-05-03T00:00:10+00:00",
            },
            {
                "eventId": "save-after-midnight",
                "eventType": "file_saved",
                "date": "2026-05-03",
                "occurredAtUtc": "2026-05-03T00:00:20Z",
                "occurredAtLocal": "2026-05-03T00:00:20+00:00",
            },
        ],
    }

    repo._save_event_batch("cur", "1.0.0", payload, "raw-1", "auto", dt.datetime(2026, 5, 3, 0, 0, 30, tzinfo=dt.UTC), "challenge-1", None)

    rows_by_date = {row["date"]: row for row in repo.db.report_rows.items if row.get("source") == "cur"}
    assert sorted(rows_by_date) == ["2026-05-02", "2026-05-03"]
    assert rows_by_date["2026-05-02"]["overtimeActiveDeltaSeconds"] == 10
    assert rows_by_date["2026-05-03"]["overtimeActiveDeltaSeconds"] == 0
    assert rows_by_date["2026-05-03"]["activeDeltaSeconds"] == 10

    repo.rebuild_aggregates_if_needed(force=True)

    rebuilt_rows_by_date = {row["date"]: row for row in repo.db.report_rows.items if row.get("source") == "cur"}
    assert rebuilt_rows_by_date["2026-05-03"]["overtimeActiveDeltaSeconds"] == 0

def test_cursor_activity_project_appears_in_saved_files_without_file_save():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "scene_changed", "count": 3}],
            "savedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [{"path": "Assets/Project/Levels/Level.008/Level.008.prefab", "name": "Level.008", "saveCount": 1}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert {"path": "cursor:AL", "name": "AL", "projectId": "AL", "saveCount": 3} in author["savedPrefabs"]
    assert {
        "path": "Assets/Project/Levels/Level.008/Level.008.prefab",
        "name": "Level.008",
        "saveCount": 1,
    } in author["savedPrefabs"]

def test_activity_summary_author_source_uses_latest_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "fch",
            "author": "Future Artist",
            "projectId": "figma",
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
            "source": "fch",
            "pluginVersion": "figma-plugin",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "discord",
            "pluginVersion": "discord-bot",
            "author": "Future Artist",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.UTC),
            "reportType": "meeting",
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["source"] == "discord"
    assert author["pluginVersion"] == "discord-bot"
    assert author["lastRecordedAt"] == "2026-04-29T10:00:00+00:00"

def test_activity_before_telegram_overtime_reminder_stays_normal_during_rebuild():
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
            "occurredAtUtc": "2026-04-28T19:20:00Z",
            "occurredAtLocal": "2026-04-28T19:20:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 20, tzinfo=dt.UTC),
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
            "occurredAtUtc": "2026-04-28T19:20:30Z",
            "occurredAtLocal": "2026-04-28T19:20:30+00:00",
            "receivedAt": dt.datetime(2026, 4, 28, 19, 20, 30, tzinfo=dt.UTC),
        }
    )
    daily = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-04-28", "source": "ual"})

    assert deltas["activeDeltaSeconds"] == 30
    assert deltas["overtimeActiveDeltaSeconds"] == 0
    assert daily["activeSeconds"] == 30
    assert daily["overtimeActiveSeconds"] == 0

def test_zero_delta_event_save_report_does_not_touch_last_raw_report_received_at():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "HB Author", "displayName": "HB Author"})
    payload = {
        "author": "HB Author",
        "projectId": "figma",
        "sessionId": "0885fd320d3941aab3469ed2dc50d199",
        "timeZoneId": "Europe/Madrid",
        "timeZoneDisplayName": "Europe/Madrid",
        "events": [
            {
                "eventId": "evt-heartbeat-one",
                "eventType": "heartbeat",
                "occurredAtUtc": "2026-05-04T01:07:52.472Z",
                "occurredAtLocal": "2026-05-04T03:07:52.472+02:00",
            },
            {
                "eventId": "evt-heartbeat-two",
                "eventType": "heartbeat",
                "occurredAtUtc": "2026-05-04T01:08:52.454Z",
                "occurredAtLocal": "2026-05-04T03:08:52.454+02:00",
            },
        ],
    }

    repo.save_report("fch", "0.1.0", "packet", payload, "challenge")

    profile = repo.db.author_profiles.find_one({"rawAuthor": "HB Author"})
    assert profile is not None
    assert profile.get("lastRawReportReceivedAt") is None
    assert repo.db.report_rows.items == []
    assert len(repo.db.raw_reports.items) == 1
    assert len(repo.db.raw_event_batches.items) == 1
    assert len(repo.db.raw_activity_events.items) == 2

def test_activity_summary_keeps_mix_and_saved_files_per_author():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 60,
            "idleSeconds": 0,
            "activityCounts": [{"type": "selection", "count": 3}],
            "savedPrefabs": [{"path": "Assets/Dmitry.prefab", "name": "Dmitry", "saveCount": 2}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "author": "Igor Mats",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "play_mode", "count": 5}],
            "savedPrefabs": [{"path": "Assets/Igor.prefab", "name": "Igor", "saveCount": 4}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    authors = {author["rawAuthor"]: author for author in summary["authors"]}

    assert authors["Dmitry Shane"]["activityMix"] == [{"type": "selection", "count": 3, "percent": 100}]
    assert authors["Dmitry Shane"]["savedPrefabs"] == [{"path": "Assets/Dmitry.prefab", "name": "Dmitry", "saveCount": 2}]
    assert authors["Igor Mats"]["activityMix"] == [{"type": "play_mode", "count": 5, "percent": 100}]
    assert authors["Igor Mats"]["savedPrefabs"] == [{"path": "Assets/Igor.prefab", "name": "Igor", "saveCount": 4}]

def test_activity_summary_groups_author_breakdowns_by_source():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "focus", "count": 4}, {"type": "file_saved", "count": 1}],
            "savedPrefabs": [{"path": "cursor:AL", "name": "AL", "projectId": "AL", "saveCount": 6}],
            "overtimeActivityCounts": [{"type": "focus", "count": 2}],
            "overtimeSavedPrefabs": [{"path": "cursor:OT", "name": "OT", "projectId": "AL", "saveCount": 1}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 90,
            "idleSeconds": 0,
            "activityCounts": [{"type": "play_mode", "count": 3}],
            "savedPrefabs": [{"path": "Assets/Bike.prefab", "name": "Bike", "saveCount": 2}],
            "overtimeActivityCounts": [{"type": "scene_changed", "count": 1}],
            "overtimeSavedPrefabs": [{"path": "Assets/Overtime.prefab", "name": "Overtime", "saveCount": 3}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")

    assert author["activityMix"] == [
        {"type": "focus", "count": 4, "percent": 50},
        {"type": "play_mode", "count": 3, "percent": 38},
        {"type": "file_saved", "count": 1, "percent": 12},
    ]
    assert author["activityMixBySource"] == [
        {
            "source": "cur",
            "totalCount": 5,
            "activeSeconds": 120,
            "activityMix": [{"type": "focus", "count": 4, "percent": 80}, {"type": "file_saved", "count": 1, "percent": 20}],
        },
        {
            "source": "ual",
            "totalCount": 3,
            "activeSeconds": 90,
            "activityMix": [{"type": "play_mode", "count": 3, "percent": 100}],
        },
    ]
    assert author["savedPrefabsBySource"] == [
        {
            "source": "cur",
            "totalSaveCount": 6,
            "savedPrefabs": [{"path": "cursor:AL", "name": "AL", "projectId": "AL", "saveCount": 6}],
        },
        {
            "source": "ual",
            "totalSaveCount": 2,
            "savedPrefabs": [{"path": "Assets/Bike.prefab", "name": "Bike", "saveCount": 2}],
        },
    ]
    assert author["overtimeActivityMixBySource"] == [
        {
            "source": "cur",
            "totalCount": 2,
            "activeSeconds": 0,
            "activityMix": [{"type": "focus", "count": 2, "percent": 100}],
        },
        {
            "source": "ual",
            "totalCount": 1,
            "activeSeconds": 0,
            "activityMix": [{"type": "scene_changed", "count": 1, "percent": 100}],
        },
    ]
    assert author["overtimeSavedPrefabsBySource"] == [
        {
            "source": "ual",
            "totalSaveCount": 3,
            "savedPrefabs": [{"path": "Assets/Overtime.prefab", "name": "Overtime", "saveCount": 3}],
        },
        {
            "source": "cur",
            "totalSaveCount": 1,
            "savedPrefabs": [{"path": "cursor:OT", "name": "OT", "projectId": "AL", "saveCount": 1}],
        },
    ]

def test_activity_summary_puts_overtime_only_device_activity_in_overtime_saved_files():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "source": "dev",
            "projectId": "Bike Rush 2",
            "author": "Dmitry Shane",
            "date": "2026-05-11",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 45,
            "activityCounts": [],
            "overtimeActivityCounts": [{"type": "hold", "count": 10}, {"type": "click", "count": 4}],
            "savedPrefabs": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-05-11", end_date="2026-05-11")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")

    assert author["savedPrefabs"] == []
    assert author["overtimeSavedPrefabs"] == [
        {"path": "device:bike rush 2", "name": "Bike Rush 2", "projectId": "Bike Rush 2", "saveCount": 14}
    ]
    assert author["overtimeSavedPrefabsBySource"] == [
        {
            "source": "dev",
            "totalSaveCount": 14,
            "savedPrefabs": [{"path": "device:bike rush 2", "name": "Bike Rush 2", "projectId": "Bike Rush 2", "saveCount": 14}],
        }
    ]

def test_author_day_consumption_spans_plugin_sources():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {"source": "ual", "projectId": "unity", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 8 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "bal", "projectId": "blender", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 1800, "idleSeconds": 0}
    )

    consumed = repo._normal_microseconds_consumed_for_event(
        {"author": "Dmitry Shane", "date": "2026-04-28", "source": "bal", "projectId": "blender"}
    )

    assert consumed == ((8 * 3600) + 1800) * 1_000_000

def test_author_day_consumption_includes_figma_source():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {"source": "ual", "projectId": "unity", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 4 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "bal", "projectId": "blender", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 3 * 3600, "idleSeconds": 0}
    )
    repo.db.daily_author_activity.insert_one(
        {"source": "fch", "projectId": "figma", "author": "Dmitry Shane", "date": "2026-04-28", "activeSeconds": 2 * 3600, "idleSeconds": 0}
    )

    consumed = repo._normal_microseconds_consumed_for_event(
        {"author": "Dmitry Shane", "date": "2026-04-28", "source": "fch", "projectId": "figma"}
    )

    assert consumed == 9 * 3600 * 1_000_000

def test_raw_event_state_isolated_between_unity_and_blender_sources():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        "source": "bal",
        "author": "Dmitry Shane",
        "projectId": "blender",
        "sessionId": "blender-session",
        "date": "2026-04-28",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:30Z",
        "occurredAtLocal": "2026-04-28T10:00:30+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 30, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    unity_heartbeat = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:20Z",
        "occurredAtLocal": "2026-04-28T10:01:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 20, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_event)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)

    assert blender_deltas["activeDeltaSeconds"] == 0
    assert heartbeat_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["activeDeltaSeconds"] == 0

def test_cross_source_activity_clamps_unity_after_vscode_activity():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    unity_event = {
        "source": "ual",
        "author": "Igor Mats",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "mac-mini",
        "date": "2026-05-01",
        "eventType": "selection",
        "occurredAtUtc": "2026-05-01T19:42:27Z",
        "occurredAtLocal": "2026-05-01T12:42:27-07:00",
        "receivedAt": dt.datetime(2026, 5, 1, 20, 4, tzinfo=dt.UTC),
    }
    unity_blur = {
        **unity_event,
        "eventType": "blur",
        "occurredAtUtc": "2026-05-01T19:58:22Z",
        "occurredAtLocal": "2026-05-01T12:58:22-07:00",
    }
    unity_background_event = {
        **unity_event,
        "eventType": "play_mode",
        "occurredAtUtc": "2026-05-01T19:58:30Z",
        "occurredAtLocal": "2026-05-01T12:58:30-07:00",
    }
    vscode_event = {
        "source": "vsc",
        "author": "Igor Mats",
        "projectId": "unity-bike-rush-2",
        "sessionId": "vscode-session",
        "deviceId": "mac-mini",
        "date": "2026-05-01",
        "eventType": "selection",
        "occurredAtUtc": "2026-05-01T19:58:59Z",
        "occurredAtLocal": "2026-05-01T12:58:59-07:00",
        "receivedAt": dt.datetime(2026, 5, 1, 20, 1, tzinfo=dt.UTC),
    }
    unity_focus_event = {
        **unity_event,
        "eventType": "focus",
        "occurredAtUtc": "2026-05-01T19:59:19Z",
        "occurredAtLocal": "2026-05-01T12:59:19-07:00",
    }
    unity_asset_event = {
        **unity_event,
        "eventType": "asset_saved",
        "occurredAtUtc": "2026-05-01T20:00:33Z",
        "occurredAtLocal": "2026-05-01T13:00:33-07:00",
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    repo._apply_raw_event_to_aggregates(unity_blur)
    background_deltas = repo._apply_raw_event_to_aggregates(unity_background_event)
    repo._apply_raw_event_to_aggregates(vscode_event)
    focus_deltas = repo._apply_raw_event_to_aggregates(unity_focus_event)
    asset_deltas = repo._apply_raw_event_to_aggregates(unity_asset_event)

    assert background_deltas["activeDeltaSeconds"] == 0
    assert background_deltas["idleDeltaSeconds"] == 0
    assert focus_deltas["activeDeltaSeconds"] == 0
    assert asset_deltas["activeDeltaSeconds"] == 94
    assert focus_deltas["idleDeltaSeconds"] == 0
    assert asset_deltas["idleDeltaSeconds"] == 0

def test_rebuild_batch_row_ignores_deltas_before_previous_author_report():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    author = "Igor Mats"
    common = {
        "author": author,
        "authorEmail": "",
        "deviceId": "mac-mini",
        "date": "2026-05-01",
        "pluginVersion": "1.0.0",
        "reportType": "auto",
        "receivedAt": dt.datetime(2026, 5, 1, 20, 4, tzinfo=dt.UTC),
    }
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": "vscode-batch",
            "source": "vsc",
            "author": author,
            "authorEmail": "",
            "projectId": "unity-bike-rush-2",
            "sessionId": "vscode-session",
            "deviceId": "mac-mini",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 1, tzinfo=dt.UTC),
            "reportType": "auto",
        }
    )
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": "unity-batch",
            "source": "ual",
            "author": author,
            "authorEmail": "",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "mac-mini",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 4, tzinfo=dt.UTC),
            "reportType": "auto",
        }
    )

    for event in [
        {
            **common,
            "eventId": "unity-early",
            "source": "ual",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "batchId": "unity-batch",
            "eventType": "selection",
            "occurredAtUtc": "2026-05-01T19:42:27Z",
            "occurredAtLocal": "2026-05-01T12:42:27-07:00",
        },
        {
            **common,
            "eventId": "unity-before-vscode",
            "source": "ual",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "batchId": "unity-batch",
            "eventType": "selection",
            "occurredAtUtc": "2026-05-01T19:57:00Z",
            "occurredAtLocal": "2026-05-01T12:57:00-07:00",
        },
        {
            **common,
            "eventId": "vscode-start",
            "source": "vsc",
            "projectId": "unity-bike-rush-2",
            "sessionId": "vscode-session",
            "batchId": "vscode-batch",
            "eventType": "selection",
            "occurredAtUtc": "2026-05-01T19:58:21Z",
            "occurredAtLocal": "2026-05-01T12:58:21-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 1, tzinfo=dt.UTC),
        },
        {
            **common,
            "eventId": "vscode-report-end",
            "source": "vsc",
            "projectId": "unity-bike-rush-2",
            "sessionId": "vscode-session",
            "batchId": "vscode-batch",
            "eventType": "file_saved",
            "occurredAtUtc": "2026-05-01T19:58:59Z",
            "occurredAtLocal": "2026-05-01T12:58:59-07:00",
            "receivedAt": dt.datetime(2026, 5, 1, 20, 1, tzinfo=dt.UTC),
        },
        {
            **common,
            "eventId": "unity-focus",
            "source": "ual",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "batchId": "unity-batch",
            "eventType": "focus",
            "occurredAtUtc": "2026-05-01T19:59:19Z",
            "occurredAtLocal": "2026-05-01T12:59:19-07:00",
        },
        {
            **common,
            "eventId": "unity-report-end",
            "source": "ual",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "batchId": "unity-batch",
            "eventType": "asset_saved",
            "occurredAtUtc": "2026-05-01T20:00:33Z",
            "occurredAtLocal": "2026-05-01T13:00:33-07:00",
        },
    ]:
        repo.db.raw_activity_events.insert_one(event)

    repo.rebuild_aggregates_if_needed(force=True)

    unity_row = repo.db.report_rows.find_one({"author": author, "source": "ual", "batchId": "unity-batch"})
    assert unity_row is not None
    assert unity_row["activeDeltaSeconds"] == 94
    assert unity_row["idleDeltaSeconds"] == 0

def test_raw_event_state_isolated_between_unity_blender_and_figma_sources():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        **unity_event,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:30Z",
        "occurredAtLocal": "2026-04-28T10:00:30+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 30, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    figma_event = {
        **unity_event,
        "source": "fch",
        "projectId": "figma",
        "sessionId": "figma-session",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:01:00Z",
        "occurredAtLocal": "2026-04-28T10:01:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, tzinfo=dt.UTC),
    }
    figma_heartbeat = {
        **figma_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:02:20Z",
        "occurredAtLocal": "2026-04-28T10:02:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 2, 20, tzinfo=dt.UTC),
    }
    unity_heartbeat = {
        **unity_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:02:24Z",
        "occurredAtLocal": "2026-04-28T10:02:24+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 2, 24, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_event)
    figma_deltas = repo._apply_raw_event_to_aggregates(figma_event)
    figma_heartbeat_deltas = repo._apply_raw_event_to_aggregates(figma_heartbeat)
    unity_heartbeat_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)

    assert blender_deltas["activeDeltaSeconds"] == 0
    assert figma_deltas["activeDeltaSeconds"] == 0
    assert figma_heartbeat_deltas["idleDeltaSeconds"] == 80
    assert unity_heartbeat_deltas["idleDeltaSeconds"] == 0
    assert unity_heartbeat_deltas["activeDeltaSeconds"] == 0

def test_second_plugin_heartbeat_does_not_create_tiny_duplicate_idle_row():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    activity = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    unity_heartbeat = {
        **activity,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:06:00Z",
        "occurredAtLocal": "2026-04-28T10:06:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 6, tzinfo=dt.UTC),
    }
    blender_heartbeat = {
        **activity,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:06:04Z",
        "occurredAtLocal": "2026-04-28T10:06:04+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 6, 4, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(activity)
    unity_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_heartbeat)

    assert unity_deltas["idleDeltaSeconds"] == 6 * 60
    assert blender_deltas["idleDeltaSeconds"] == 0
    assert blender_deltas["activeDeltaSeconds"] == 0

def test_idle_is_accounted_by_each_activity_source_state():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    unity_event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    blender_event = {
        **unity_event,
        "source": "bal",
        "projectId": "blender",
        "sessionId": "blender-session",
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:20Z",
        "occurredAtLocal": "2026-04-28T10:00:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 20, tzinfo=dt.UTC),
        "metadata": {"inputType": "MOUSEMOVE"},
    }
    unity_heartbeat = {
        **unity_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:20Z",
        "occurredAtLocal": "2026-04-28T10:01:20+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 20, tzinfo=dt.UTC),
    }
    blender_heartbeat = {
        **blender_event,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:25Z",
        "occurredAtLocal": "2026-04-28T10:01:25+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 25, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(unity_event)
    repo._apply_raw_event_to_aggregates(blender_event)
    unity_deltas = repo._apply_raw_event_to_aggregates(unity_heartbeat)
    blender_deltas = repo._apply_raw_event_to_aggregates(blender_heartbeat)

    assert unity_deltas["idleDeltaSeconds"] == 0
    assert unity_deltas["activeDeltaSeconds"] == 0
    assert blender_deltas["idleDeltaSeconds"] == 65
    assert blender_deltas["activeDeltaSeconds"] == 0

def test_blender_scene_changed_without_input_metadata_is_not_activity():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    file_saved = {
        "source": "bal",
        "author": "Dmitry Shane",
        "projectId": "blender",
        "sessionId": "blender-session",
        "date": "2026-04-28",
        "eventType": "file_saved",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
        "metadata": {"path": "/project/Scene.blend", "name": "Scene.blend"},
    }
    background_scene_changed = {
        **file_saved,
        "eventType": "scene_changed",
        "occurredAtUtc": "2026-04-28T10:00:03Z",
        "occurredAtLocal": "2026-04-28T10:00:03+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, 3, tzinfo=dt.UTC),
        "metadata": {"filepath": "/project/Scene.blend"},
    }
    heartbeat = {
        **file_saved,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:03Z",
        "occurredAtLocal": "2026-04-28T10:01:03+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, 3, tzinfo=dt.UTC),
        "metadata": {},
    }

    repo._apply_raw_event_to_aggregates(file_saved)
    background_deltas = repo._apply_raw_event_to_aggregates(background_scene_changed)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(heartbeat)

    assert background_deltas["activeDeltaSeconds"] == 0
    assert background_deltas["idleDeltaSeconds"] == 0
    assert background_deltas["activityCountDeltas"] == []
    assert heartbeat_deltas["idleDeltaSeconds"] == 63
    assert heartbeat_deltas["activeDeltaSeconds"] == 0

def test_manual_report_requested_is_not_author_activity():
    repo = fake_repository()
    event = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "manual_report_requested",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
        "metadata": {"reason": "server_request"},
    }

    deltas = repo._apply_raw_event_to_aggregates(event)

    assert deltas["activeDeltaSeconds"] == 0
    assert deltas["idleDeltaSeconds"] == 0
    assert deltas["activityCountDeltas"] == []

def test_heartbeat_idle_threshold_is_independent_from_plugin_interval():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "sendIntervalSeconds": 30, "idleThresholdSeconds": 120}
    )
    activity = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    heartbeat = {
        **activity,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:00Z",
        "occurredAtLocal": "2026-04-28T10:01:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(activity)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(heartbeat)

    assert repo.get_interval_for_author("Dmitry Shane") == 30
    assert repo.get_interval_for_author("Dmitry Shane", source="dev") == 30
    assert repo.get_idle_threshold_for_author("Dmitry Shane") == 120
    assert heartbeat_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["activeDeltaSeconds"] == 0

def test_device_interval_is_independent_from_global_plugin_interval():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "sendIntervalSeconds": 300, "deviceSendIntervalSeconds": 1}
    )

    assert repo.get_interval_for_author("Dmitry Shane") == 300
    assert repo.get_interval_for_author("Dmitry Shane", source="dev") == 1
    assert repo.get_interval_settings()["deviceSendIntervalSeconds"] == 1

def test_heartbeat_idle_threshold_can_be_lower_than_plugin_interval():
    repo = fake_repository()
    repo.db.interval_settings.insert_one(
        {"kind": "global", "sendIntervalSeconds": 600, "idleThresholdSeconds": 60}
    )
    activity = {
        "source": "ual",
        "author": "Dmitry Shane",
        "projectId": "unity",
        "sessionId": "unity-session",
        "date": "2026-04-28",
        "eventType": "selection",
        "occurredAtUtc": "2026-04-28T10:00:00Z",
        "occurredAtLocal": "2026-04-28T10:00:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    }
    heartbeat = {
        **activity,
        "eventType": "heartbeat",
        "occurredAtUtc": "2026-04-28T10:01:00Z",
        "occurredAtLocal": "2026-04-28T10:01:00+00:00",
        "receivedAt": dt.datetime(2026, 4, 28, 10, 1, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(activity)
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(heartbeat)

    assert repo.get_interval_for_author("Dmitry Shane") == 600
    assert repo.get_idle_threshold_for_author("Dmitry Shane") == 60
    assert heartbeat_deltas["idleDeltaSeconds"] == 60
    assert heartbeat_deltas["activeDeltaSeconds"] == 0

def test_blender_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "bal",
            "eventType": "file_saved",
            "metadata": {
                "path": "/projects/scene/Shot01.blend",
                "name": "Shot01.blend",
            },
        }
    )

    assert saved == {"path": "/projects/scene/Shot01.blend", "name": "Shot01.blend", "saveCount": 1}

def test_figma_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "fch",
            "eventType": "file_saved",
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    assert saved == {
        "path": "https://www.figma.com/design/abc123/Game-HUD",
        "name": "Game HUD",
        "saveCount": 1,
    }

def test_figma_activity_file_metadata_is_counted_as_saved_file_breakdown_item():
    worked = _worked_file_delta(
        {
            "source": "fch",
            "eventType": "selection",
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    assert worked == {
        "path": "https://www.figma.com/design/abc123/Game-HUD",
        "name": "Game HUD",
        "saveCount": 1,
    }

def test_non_figma_activity_file_metadata_is_not_counted_as_saved_file_breakdown_item():
    worked = _worked_file_delta(
        {
            "source": "cur",
            "eventType": "selection",
            "projectId": "AL",
            "metadata": {
                "path": "/projects/AL/apps/backend/al_backend/activity_math.py",
                "name": "activity_math.py",
            },
        }
    )

    assert worked is None

def test_vscode_file_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "vsc",
            "eventType": "file_saved",
            "projectId": "game",
            "metadata": {
                "path": "/projects/game/Assets/Scripts/PlayerController.cs",
                "name": "PlayerController.cs",
                "languageId": "csharp",
            },
        }
    )

    assert saved == {
        "path": "/projects/game/Assets/Scripts/PlayerController.cs",
        "name": "PlayerController.cs",
        "saveCount": 1,
    }

def test_cursor_file_saved_is_counted_as_saved_file_with_project():
    saved = _saved_prefab_delta(
        {
            "source": "cur",
            "eventType": "file_saved",
            "projectId": "AL",
            "metadata": {
                "path": "/projects/AL/apps/frontend/src/main.tsx",
                "name": "main.tsx",
                "languageId": "typescriptreact",
            },
        }
    )

    assert saved == {
        "path": "/projects/AL/apps/frontend/src/main.tsx",
        "name": "main.tsx",
        "projectId": "AL",
        "saveCount": 1,
    }

def test_non_figma_file_saved_still_requires_blend_file():
    saved = _saved_prefab_delta(
        {
            "source": "bal",
            "eventType": "file_saved",
            "metadata": {
                "path": "https://www.figma.com/design/abc123/Game-HUD",
                "name": "Game HUD",
                "fileKey": "abc123",
            },
        }
    )

    assert saved is None
