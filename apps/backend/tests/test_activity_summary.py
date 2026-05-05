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
from al_backend.meeting_summary import DEFAULT_MEETING_SUMMARY_PROMPT, meeting_summary_sections, render_meeting_summary_prompt
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


def test_productivity_ignores_first_break_hour():
    author = _with_productivity({"activeSeconds": 5 * 3600, "idleSeconds": 3 * 3600, "breakSeconds": 60 * 60})

    assert author["productivity"] == 62.5


def test_interval_settings_include_independent_idle_threshold():
    repo = fake_repository()

    assert repo.get_interval_settings()["defaultSendIntervalSeconds"] == 60
    assert repo.get_interval_settings()["idleThresholdSeconds"] == 300
    assert repo.get_interval_settings()["telegramOnlinePromptDelayMinutes"] == 15

    result = repo.upsert_interval_settings(
        default_send_interval_seconds=120,
        idle_threshold_seconds=450,
        device_idle_threshold_seconds=60,
        plugin_ingest_enabled=None,
        author=None,
        author_send_interval_seconds=None,
    )

    assert result["defaultSendIntervalSeconds"] == 120
    assert result["idleThresholdSeconds"] == 450
    assert result["deviceIdleThresholdSeconds"] == 60
    assert result["pluginIngestEnabled"] is True


def test_interval_settings_include_global_plugin_ingest_toggle():
    repo = fake_repository()

    result = repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=False,
        author=None,
        author_send_interval_seconds=None,
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
        author=None,
        author_send_interval_seconds=None,
    )
    repo.upsert_interval_settings(
        default_send_interval_seconds=None,
        idle_threshold_seconds=None,
        device_idle_threshold_seconds=None,
        plugin_ingest_enabled=True,
        author=None,
        author_send_interval_seconds=None,
    )

    doc = repo.db.system_settings.find_one({"kind": "plugins"}) or {}
    assert doc.get("pluginIngestResumedAtUtc") is not None


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
        author=None,
        author_send_interval_seconds=None,
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


def test_telegram_username_is_normalized_for_mapping():
    assert _normalize_telegram_username(" @Dmitry_Shane ") == "dmitry_shane"


def test_telegram_bot_parses_team_commands():
    assert parse_event_type(" онлайн ") == "online"
    assert parse_event_type("ONLINE") == "online"
    assert parse_event_type("АФК") == "afk"
    assert parse_event_type("афк!") == "afk"
    assert parse_event_type("оффлайн") == "offline"
    assert parse_event_type("hello") is None


def test_telegram_bot_uses_sender_username():
    assert telegram_username({"username": "@Dmitry_Shane"}) == "dmitry_shane"


def test_telegram_bot_parses_reminder_callbacks():
    assert parse_reminder_callback("altd:abc123:offline") == ("abc123", "offline")
    assert parse_reminder_callback("altd:abc123:overtime") == ("abc123", "overtime")
    assert parse_reminder_callback("altd:abc123:afk") is None
    assert parse_reminder_callback("altm:abc:confirm_online") is None
    assert parse_reminder_callback("hello") is None


def test_telegram_bot_parses_online_prompt_callbacks():
    assert parse_callback_data("altm:abc123:confirm_online") == ("altm", "abc123", "confirm_online")
    assert parse_callback_data("altm:abc123:dismiss") == ("altm", "abc123", "dismiss")
    assert parse_callback_data("altm:abc:offline") is None
    assert parse_callback_data("altd:x:offline") == ("altd", "x", "offline")
    assert parse_callback_data("altb:break1:confirm_online") == ("altb", "break1", "confirm_online")
    assert parse_callback_data("altb:break1:still_afk") == ("altb", "break1", "still_afk")
    assert parse_callback_data("altb:break1:dismiss") is None
    assert parse_callback_data("altf:d1:confirm_online") == ("altf", "d1", "confirm_online")
    assert parse_callback_data("altf:d1:still_afk") == ("altf", "d1", "still_afk")


def test_telegram_bot_update_polling_includes_callbacks(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": []}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)

    assert get_updates("token", None) == []
    assert captured["method"] == "getUpdates"
    assert "callback_query" in captured["params"]["allowed_updates"]


def test_telegram_bot_reminder_message_mentions_author_and_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 42}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    result = send_reminder_message("token", 123, "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?", "reminder-1")

    assert result["result"]["message_id"] == 42
    assert captured["method"] == "sendMessage"
    assert "@dmitryshane" in captured["params"]["text"]
    assert '"Offline"' in captured["params"]["reply_markup"]
    assert '"Overtime"' in captured["params"]["reply_markup"]
    assert "altd:reminder-1:offline" in captured["params"]["reply_markup"]
    assert "altd:reminder-1:overtime" in captured["params"]["reply_markup"]


def test_telegram_bot_edit_message_names_author(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)

    edit_reminder_message("token", 123, 42, "overtime", "dmitryshane")

    assert captured["method"] == "editMessageText"
    assert captured["params"]["text"] == "Done. @dmitryshane Telegram day closed as Overtime."
    assert '"inline_keyboard": []' in captured["params"]["reply_markup"]


def test_telegram_bot_online_prompt_message_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 99}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_online_prompt_message("token", 123, "Hi @user. Test?", "prompt-1")

    assert captured["method"] == "sendMessage"
    assert "altm:prompt-1:confirm_online" in captured["params"]["reply_markup"]
    assert "altm:prompt-1:dismiss" in captured["params"]["reply_markup"]
    assert "I'm online" in captured["params"]["reply_markup"]


def test_telegram_bot_break_activity_prompt_message_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 100}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_break_activity_prompt_message("token", 123, "Hi @user. Test?", "prompt-1")

    assert captured["method"] == "sendMessage"
    assert "altb:prompt-1:confirm_online" in captured["params"]["reply_markup"]
    assert "altb:prompt-1:still_afk" in captured["params"]["reply_markup"]
    assert "I'm online" in captured["params"]["reply_markup"]
    assert "Still AFK" in captured["params"]["reply_markup"]


def test_telegram_bot_duplicate_afk_prompt_message_has_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 101}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_duplicate_afk_prompt_message("token", 123, "Hi @user. Duplicate AFK?", "prompt-2")

    assert captured["method"] == "sendMessage"
    assert "altf:prompt-2:confirm_online" in captured["params"]["reply_markup"]
    assert "altf:prompt-2:still_afk" in captured["params"]["reply_markup"]


def test_telegram_bot_plain_message_has_no_buttons(monkeypatch):
    captured = {}

    def fake_request(token, method, params):
        captured.update({"token": token, "method": method, "params": params})
        return {"result": {"message_id": 101}}

    monkeypatch.setattr("al_backend.telegram_bot.telegram_request", fake_request)
    send_plain_message("token", 123, "Hello")

    assert captured["method"] == "sendMessage"
    assert captured["params"] == {"chat_id": 123, "text": "Hello"}


def test_telegram_bot_formats_prompt_time_in_author_time_zone():
    assert format_prompt_time("2026-05-01T13:09:24", "Europe/Madrid") == "15:09"


def test_telegram_bot_formats_duration_labels():
    assert format_duration_label(60) == "1 minute"
    assert format_duration_label(600) == "10 minutes"
    assert format_duration_label(75) == "75 seconds"


def test_telegram_bot_formats_meeting_duration_labels():
    assert format_meeting_duration_label(-1) == "unknown"
    assert format_meeting_duration_label(0) == "0m"
    assert format_meeting_duration_label(45) == "45s"
    assert format_meeting_duration_label(60) == "1m"
    assert format_meeting_duration_label(65) == "1m 5s"
    assert format_meeting_duration_label(180) == "3m"
    assert format_meeting_duration_label(3600) == "1h"
    assert format_meeting_duration_label(3605) == "1h 5s"
    assert format_meeting_duration_label(3660) == "1h 1m"
    assert format_meeting_duration_label(3661) == "1h 1m 1s"


def test_telegram_bot_callback_edits_online_prompt(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", reminder_id, action, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", action, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altm:prompt-1:dismiss",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": 'Hi @dmitryshane. You have activity today but no "online" message yet.',
            },
        },
    )

    assert ("close", "prompt-1", "dismiss", "online_prompt") in calls
    assert ("edit", "dismiss", "online_prompt") in calls
    assert ("answer", "Dismissed.") in calls


def test_telegram_bot_callback_edits_break_activity_prompt(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", reminder_id, action, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", action, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altb:prompt-1:still_afk",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. You went AFK at 14:30, but I now see activity from you.",
            },
        },
    )

    assert ("close", "prompt-1", "still_afk", "break_activity_prompt") in calls
    assert ("edit", "still_afk", "break_activity_prompt") in calls
    assert ("answer", "Still AFK.") in calls


def test_telegram_bot_callback_edits_message_after_close(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(backend_url, bot_secret, reminder_id, action, actor_telegram_username="", *, reminder_kind="day_end"):
        calls.append(("close", backend_url, bot_secret, reminder_id, action, actor_telegram_username, reminder_kind))
        return {"ok": True}

    def fake_edit_reminder_message(token, chat_id, message_id, action, telegram_username="", *, reminder_kind="day_end"):
        calls.append(("edit", token, chat_id, message_id, action, telegram_username, reminder_kind))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", token, callback_query_id, text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altd:reminder-1:overtime",
            "from": {"username": "dmitryshane"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?",
            },
        },
    )

    assert ("close", "https://activity.mempic.com", "secret", "reminder-1", "overtime", "dmitryshane", "day_end") in calls
    assert ("edit", "token", 123, 42, "overtime", "dmitryshane", "day_end") in calls
    assert ("answer", "token", "callback-1", "Telegram day closed.") in calls


def test_telegram_bot_callback_rejects_wrong_user(monkeypatch):
    calls = []
    config = BotConfig(
        token="token",
        backend_url="https://activity.mempic.com",
        allowed_chat_id=123,
        bot_secret="secret",
    )

    def fake_close_reminder(*args):
        calls.append(("close", *args))
        return {"ok": True}

    def fake_edit_reminder_message(*args):
        calls.append(("edit", *args))
        return {"ok": True}

    def fake_answer_callback_query(token, callback_query_id, text):
        calls.append(("answer", token, callback_query_id, text))
        return {"ok": True}

    monkeypatch.setattr("al_backend.telegram_bot.close_reminder", fake_close_reminder)
    monkeypatch.setattr("al_backend.telegram_bot.edit_reminder_message", fake_edit_reminder_message)
    monkeypatch.setattr("al_backend.telegram_bot.answer_callback_query", fake_answer_callback_query)

    handle_callback_query(
        config,
        {
            "id": "callback-1",
            "data": "altd:reminder-1:offline",
            "from": {"username": "someone_else"},
            "message": {
                "message_id": 42,
                "chat": {"id": 123},
                "text": "Hi @dmitryshane. Did you forget to go offline, or are you working overtime?",
            },
        },
    )

    assert calls == [("answer", "token", "callback-1", "Sorry, this reminder was not sent to you.")]


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


def test_telegram_online_creates_visible_report_row_and_live_day_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})

    result = repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    authors = {}
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}
    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-28",
        "2026-04-28",
        None,
        dt.datetime(2026, 4, 28, 9, 10, tzinfo=dt.UTC),
    )

    assert result["status"] == "online_recorded"
    assert repo.db.report_rows.items[0]["source"] == "telegram"
    assert repo.db.report_rows.items[0]["reportType"] == "telegram"
    assert repo.db.report_rows.items[0]["telegramEventType"] == "online"
    assert authors["Future Artist"]["telegramDaySeconds"] == 10 * 60


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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramToFirstActivitySeconds"] == 17 * 60 + 30


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
            "hourlyActivity": _empty_hourly_activity(),
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
    assert hourly_by_hour[11]["idleSeconds"] == 57 * 60 + 1
    assert hourly_by_hour[11]["missedStartSeconds"] == 2 * 60 + 59
    assert hourly_by_hour[12]["idleSeconds"] == 31 * 60 + 7


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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[9]["missedSeconds"] == 17 * 60 + 30
    assert hourly_by_hour[9]["missedStartSeconds"] == 17 * 60 + 30
    assert hourly_by_hour[9]["missedEndSeconds"] == 0
    assert hourly_by_hour[9]["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[19]["missedSeconds"] == 40 * 60
    assert hourly_by_hour[19]["missedStartSeconds"] == 0
    assert hourly_by_hour[19]["missedEndSeconds"] == 40 * 60
    assert hourly_by_hour[19]["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["pluginDaySeconds"] == 60


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
    hourly_activity = _empty_hourly_activity()
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

    assert hour_16["activeSeconds"] == 2679
    assert hour_16["idleSeconds"] == 921
    assert hour_16["missedSeconds"] == 0
    assert hour_16["activeSeconds"] + hour_16["idleSeconds"] == 3600
    assert author["idleSeconds"] == 837
    assert author["pluginDaySeconds"] == 3516
    assert summary["totals"]["idleSeconds"] == 837
    assert summary["totals"]["pluginDaySeconds"] == 3516


def test_activity_summary_current_plugin_hour_gap_is_not_filled():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "UTC"})
    hourly_activity = _empty_hourly_activity()
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

    assert hour_10["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["pluginDaySeconds"] == 60
    assert summary["totals"]["idleSeconds"] == 0
    assert summary["totals"]["pluginDaySeconds"] == 60


def test_activity_summary_previous_plugin_hour_gap_is_visual_only_after_next_hour_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane", "timeZoneId": "UTC"})
    hourly_activity = _empty_hourly_activity()
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

    assert hour_10["idleSeconds"] == 3540
    assert hour_10["activeSeconds"] + hour_10["idleSeconds"] == 3600
    assert hour_11["idleSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["pluginDaySeconds"] == 90
    assert summary["totals"]["idleSeconds"] == 0
    assert summary["totals"]["pluginDaySeconds"] == 90


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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[19]["missedEndSeconds"] == 0
    assert hourly_by_hour[21]["missedSeconds"] == 23 * 60
    assert hourly_by_hour[21]["missedEndSeconds"] == 23 * 60
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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[20]["missedEndSeconds"] == 51 * 60
    assert hourly_by_hour[21]["missedEndSeconds"] == 0


def test_activity_summary_visual_missed_end_fills_last_report_hour_to_sixty_minutes():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "timeZoneId": "America/Vancouver"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "date": "2026-05-01",
            "startedAt": dt.datetime(2026, 5, 1, 15, 0, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 1, 22, 30, tzinfo=dt.UTC),
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
    hourly_activity = _empty_hourly_activity()
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

    assert hourly_by_hour[14]["overtimeActiveSeconds"] == 21 * 60 + 31
    assert hourly_by_hour[14]["missedEndSeconds"] == 3600 - (21 * 60 + 31)


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
    hourly_activity = _empty_hourly_activity()
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

    assert hourly_by_hour[18]["missedEndSeconds"] == 0
    assert hourly_by_hour[19]["idleSeconds"] == 464
    assert hourly_by_hour[19]["missedEndSeconds"] == 3600 - 464


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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert hourly_by_hour[14]["missedEndSeconds"] == 0
    assert hourly_by_hour[14]["missedSeconds"] == 0


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
    hourly_activity = _empty_hourly_activity()
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

    assert hourly_by_hour[20]["idleSeconds"] == 10 * 60
    assert hourly_by_hour[20]["missedEndSeconds"] == 50 * 60
    assert hourly_by_hour[20]["missedSeconds"] == 50 * 60
    assert author["idleSeconds"] == 7 * 60
    assert author["pluginDaySeconds"] == 7 * 60
    assert summary["totals"]["idleSeconds"] == 7 * 60
    assert summary["totals"]["pluginDaySeconds"] == 7 * 60


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
    hourly_activity = _empty_hourly_activity()
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

    assert hourly_by_hour[20]["idleSeconds"] == 9 * 60
    assert hourly_by_hour[20]["missedEndSeconds"] == 51 * 60
    assert author["idleSeconds"] == 6 * 60
    assert author["pluginDaySeconds"] == 6 * 60
    assert summary["totals"]["idleSeconds"] == 6 * 60
    assert summary["totals"]["pluginDaySeconds"] == 6 * 60


def test_telegram_to_first_activity_gap_counts_as_idle_hourly_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist", "timeZoneId": "UTC"})
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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 77 * 60 + 30
    assert author["idleSeconds"] == 77 * 60 + 30
    assert author["pluginDaySeconds"] == 78 * 60 + 30
    assert author["rawPluginDaySeconds"] == 78 * 60 + 30
    assert author["productivity"] == 1.27
    assert hourly_by_hour[9]["idleSeconds"] == 3600
    assert hourly_by_hour[10]["idleSeconds"] == 17 * 60 + 30


def test_telegram_to_first_activity_uses_first_raw_activity_event_before_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry",
            "displayName": "Dmitriy Zhdamarov",
            "telegramUsername": "zhdamarovich",
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.record_break_event("zhdamarovich", "online", "2026-04-30T07:35:42Z")
    repo.db.raw_activity_events.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "eventType": "focus",
            "occurredAtUtc": "2026-04-30T07:36:12Z",
            "occurredAtLocal": "2026-04-30T09:36:12+02:00",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:43:12+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
            "savedPrefabDeltas": [{"path": "Assets/Level.prefab", "name": "Level", "saveCount": 1}],
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:45:13+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "projectId": "unity",
            "date": "2026-04-30",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-30", end_date="2026-04-30")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry")

    assert author["telegramToFirstActivitySeconds"] == 30


def test_telegram_to_first_activity_falls_back_to_first_positive_report_row_without_raw_events():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry",
            "displayName": "Dmitriy Zhdamarov",
            "telegramUsername": "zhdamarovich",
            "timeZoneId": "Europe/Madrid",
        }
    )
    repo.record_break_event("zhdamarovich", "online", "2026-04-30T07:35:42Z")
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:43:12+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
            "savedPrefabDeltas": [{"path": "Assets/Level.prefab", "name": "Level", "saveCount": 1}],
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:45:13+02:00",
            "receivedAt": dt.datetime(2026, 4, 30, 7, 46, 15, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry",
            "projectId": "unity",
            "date": "2026-04-30",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-30", end_date="2026-04-30")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry")

    assert author["telegramToFirstActivitySeconds"] == 9 * 60 + 31


def _insert_presence_daily_activity(repo, received_at):
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": received_at,
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )


def _author_from_summary(repo, now):
    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28", now=now)
    return next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")


def _author_status(repo, now):
    return _author_from_summary(repo, now)["status"]


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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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


def test_device_summary_uses_application_name_as_saved_item():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
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
            "hourlyActivity": _empty_hourly_activity(),
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


def test_device_profile_includes_latest_device_id():
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

    profile = next(item for item in repo.author_profiles() if item["rawAuthor"] == "Device1")

    assert profile["deviceId"] == "keychain-device-id"


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
            "hourlyActivity": _empty_hourly_activity(),
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
                {"eventId": "focus-1", "eventType": "focus", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
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
    assert daily["idleSeconds"] == 7200
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
                {"eventId": "focus-1", "eventType": "focus", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
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
    assert daily["idleSeconds"] == 7200
    assert daily.get("breakSeconds", 0) == 0
    assert daily.get("autoBreakSeconds", 0) == 0
    assert sum(hour["idleSeconds"] for hour in daily["hourlyActivity"]) == 7200
    assert sum(hour.get("breakSeconds", 0) for hour in daily["hourlyActivity"]) == 0

    report_page = repo.reports_page(start_date="2026-05-02", end_date="2026-05-02", author="Future Artist")
    assert report_page["reports"][0]["idleDeltaSeconds"] == 7200
    assert report_page["reports"][0].get("breakDeltaSeconds", 0) == 0

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    hourly = next(item for item in summary["hourlyActivityByAuthor"] if item["rawAuthor"] == "Future Artist")["hourlyActivity"]
    assert author["idleSeconds"] == 3600
    assert author["breakSeconds"] == 3600
    assert sum(hour["idleSeconds"] for hour in hourly) == 3600
    assert sum(hour["breakSeconds"] for hour in hourly) == 3600

    repo.rebuild_aggregates_if_needed(force=True)
    rebuilt = repo.db.daily_author_activity.find_one({"author": "Future Artist", "date": "2026-05-02", "source": "cur"})
    assert rebuilt["idleSeconds"] == 7200
    assert rebuilt.get("breakSeconds", 0) == 0
    assert rebuilt.get("autoBreakSeconds", 0) == 0


def test_auto_break_only_fills_remaining_legal_break():
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
                {"eventId": "focus-1", "eventType": "focus", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
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
    assert daily["idleSeconds"] == 7200
    assert daily.get("breakSeconds", 0) == 0
    assert daily.get("autoBreakSeconds", 0) == 0

    summary = repo.activity_summary(start_date="2026-05-02", end_date="2026-05-02")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    assert author["idleSeconds"] == 5400
    assert author["breakSeconds"] == 3600


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
    cur_hourly = _empty_hourly_activity()
    vsc_hourly = _empty_hourly_activity()
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

    assert author["idleSeconds"] == 3600
    assert author["breakSeconds"] == 3600
    assert sum(hour["idleSeconds"] for hour in hourly) == 3600
    assert sum(hour["breakSeconds"] for hour in hourly) == 3600


def test_auto_break_applies_after_visual_idle_gaps():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Future Artist",
            "displayName": "Future Artist",
            "autoBreakEnabled": True,
            "autoBreakEffectiveDate": "2026-05-02",
        }
    )
    hourly_activity = _empty_hourly_activity()
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

    assert author["breakSeconds"] == 3540
    assert hourly[8]["idleSeconds"] == 0
    assert hourly[8]["breakSeconds"] == 3540


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
                {"eventId": "focus-1", "eventType": "focus", "date": "2026-05-02", "occurredAtUtc": "2026-05-02T08:00:00Z"},
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
    assert daily["idleSeconds"] == 7200
    assert daily.get("breakSeconds", 0) == 0


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
            "hourlyActivity": _empty_hourly_activity(),
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
    assert daily["activeSeconds"] == 32415
    assert daily["overtimeActiveSeconds"] == 15


def test_vacation_day_plugin_activity_is_overtime_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "timeZoneId": "UTC"})
    repo.db.calendar_marks.insert_one(
        {"rawAuthor": "Future Artist", "date": "2026-05-06", "reasonId": "vacation", "note": "Vacation"}
    )

    hourly_activity = _empty_hourly_activity()
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

    assert daily["activeSeconds"] == 0
    assert daily["overtimeActiveSeconds"] == 1800
    assert author["dayOverride"]["type"] == "vacation"
    assert author["activeSeconds"] == 0
    assert author["idleSeconds"] == 0
    assert author["breakSeconds"] == 0
    assert author["meetingSeconds"] == 0
    assert author["overtimeActiveSeconds"] == 1800
    assert hour_10["activeSeconds"] == 0
    assert hour_10["idleSeconds"] == 0
    assert hour_10["breakSeconds"] == 0
    assert hour_10["meetingSeconds"] == 0
    assert hour_10["missedSeconds"] == 0
    assert hour_10["overtimeActiveSeconds"] == 1800
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
    assert author["meetingSeconds"] == 0
    assert author["overtimeActiveSeconds"] == 2700
    assert hour_11["meetingSeconds"] == 0
    assert hour_11["missedSeconds"] == 0
    assert hour_11["overtimeActiveSeconds"] == 2700


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
            "hourlyActivity": _empty_hourly_activity(),
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
    assert daily["activeSeconds"] == 32430
    assert daily["overtimeActiveSeconds"] == 0


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


def test_telegram_online_after_offline_restores_normal_presence():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.record_break_event("future_artist", "online", "2026-04-28T18:10:00Z")

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "online"


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

    assert author["idleSeconds"] == 1800
    assert author["meetingSeconds"] == 1800
    assert author["productivity"] == 0
    assert hour["idleSeconds"] == 1800
    assert hour["meetingSeconds"] == 1800


def test_discord_meeting_does_not_replace_active_time():
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

    assert author["activeSeconds"] == 600
    assert author["idleSeconds"] == 0
    assert author["meetingSeconds"] == 1200
    assert hour["activeSeconds"] == 600
    assert hour["idleSeconds"] == 0
    assert hour["meetingSeconds"] == 1200


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

    assert author["meetingSeconds"] == 1800
    assert author["idleSeconds"] == 5400


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

    assert author["meetingSeconds"] == 1200
    assert hour["meetingSeconds"] == 1200


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

    assert author["meetingSeconds"] == 1200
    assert summary["totals"]["meetingSeconds"] == 1200
    assert hour["meetingSeconds"] == 1200


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

    assert author["meetingSeconds"] == 3420
    assert summary["totals"]["meetingSeconds"] == 3420
    assert hour_0["meetingSeconds"] == 31 * 60
    assert hour_1["meetingSeconds"] == 26 * 60


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

    assert hour_0["meetingSeconds"] == 3600
    assert hour_1["meetingSeconds"] == 26 * 60


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
    assert author["meetingSeconds"] == 1200


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

    assert author["activeSeconds"] == 600
    assert author["idleSeconds"] == 600
    assert author["meetingSeconds"] == 900
    assert hour["activeSeconds"] == 600
    assert hour["idleSeconds"] == 600
    assert hour["meetingSeconds"] == 900
    assert summary["totals"]["idleSeconds"] == 600


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

    assert hour_17["meetingSeconds"] == 2417
    assert hour_17["activeSeconds"] == 1183
    assert hour_17["idleSeconds"] == 0
    assert hour_18["meetingSeconds"] == 228
    assert author["activeSeconds"] == 2597
    assert author["meetingSeconds"] == 2645


def test_overtime_hourly_graph_fills_gap_when_overtime_continues_next_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = _empty_hourly_activity()
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

    assert hour_19["meetingSeconds"] == 4 * 60
    assert hour_19["overtimeActiveSeconds"] == 56 * 60
    assert author["overtimeActiveSeconds"] == 52 * 60
    assert summary["totals"]["overtimeActiveSeconds"] == 52 * 60


def test_overtime_hourly_graph_does_not_fill_gap_without_next_overtime_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "displayName": "Denis Ostrovskiy",
            "timeZoneId": "UTC",
        }
    )
    hourly_activity = _empty_hourly_activity()
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

    assert hour_19["meetingSeconds"] == 4 * 60
    assert hour_19["overtimeActiveSeconds"] == 52 * 60


def test_overtime_hourly_graph_does_not_fill_from_reports_only_inside_hour():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    hourly_activity = _empty_hourly_activity()
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

    assert hour_14["overtimeActiveSeconds"] == 1665


def test_overtime_hourly_graph_fills_normal_to_overtime_transition_gap():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    hourly_activity = _empty_hourly_activity()
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

    assert hour_13["activeSeconds"] == 125
    assert hour_13["overtimeActiveSeconds"] == 3600 - 125


def test_discord_voice_events_open_and_close_meeting_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})

    join = repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")
    leave = repo.record_discord_voice_event("123", "future", "leave", timestamp="2026-04-29T10:25:00+00:00")

    assert join["status"] == "meeting_started"
    assert leave["status"] == "meeting_closed"
    assert leave["meetingSeconds"] == 1500
    assert repo.db.meeting_sessions.items == []
    assert repo.db.meeting_intervals.items[0]["meetingSeconds"] == 1500
    assert repo.db.report_rows.items[-1]["source"] == "discord"
    assert repo.db.report_rows.items[-1]["reportType"] == "meeting"


def test_live_discord_meeting_reports_follow_send_interval():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "sendIntervalSeconds": 300})
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-04-29T10:00:00+00:00")

    inserted = repo.materialize_live_meeting_reports(dt.datetime(2026, 4, 29, 10, 12, tzinfo=dt.UTC))

    live_reports = [item for item in repo.db.report_rows.items if item.get("discordStatus") == "meeting_live"]
    daily = repo.db.daily_author_activity.find_one({"source": "discord", "author": "Future Artist", "date": "2026-04-29"})
    hour = daily["hourlyActivity"][10]
    session = repo.db.meeting_sessions.find_one({"discordUserId": "123"})

    assert inserted == 2
    assert [item["meetingSeconds"] for item in live_reports] == [300, 300]
    assert [item["recordedAt"] for item in live_reports] == [
        "2026-04-29T10:05:00+00:00",
        "2026-04-29T10:10:00+00:00",
    ]
    assert all(item["activityType"] == "meeting_live" for item in live_reports)
    assert hour["meetingSeconds"] == 600
    assert session["lastLiveReportAt"] == dt.datetime(2026, 4, 29, 10, 10, tzinfo=dt.UTC)


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
    assert result["meetingSeconds"] == 1500
    assert repo.db.meeting_sessions.items == []
    assert repo.db.meeting_intervals.items[0]["endedAt"] == dt.datetime(2026, 4, 29, 10, 25, tzinfo=dt.UTC)
    assert repo.db.meeting_intervals.items[0]["meetingSeconds"] == 1500
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
    assert repo.get_discord_settings()["meetingAutoAfkTimeoutSeconds"] == 900


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


def test_discord_settings_include_default_meeting_summary_prompt():
    repo = fake_repository()

    prompt = repo.get_discord_settings()["meetingSummaryPrompt"]

    assert prompt == DEFAULT_MEETING_SUMMARY_PROMPT
    assert "{transcript}" not in prompt


def test_default_meeting_summary_prompt_renders_sections():
    prompt = render_meeting_summary_prompt(
        DEFAULT_MEETING_SUMMARY_PROMPT,
        language="English",
        participants="Dmitry, Igor",
        sections=meeting_summary_sections("English"),
        transcript="We agreed to fix Discord recordings.",
    )

    assert "Expected participants: Dmitry, Igor" in prompt
    assert "Return exactly these sections:\nDiscussed:\n" in prompt
    assert "\nParticipants:\n" not in prompt.split("Return exactly these sections:")[1]
    assert "Action items:" in prompt
    assert "Transcript:\nWe agreed to fix Discord recordings." in prompt


def test_meeting_recording_finished_creates_summary_notification():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "telegramUsername": "dmitryshane",
            "discordUserId": "1",
        }
    )
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "discordUserId": "2",
        }
    )
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    class FakeSummary:
        transcript = "Dmitry and Igor agreed to create a Discord summary task."
        summary = "Discussed:\n- Plans for Discord summaries.\n\nDecisions:\n- Add Discord summaries.\n\nAction items:\n- Create a task.\n\nOpen questions:\n- None."

    result = repo.process_meeting_recording_finished(
        recording_id="recording-1",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: FakeSummary(),
    )
    notifications = repo.claim_due_telegram_meeting_summary_notifications()

    assert result["status"] == "summary_created"
    assert notifications[0]["summaryId"] == result["summaryId"]
    assert notifications[0]["participantTelegramUsernames"] == ["dmitryshane", "igormats"]
    assert "Discord summaries" in notifications[0]["summary"]


def test_meeting_recording_start_and_finish_create_telegram_notifications():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "telegramUsername": "dmitryshane",
            "discordUserId": "1",
        }
    )
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "discordUserId": "2",
        }
    )
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=3,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    start_result = repo.record_meeting_recording_started(
        recording_id="recording-telegram",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
    )
    finish_result = repo.process_meeting_recording_finished(
        recording_id="recording-telegram",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: None,
    )
    notifications = repo.claim_due_telegram_meeting_recording_notifications()

    assert start_result["status"] == "recording_started"
    assert finish_result["status"] == "skipped_not_enough_participants"
    assert [item["kind"] for item in notifications] == ["started", "ended"]
    assert notifications[0]["participantTelegramUsernames"] == ["dmitryshane", "igormats"]
    assert notifications[1]["durationSeconds"] == 180

    repo.record_meeting_recording_started(
        recording_id="recording-telegram",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
    )

    assert len(repo.db.telegram_meeting_recording_notifications.items) == 2


def test_meeting_recording_notification_mark_sent():
    repo = fake_repository()
    repo.db.telegram_meeting_recording_notifications.insert_one(
        {
            "reminderId": "meeting-recording-reminder",
            "recordingId": "recording-1",
            "kind": "started",
            "status": "pending",
        }
    )

    result = repo.mark_telegram_meeting_recording_notification_sent("meeting-recording-reminder", message_id=321)
    notification = repo.db.telegram_meeting_recording_notifications.find_one({"reminderId": "meeting-recording-reminder"})

    assert result["ok"] is True
    assert notification["status"] == "sent"
    assert notification["messageId"] == 321


def test_meeting_recording_summary_queue_stores_audio_stats_and_clears_error():
    repo = fake_repository()
    repo.db.meeting_recordings.insert_one(
        {
            "recordingId": "recording-queued",
            "status": "recording_failed",
            "error": "<html>504 Gateway Time-out</html>",
        }
    )

    result = repo.queue_meeting_recording_summary_processing(
        recording_id="recording-queued",
        ended_at="2026-04-29T10:03:00+00:00",
        duration_seconds=180,
        audio_frame_count=100,
        non_silent_frame_count=80,
        mixed_user_count=2,
        audio_quality_status="ok",
    )
    recording = repo.db.meeting_recordings.find_one({"recordingId": "recording-queued"})

    assert result == {"ok": True, "status": "summary_queued"}
    assert recording["status"] == "queued_for_summary"
    assert recording["durationSeconds"] == 180
    assert recording["audioFrameCount"] == 100
    assert recording["nonSilentFrameCount"] == 80
    assert recording["mixedUserCount"] == 2
    assert recording["audioQualityStatus"] == "ok"
    assert "error" not in recording


def test_queued_meeting_recording_summary_worker_creates_summary_and_deletes_audio():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    class FakeSummary:
        transcript = "Dmitry and Igor agreed to create a Discord summary task."
        summary = "Discussed:\n- Plans for Discord summaries.\n\nDecisions:\n- Add Discord summaries.\n\nAction items:\n- Create a task.\n\nOpen questions:\n- None."

    with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as audio_file:
        audio_path = audio_file.name
        audio_file.write(b"audio")

    result = repo.process_queued_meeting_recording_summary(
        recording_id="recording-worker",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
        audio_path=audio_path,
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: FakeSummary(),
    )
    recording = repo.db.meeting_recordings.find_one({"recordingId": "recording-worker"})

    assert result["status"] == "summary_created"
    assert recording["status"] == "waiting_for_telegram"
    assert recording["summaryId"] == result["summaryId"]
    assert Path(audio_path).exists() is False


def test_meeting_recording_summary_worker_keeps_existing_summary_idempotent():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )
    repo.db.meeting_summaries.insert_one(
        {
            "summaryId": "summary-existing",
            "recordingId": "recording-existing",
            "status": "pending",
        }
    )
    called = False

    def fake_summary_generator(path, people, language, prompt_template, progress_callback=None):
        nonlocal called
        called = True

    result = repo.process_meeting_recording_finished(
        recording_id="recording-existing",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Igor"],
        audio_path="/tmp/missing.wav",
        summary_generator=fake_summary_generator,
    )

    assert result == {"ok": True, "status": "summary_already_created", "summaryId": "summary-existing"}
    assert called is False
    assert len(repo.db.meeting_summaries.items) == 1


def test_meeting_summary_sent_clears_stale_recording_error():
    repo = fake_repository()
    repo.db.meeting_recordings.insert_one(
        {
            "recordingId": "recording-sent",
            "status": "recording_failed",
            "startedAt": dt.datetime(2026, 5, 1, 10, 0, tzinfo=dt.UTC),
            "error": "<html>504 Gateway Time-out</html>",
        }
    )
    repo.db.meeting_summaries.insert_one(
        {
            "summaryId": "summary-sent",
            "recordingId": "recording-sent",
            "status": "claimed",
        }
    )

    result = repo.mark_telegram_meeting_summary_sent("summary-sent", message_id=123)
    recording = repo.db.meeting_recordings.find_one({"recordingId": "recording-sent"})
    recent = repo.recent_meeting_recordings()[0]

    assert result["ok"] is True
    assert recording["status"] == "telegram_sent"
    assert "error" not in recording
    assert recent["status"] == "telegram_sent"
    assert recent["error"] is None


def test_meeting_recording_finished_skips_solo_recording():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=2,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    result = repo.process_meeting_recording_finished(
        recording_id="recording-1",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1"],
        participant_names=["Dmitry"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: None,
    )

    assert result["status"] == "skipped_not_enough_participants"
    assert repo.db.meeting_summaries.items == []


def test_meeting_recording_finished_skips_empty_work_summary():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=1,
        meeting_summary_min_duration_seconds=10,
        meeting_summary_language="Russian",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    class FakeSummary:
        transcript = "garbled text that is long enough but has no usable work content"
        summary = "Обсудили:\nНет\n\nРешения:\nНет\n\nЗадачи:\nНет\n\nОткрытые вопросы:\nНет"

    result = repo.process_meeting_recording_finished(
        recording_id="recording-empty-work",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:01:00+00:00",
        participant_discord_user_ids=["1"],
        participant_names=["Dmitry"],
        audio_path="/tmp/missing.m4a",
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: FakeSummary(),
    )

    assert result["status"] == "skipped_empty_transcript"
    assert repo.db.meeting_summaries.items == []


def test_meeting_recording_finished_skips_corrupted_audio_before_openai():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=1,
        meeting_summary_min_duration_seconds=10,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )
    called = False

    def fake_summary_generator(path, people, language, prompt_template, progress_callback=None):
        nonlocal called
        called = True

    result = repo.process_meeting_recording_finished(
        recording_id="recording-corrupted",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:01:00+00:00",
        participant_discord_user_ids=["1"],
        participant_names=["Dmitry"],
        audio_frame_count=100,
        non_silent_frame_count=80,
        corrupted_packet_count=25,
        audio_path="/tmp/missing.m4a",
        summary_generator=fake_summary_generator,
    )

    recording = repo.db.meeting_recordings.find_one({"recordingId": "recording-corrupted"})
    assert result["status"] == "skipped_corrupted_audio"
    assert recording["audioQualityStatus"] == "corrupted"
    assert recording["corruptedPacketCount"] == 25
    assert called is False
    assert repo.db.meeting_summaries.items == []


def test_recent_meeting_recordings_include_audio_quality_stats():
    repo = fake_repository()
    repo.db.meeting_recordings.insert_one(
        {
            "recordingId": "recording-quality",
            "startedAt": dt.datetime(2026, 5, 1, 10, 0, tzinfo=dt.UTC),
            "status": "skipped_corrupted_audio",
            "audioFrameCount": 100,
            "nonSilentFrameCount": 80,
            "corruptedPacketCount": 25,
            "unknownSourceFrameCount": 2,
            "silencePaddingFrameCount": 4,
            "mixedUserCount": 2,
            "perUserFrameCounts": {"Dmitry": 60, "Igor": 40},
            "listenErrorCount": 1,
            "listenError": "decode failed",
            "audioQualityStatus": "corrupted",
        }
    )

    recording = repo.recent_meeting_recordings()[0]

    assert recording["audioQualityStatus"] == "corrupted"
    assert recording["mixedUserCount"] == 2
    assert recording["perUserFrameCounts"]["Dmitry"] == 60
    assert recording["listenErrorCount"] == 1


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


def test_telegram_meeting_auto_afk_notifications_can_be_claimed_and_marked_sent():
    repo = fake_repository()
    repo.db.telegram_meeting_auto_afk_notifications.insert_one(
        {
            "reminderId": "notification-1",
            "autoAfkEventId": "123:2026-04-29T10:25:00+00:00",
            "rawAuthor": "Future Artist",
            "telegramUsername": "future_artist",
            "soloStartedAt": dt.datetime(2026, 4, 29, 10, 25, tzinfo=dt.UTC),
            "movedAt": dt.datetime(2026, 4, 29, 10, 35, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "excludedSeconds": 600,
            "thresholdSeconds": 60,
            "status": "pending",
        }
    )

    notifications = repo.claim_due_telegram_meeting_auto_afk_notifications(now=dt.datetime(2026, 4, 29, 10, 36, tzinfo=dt.UTC))
    sent = repo.mark_telegram_meeting_auto_afk_notification_sent("notification-1", 42)

    assert notifications[0]["reminderId"] == "notification-1"
    assert notifications[0]["telegramUsername"] == "future_artist"
    assert notifications[0]["excludedSeconds"] == 600
    assert notifications[0]["thresholdSeconds"] == 60
    assert sent == {"ok": True}
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["status"] == "sent"
    assert repo.db.telegram_meeting_auto_afk_notifications.items[0]["messageId"] == 42


def test_telegram_private_chat_is_saved_for_profile():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitryshane"})

    result = repo.save_telegram_private_chat("dmitryshane", 12345)
    profile = repo.db.author_profiles.find_one({"rawAuthor": "Dmitry Shane"})

    assert result["ok"] is True
    assert profile["telegramPrivateChatId"] == 12345
    assert repo.author_profiles()[0]["telegramPrivateChatId"] == 12345


def test_meeting_summary_chat_id_uses_private_recipient():
    assert meeting_summary_chat_id(1, {"recipient": {"kind": "private", "chatId": 42}}) == 42
    assert meeting_summary_chat_id(1, {"recipient": {"kind": "work_chat"}}) == 1


def test_meeting_summary_message_includes_meeting_metadata():
    message = format_meeting_summary_message(
        {
            "startedAt": "2026-05-01T10:00:00+00:00",
            "durationSeconds": 180,
            "participantNames": ["Dmitry", "Igor"],
            "participantTelegramUsernames": ["dmitryshane", "igormats"],
        },
        "Discussed:\n- Backend status UI.",
    )

    assert "Date: 2026-05-01" in message
    assert "Duration: 3m" in message
    assert "Participants: @dmitryshane, @igormats" in message
    assert "Discussed:" in message


def test_meeting_summary_message_falls_back_to_participant_names():
    message = format_meeting_summary_message(
        {
            "startedAt": "2026-05-01T10:00:00+00:00",
            "durationSeconds": 180,
            "participantNames": ["Dmitry", "Igor"],
        },
        "Discussed:\n- Backend status UI.",
    )

    assert "Participants: @Dmitry, @Igor" in message


def test_meeting_recording_notification_message_starts_with_hi_and_mentions_participants():
    start_message = format_meeting_recording_notification_message(
        {
            "kind": "started",
            "participantNames": ["Dmitry", "Igor"],
            "participantTelegramUsernames": ["dmitryshane", "igormats"],
        }
    )
    end_message = format_meeting_recording_notification_message(
        {
            "kind": "ended",
            "participantNames": ["Dmitry", "Igor"],
            "participantTelegramUsernames": ["dmitryshane", "igormats"],
        }
    )

    assert start_message == "Hi @dmitryshane, @igormats. Your meeting has started. Hope it goes smoothly!"
    assert end_message == "Hi @dmitryshane, @igormats. Your meeting has ended. Thanks everyone, please wait for the summary."


def test_recent_meeting_recordings_include_summary_delivery_status():
    repo = fake_repository()
    repo.db.meeting_recordings.insert_one(
        {
            "recordingId": "recording-1",
            "startedAt": dt.datetime(2026, 5, 1, 10, 0, tzinfo=dt.UTC),
            "endedAt": dt.datetime(2026, 5, 1, 10, 5, tzinfo=dt.UTC),
            "durationSeconds": 300,
            "participantNames": ["dmitryshane"],
            "status": "summarized",
            "updatedAt": dt.datetime(2026, 5, 1, 10, 6, tzinfo=dt.UTC),
        }
    )
    repo.db.meeting_summaries.insert_one(
        {
            "recordingId": "recording-1",
            "summaryId": "summary-1",
            "status": "sent",
            "recipient": {"kind": "private", "label": "@dmitryshane"},
            "telegramSentAt": dt.datetime(2026, 5, 1, 10, 7, tzinfo=dt.UTC),
        }
    )

    recordings = repo.recent_meeting_recordings()

    assert recordings[0]["recordingId"] == "recording-1"
    assert recordings[0]["summaryId"] == "summary-1"
    assert recordings[0]["status"] == "telegram_sent"
    assert recordings[0]["recipient"]["kind"] == "private"


def test_recent_meeting_activity_includes_voice_events_recordings_and_day_separators():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "discordUserId": "123", "timeZoneId": "UTC"})
    repo.record_discord_voice_event("123", "future", "join", timestamp="2026-05-04T10:00:00+00:00")
    repo.record_discord_voice_event("123", "future", "leave", timestamp="2026-05-04T10:30:00+00:00")
    repo.db.meeting_recordings.insert_one(
        {
            "recordingId": "recording-1",
            "status": "recording_failed",
            "startedAt": dt.datetime(2026, 5, 3, 10, 0, tzinfo=dt.UTC),
            "updatedAt": dt.datetime(2026, 5, 3, 10, 30, tzinfo=dt.UTC),
            "participantNames": ["Future Artist"],
        }
    )

    items = repo.recent_meeting_activity()

    assert [item["date"] for item in items if item["itemType"] == "day_separator"] == ["2026-05-04", "2026-05-03"]
    assert any(item["itemType"] == "voice_event" and item["eventType"] == "join" for item in items)
    assert any(item["itemType"] == "voice_event" and item["eventType"] == "leave" for item in items)
    assert any(item["itemType"] == "recording" and item["recording"]["recordingId"] == "recording-1" for item in items)


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


def test_meeting_recording_tracks_openai_pipeline_status():
    repo = fake_repository()
    repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=600,
        meeting_summaries_enabled=True,
        meeting_summary_min_participants=1,
        meeting_summary_min_duration_seconds=60,
        meeting_summary_language="English",
        meeting_summary_recipient="work_chat",
        meeting_audio_retention_seconds=0,
        meeting_summary_prompt="",
    )

    class FakeSummary:
        transcript = "Dmitry agreed to create a task."
        summary = "Discussed:\n- Task creation.\n\nDecisions:\n- Create a task.\n\nAction items:\n- Create a task.\n\nOpen questions:\n- None."

    def fake_summary_generator(path, people, language, prompt_template, progress_callback=None):
        if progress_callback:
            progress_callback("transcribing_openai")
            progress_callback("summarizing_openai")
        return FakeSummary()

    result = repo.process_meeting_recording_finished(
        recording_id="recording-2",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-04-29T10:00:00+00:00",
        ended_at="2026-04-29T10:03:00+00:00",
        participant_discord_user_ids=["1"],
        participant_names=["Dmitry"],
        audio_path="/tmp/missing.wav",
        summary_generator=fake_summary_generator,
    )

    assert result["status"] == "summary_created"
    assert repo.recent_meeting_recordings()[0]["status"] == "summary_pending"


def test_discord_recording_fail_and_status_are_public_bot_paths():
    assert "/api/v1/discord/meeting-recordings/fail" in PUBLIC_API_PATHS
    assert "/api/v1/discord/meeting-recordings/status" in PUBLIC_API_PATHS


def test_meeting_audio_finalize_deletes_pcm_after_success(tmp_path):
    audio_path = tmp_path / "al-meeting-success.m4a"
    track_path = tmp_path / "al-meeting-success.m4a.123.pcm"
    track_path.write_bytes(b"\x01" * 8)

    sink = MeetingAudioSink(audio_path=str(audio_path))
    sink.tracks[123] = UserPcmTrack(
        user_id=123,
        user_name="Speaker",
        path=str(track_path),
        file=open(track_path, "ab"),
        bytes_written=8,
        frame_count=1,
        non_silent_frame_count=1,
    )

    def fake_run_ffmpeg(args):
        audio_path.write_bytes(b"audio")

    sink._run_ffmpeg = fake_run_ffmpeg
    sink.finalize()

    assert audio_path.exists()
    assert not track_path.exists()


def test_meeting_audio_finalize_keeps_pcm_after_failure_for_retention(tmp_path):
    audio_path = tmp_path / "al-meeting-failed.m4a"
    track_path = tmp_path / "al-meeting-failed.m4a.123.pcm"
    track_path.write_bytes(b"\x01" * 8)

    sink = MeetingAudioSink(audio_path=str(audio_path))
    sink.tracks[123] = UserPcmTrack(
        user_id=123,
        user_name="Speaker",
        path=str(track_path),
        file=open(track_path, "ab"),
        bytes_written=8,
        frame_count=1,
        non_silent_frame_count=1,
    )

    def fake_run_ffmpeg(args):
        raise RuntimeError("ffmpeg failed")

    sink._run_ffmpeg = fake_run_ffmpeg

    try:
        sink.finalize()
    except RuntimeError:
        pass

    assert track_path.exists()

    recording = RecordingSession(
        recording_id="recording-1",
        started_at=dt.datetime(2026, 5, 4, 14, 52, tzinfo=dt.UTC),
        audio_path=str(audio_path),
        participant_ids={123},
        participant_names={123: "Speaker"},
        voice_client=None,
        sink=sink,
        cleanup_future=None,
    )
    retain_recording_recovery_files(recording, 3600, "ffmpeg failed")

    retained_tracks = list(tmp_path.glob("al-meeting-failed.m4a.123.pcm.keep-until-*"))
    manifests = list(tmp_path.glob("al-meeting-failed.m4a.recovery.json.keep-until-*"))

    assert len(retained_tracks) == 1
    assert len(manifests) == 1
    assert not track_path.exists()

    manifest = json.loads(manifests[0].read_text())
    assert manifest["recordingId"] == "recording-1"
    assert manifest["tracks"][0]["path"] == str(retained_tracks[0])


def test_cleanup_old_retained_recordings_removes_recovery_files(tmp_path):
    expired_track = tmp_path / "al-meeting-old.m4a.123.pcm.keep-until-1"
    expired_manifest = tmp_path / "al-meeting-old.m4a.recovery.json.keep-until-1"
    unrelated = tmp_path / "other.keep-until-1"
    expired_track.write_bytes(b"pcm")
    expired_manifest.write_text("{}")
    unrelated.write_text("keep")

    cleanup_old_retained_recordings(str(tmp_path))

    assert not expired_track.exists()
    assert not expired_manifest.exists()
    assert unrelated.exists()


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
            "hourlyActivity": _empty_hourly_activity(),
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


def test_activity_hourly_cache_keeps_heavy_hourly_separate():
    repo = fake_repository()
    hourly = _empty_hourly_activity()
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
    assert hourly_summary["hourlyActivityByAuthor"][0]["hourlyActivity"][10]["activeSeconds"] == 60


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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 9, 5, 30, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["status"] == "online"
    assert author["lastReceivedAt"] == "2026-04-29T09:05:00+00:00"
    assert repo.db.status_events.items[-1]["statusEventType"] == "online"
    assert repo.db.status_events.items[-1]["reason"] == "reports_resumed"
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
            "hourlyActivity": _empty_hourly_activity(),
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


def test_reports_stopped_gap_heartbeats_do_not_double_status_idle():
    """Heartbeats inside author status offline (reports_stopped window) must not add idle that later duplicates status reconciliation.

    After deploying a fix for historical days, run rebuild_aggregates_for_dates for affected authors/dates so report_rows and daily_author_activity match.
    """
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    day = "2026-05-04"
    author = "Future Artist"
    tz = "UTC"
    offline_at = dt.datetime(2026, 5, 4, 0, 21, 45, 261000, tzinfo=dt.UTC)
    online_at = dt.datetime(2026, 5, 4, 0, 41, 9, 809000, tzinfo=dt.UTC)
    gap_seconds = int(round((online_at - offline_at).total_seconds()))

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

    assert total_idle == gap_seconds


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


def test_open_telegram_day_is_capped_at_ten_hours_without_dashboard_payload():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})

    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    summary = repo.activity_summary(
        start_date="2026-04-28",
        end_date="2026-04-28",
        now=dt.datetime(2026, 4, 28, 19, 30, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert author["telegramDaySeconds"] == 10 * 3600
    assert summary["totals"]["telegramDaySeconds"] == 10 * 3600
    assert "alerts" not in author


def test_due_telegram_reminder_includes_profile_username_and_deduplicates_after_sent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")

    reminders = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))

    assert len(reminders) == 1
    assert reminders[0]["telegramUsername"] == "future_artist"
    assert reminders[0]["rawAuthor"] == "Future Artist"

    repo.mark_telegram_day_reminder_sent(reminders[0]["reminderId"], 42)

    assert repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 5, tzinfo=dt.UTC)) == []


def test_telegram_reminder_offline_closes_day_at_click_time():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")

    assert result["status"] == "reminder_offline"
    assert result["daySeconds"] == 10 * 3600 + 15 * 60
    assert repo.db.day_sessions.items[0]["lastOfflineAt"] == dt.datetime(2026, 4, 28, 19, 15, tzinfo=dt.UTC)
    assert repo.db.daily_author_activity.items[0]["source"] == "telegram"
    assert repo.db.daily_author_activity.items[0]["daySeconds"] == 10 * 3600 + 15 * 60
    assert repo.db.report_rows.items[-1]["telegramEventType"] == "offline"
    assert repo.db.report_rows.items[-1]["telegramStatus"] == "reminder_offline"
    assert repo.db.report_rows.items[-1]["metadata"]["reminderAction"] == "offline"


def test_telegram_reminder_overtime_closes_day_with_overtime_metadata():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:30:00Z")

    assert result["status"] == "reminder_overtime"
    assert result["daySeconds"] == 10 * 3600 + 30 * 60
    assert repo.db.report_rows.items[-1]["telegramStatus"] == "reminder_overtime"
    assert repo.db.report_rows.items[-1]["metadata"]["reminderAction"] == "overtime"


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


def test_telegram_reminder_close_closes_open_break_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "afk", "2026-04-28T18:45:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")

    assert result["breakSeconds"] == 30 * 60
    assert repo.db.break_sessions.items == []
    assert repo.db.break_intervals.items[0]["breakSeconds"] == 30 * 60


def test_telegram_reminder_close_is_idempotent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    first = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")
    second = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-28T19:20:00Z")

    reminder_reports = [row for row in repo.db.report_rows.items if row.get("telegramStatus", "").startswith("reminder_")]
    assert first["status"] == "reminder_offline"
    assert second["status"] == "reminder_offline_already_closed"
    assert len(reminder_reports) == 1
    assert repo.db.day_sessions.items[0]["daySeconds"] == 10 * 3600 + 15 * 60


def test_telegram_reminder_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z", "other_person")

    reminder_reports = [row for row in repo.db.report_rows.items if row.get("telegramStatus", "").startswith("reminder_")]
    assert result["status"] == "wrong_user"
    assert "lastOfflineAt" not in repo.db.day_sessions.items[0]
    assert reminder_reports == []


def test_telegram_reminder_close_keeps_day_visible_on_close_date():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igormats",
            "timeZoneId": "America/Vancouver",
        }
    )
    repo.record_break_event("igormats", "online", "2026-04-29T00:35:33Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 29, 23, 25, tzinfo=dt.UTC))[0]

    result = repo.close_telegram_day_from_reminder(reminder["reminderId"], "overtime", "2026-04-29T23:29:37Z")
    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29", now=dt.datetime(2026, 4, 29, 23, 30, tzinfo=dt.UTC))
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Igor Mats")

    assert result["daySeconds"] == 82444
    assert repo.db.day_sessions.items[0]["date"] == "2026-04-29"
    assert repo.db.daily_author_activity.items[0]["date"] == "2026-04-29"
    assert author["telegramDaySeconds"] == 82444


def test_telegram_online_prompt_schedules_once_per_day():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert len(repo.db.telegram_online_prompts.items) == 1


def test_telegram_online_prompt_schedules_again_after_dismiss_same_day():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    day = "2026-04-30"
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t0)

    assert len(repo.db.telegram_online_prompts.items) == 1

    repo.db.telegram_online_prompts.items[0]["status"] = "closed"
    repo.db.telegram_online_prompts.items[0]["closeAction"] = "dismiss"
    t1 = dt.datetime(2026, 4, 30, 12, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t1)

    assert len(repo.db.telegram_online_prompts.items) == 2
    assert repo.db.telegram_online_prompts.items[1]["status"] == "pending"
    assert repo.db.telegram_online_prompts.items[1]["firstReportReceivedAt"] == t1


def test_telegram_online_prompt_not_scheduled_second_time_while_sent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    day = "2026-04-30"
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t0)
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"
    t1 = dt.datetime(2026, 4, 30, 12, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t1)

    assert len(repo.db.telegram_online_prompts.items) == 1


def test_telegram_online_prompt_not_scheduled_without_telegram():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "timeZoneId": "UTC"})
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC))

    assert repo.db.telegram_online_prompts.items == []


def test_telegram_online_prompt_not_scheduled_when_day_session_exists():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.day_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": t0, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.db.telegram_online_prompts.items == []


def test_telegram_online_prompt_claim_after_delay():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=14)) == []

    due = repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=16))
    assert len(due) == 1
    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=20)) == []


def test_telegram_online_prompt_claim_after_custom_delay_minutes():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "telegramOnlinePromptDelayMinutes": 30})
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=29)) == []

    due = repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=31))
    assert len(due) == 1
    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=35)) == []


def test_telegram_online_prompt_superseded_when_day_session_exists_at_claim():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo.db.day_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": t0, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )

    assert repo.claim_due_telegram_online_prompts(t0 + dt.timedelta(minutes=16)) == []

    doc = repo.db.telegram_online_prompts.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "superseded_day_session"


def test_telegram_online_prompt_dismiss_closes_without_online_event():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "dismiss", "2026-04-30T12:00:00Z", "ta")

    assert result["ok"]
    assert result["status"] == "online_prompt_dismissed"
    assert repo.db.day_sessions.items == []
    assert [e for e in repo.db.break_events.items if e.get("eventType") == "online"] == []


def test_telegram_online_prompt_dismiss_purges_plugin_raw_events_preserves_discord():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    day = "2026-04-30"
    batch_id = "batch-dismiss-1"
    repo.db.raw_reports.insert_one({"_id": "rr1"})
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": batch_id,
            "rawReportId": "rr1",
            "challengeId": "c1",
            "source": "ual",
            "pluginVersion": "1",
            "author": "A",
            "authorEmail": "",
            "projectId": "p",
            "sessionId": "s",
            "deviceId": "d",
            "receivedAt": dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC),
            "sentAt": None,
            "eventCount": 1,
            "reportType": "auto",
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "ual-e1",
            "batchId": batch_id,
            "rawReportId": "rr1",
            "challengeId": "c1",
            "source": "ual",
            "pluginVersion": "1",
            "author": "A",
            "authorEmail": "",
            "projectId": "p",
            "sessionId": "s",
            "deviceId": "d",
            "date": day,
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-30T08:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 8, 0, 1, tzinfo=dt.UTC),
            "reportType": "auto",
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
        }
    )
    repo.db.raw_activity_events.insert_one(
        {
            "eventId": "discord-e1",
            "source": "discord",
            "pluginVersion": "1",
            "author": "A",
            "authorEmail": "",
            "projectId": "p",
            "sessionId": "s",
            "deviceId": "d",
            "date": day,
            "eventType": "focus",
            "occurredAtUtc": dt.datetime(2026, 4, 30, 9, 0, tzinfo=dt.UTC),
            "occurredAtLocal": "2026-04-30T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 9, 0, 1, tzinfo=dt.UTC),
            "reportType": "auto",
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
        }
    )
    repo.db.status_events.insert_one(
        {
            "rawAuthor": "A",
            "date": day,
            "statusEventType": "offline",
            "transitionAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
            "receivedAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "reason": "reports_stopped",
            "createdAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.status_events.insert_one(
        {
            "rawAuthor": "A",
            "date": "2026-04-29",
            "statusEventType": "online",
            "transitionAt": dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.UTC),
            "receivedAt": dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "reason": "reports_resumed",
            "createdAt": dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.status_states.insert_one(
        {
            "rawAuthor": "A",
            "status": "offline",
            "updatedAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
            "transitionAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
        }
    )
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "LegacyAlias", "targetRawAuthor": "A"})
    repo.db.report_rows.insert_one(
        {
            "source": "status",
            "author": "LegacyAlias",
            "date": day,
            "recordedAt": "2026-04-30T05:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 5, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "offline",
        }
    )
    t0 = dt.datetime(2026, 4, 30, 7, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "dismiss", "2026-04-30T12:00:00Z", "ta")

    assert result["ok"]
    assert result["deletedRawActivityEvents"] == 1
    assert result["deletedStatusEvents"] == 1
    assert result["deletedStatusReportRows"] == 1
    assert len(repo.db.raw_activity_events.items) == 1
    assert repo.db.raw_activity_events.items[0]["source"] == "discord"
    assert repo.db.raw_event_batches.items == []
    assert repo.db.raw_reports.items == []
    assert len(repo.db.status_events.items) == 1
    assert repo.db.status_events.items[0]["date"] == "2026-04-29"
    assert repo.db.status_states.items[0]["status"] == "online"
    assert not [r for r in repo.db.report_rows.items if r.get("source") == "status" and r.get("date") == day]


def test_telegram_online_prompt_confirm_records_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"
    before = len(repo.db.break_events.items)

    result = repo.close_telegram_online_prompt(rid, "confirm_online", "2026-04-30T12:00:00Z", "ta")

    assert result["ok"]
    assert len(repo.db.break_events.items) > before
    assert repo.db.day_sessions.items
    assert repo.db.telegram_online_prompts.items[0]["closeAction"] == "confirm_online"
    telegram_reports = [r for r in repo.db.report_rows.items if r.get("source") == "telegram" and r.get("telegramEventType") == "online"]
    assert len(telegram_reports) == 1
    assert telegram_reports[0]["recordedAt"] == "2026-04-30T08:00:00+00:00"
    assert telegram_reports[0]["receivedAt"] == dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)


def test_telegram_online_prompt_confirm_aligns_to_first_plugin_report_and_exempt_from_status_filter():
    repo = fake_repository()
    day = "2026-04-30"
    t_plugin = dt.datetime(2026, 4, 30, 11, 23, tzinfo=dt.UTC)
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "pluginVersion": "ual",
            "author": "A",
            "authorEmail": "",
            "projectId": "unity",
            "sessionId": "s1",
            "deviceId": "",
            "date": day,
            "recordedAt": t_plugin.isoformat(),
            "receivedAt": dt.datetime(2026, 4, 30, 11, 24, tzinfo=dt.UTC),
            "lastRecordedAt": t_plugin.isoformat(),
            "lastReceivedAt": dt.datetime(2026, 4, 30, 11, 24, tzinfo=dt.UTC),
            "timeZoneId": "UTC",
            "timeZoneDisplayName": "UTC",
            "reportType": "auto",
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "status",
            "author": "A",
            "date": day,
            "recordedAt": "2026-04-30T06:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 30, 6, 0, tzinfo=dt.UTC),
            "reportType": "status",
            "statusEventType": "offline",
        }
    )
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "confirm_online", "2026-04-30T18:00:00Z", "ta")

    assert result["ok"]
    telegram_reports = [r for r in repo.db.report_rows.items if r.get("source") == "telegram" and r.get("telegramEventType") == "online"]
    assert len(telegram_reports) == 1
    telegram_row = telegram_reports[0]
    assert telegram_row["recordedAt"] == t_plugin.isoformat()
    assert telegram_row["receivedAt"] == t_plugin
    assert telegram_row["lastReceivedAt"] == t_plugin
    status_rows = [r for r in repo.db.report_rows.items if r.get("source") == "status"]
    intervals = repo._status_intervals_for_reports(status_rows)
    assert repo._is_report_inside_status_interval(telegram_row, intervals) is False


def test_record_break_event_online_invalidates_online_prompt():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    repo.record_break_event("ta", "online", "2026-04-30T09:00:00Z")

    doc = repo.db.telegram_online_prompts.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "cancelled_by_online"


def test_telegram_online_prompt_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "dismiss", "2026-04-30T12:00:00Z", "other")

    assert result["status"] == "wrong_user"


def test_break_activity_prompt_schedules_after_sixty_minutes_only():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )

    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=59))
    assert repo.db.telegram_break_activity_prompts.items == []

    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=61))

    assert len(repo.db.telegram_break_activity_prompts.items) == 1
    assert repo.db.telegram_break_activity_prompts.items[0]["status"] == "pending"


def test_break_activity_prompt_claim_and_mark_sent():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))

    due = repo.claim_due_telegram_break_activity_prompts(started_at + dt.timedelta(minutes=61))

    assert len(due) == 1
    assert due[0]["telegramUsername"] == "ta"
    assert repo.claim_due_telegram_break_activity_prompts(started_at + dt.timedelta(minutes=62)) == []

    repo.mark_telegram_break_activity_prompt_sent(due[0]["reminderId"], 44)

    assert repo.db.telegram_break_activity_prompts.items[0]["status"] == "sent"
    assert repo.db.telegram_break_activity_prompts.items[0]["messageId"] == 44


def test_break_activity_prompt_confirm_online_closes_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "confirm_online", "2026-04-30T09:05:00Z", "ta")

    assert result["ok"]
    assert result["status"] == "break_activity_prompt_confirmed_online"
    assert repo.db.break_sessions.items == []
    assert repo.db.break_intervals.items[0]["breakSeconds"] == 65 * 60
    assert repo.db.telegram_break_activity_prompts.items[0]["closeAction"] == "confirm_online"


def test_break_activity_prompt_still_afk_keeps_break_and_reports_hidden():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "A",
            "date": "2026-04-30",
            "recordedAt": "2026-04-30T09:05:00Z",
            "receivedAt": dt.datetime(2026, 4, 30, 9, 5, tzinfo=dt.UTC),
            "activeDeltaSeconds": 0,
            "idleDeltaSeconds": 300,
            "overtimeActiveDeltaSeconds": 0,
        }
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "still_afk", "2026-04-30T09:05:00Z", "ta")

    assert result["status"] == "break_activity_prompt_still_afk"
    assert repo.db.break_sessions.items
    assert repo.latest_reports(start_date="2026-04-30", end_date="2026-04-30") == []


def test_break_activity_prompt_close_rejects_wrong_actor():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    started_at = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo.db.break_sessions.insert_one(
        {"rawAuthor": "A", "date": "2026-04-30", "startedAt": started_at, "telegramUsername": "ta", "timeZoneId": "UTC"}
    )
    repo._schedule_telegram_break_activity_prompt_if_needed("A", "2026-04-30", "ual", started_at + dt.timedelta(minutes=60))
    reminder_id = repo.db.telegram_break_activity_prompts.items[0]["reminderId"]
    repo.db.telegram_break_activity_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_break_activity_prompt(reminder_id, "confirm_online", "2026-04-30T09:05:00Z", "other")

    assert result["status"] == "wrong_user"
    assert repo.db.break_sessions.items


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


def test_author_local_today_summary_includes_authors_on_different_local_dates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Madrid Author", "displayName": "Madrid Author", "timeZoneId": "Europe/Madrid"})
    repo.db.author_profiles.insert_one({"rawAuthor": "Utc Author", "displayName": "Utc Author", "timeZoneId": "UTC"})
    repo.db.author_profiles.insert_one({"rawAuthor": "No Activity Author", "displayName": "No Activity Author", "timeZoneId": "UTC"})
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
        date_mode="authorLocalToday",
        now=dt.datetime(2026, 4, 28, 22, 30, tzinfo=dt.UTC),
    )

    authors = {author["rawAuthor"]: author for author in summary["authors"]}
    assert authors["Madrid Author"]["activeSeconds"] == 60
    assert authors["Utc Author"]["activeSeconds"] == 120
    assert authors["No Activity Author"]["activeSeconds"] == 0
    assert authors["No Activity Author"]["status"] == "stale"
    assert authors["No Activity Author"]["stalePresence"] == "telegram"
    assert authors["Utc Author"]["status"] == "stale"
    assert authors["Utc Author"]["stalePresence"] == "reports"


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


def test_live_telegram_summary_includes_open_session_outside_selected_date_range():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Igor Mats", "displayName": "Igor Mats", "telegramUsername": "igormats", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "telegramUsername": "igormats",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 22, 6, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )
    authors = {}
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}

    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-29",
        "2026-04-29",
        "authorLocalToday",
        dt.datetime(2026, 4, 28, 22, 16, tzinfo=dt.UTC),
    )

    assert authors["Igor Mats"]["telegramDaySeconds"] == 10 * 60
    assert totals["telegramDaySeconds"] == 10 * 60


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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "hourlyActivity": _empty_hourly_activity(),
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
            "activityMix": [{"type": "focus", "count": 4, "percent": 80}, {"type": "file_saved", "count": 1, "percent": 20}],
        },
        {
            "source": "ual",
            "totalCount": 3,
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
            "activityMix": [{"type": "focus", "count": 2, "percent": 100}],
        },
        {
            "source": "ual",
            "totalCount": 1,
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


def test_analytics_summary_includes_day_hourly_activity():
    repo = fake_repository()
    today = dt.date.today()
    hourly_activity = _empty_hourly_activity()
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
    assert day["hourlyActivity"][10]["activeSeconds"] == 1800
    assert len(empty_day["hourlyActivity"]) == 24
    assert empty_day["hourlyActivity"] == _empty_hourly_activity()


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
            "hourlyActivity": _empty_hourly_activity(),
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


def test_activity_summary_returns_empty_hourly_activity_for_telegram_only_author():
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
    assert hourly_by_author["Igor Mats"]["hourlyActivity"] == _empty_hourly_activity()


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


def test_break_event_flow_records_day_and_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.daily_author_activity.insert_one({"author": "Dmitry Shane", "date": "2026-04-28", "breakSeconds": 0})

    assert repo.record_break_event("dmitry_shane", "online", "2026-04-28T09:00:00Z")["status"] == "online_recorded"
    assert repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:00:00Z")["status"] == "break_started"
    break_closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:15:00Z")
    day_closed = repo.record_break_event("dmitry_shane", "offline", "2026-04-28T18:00:00Z")

    assert break_closed == {"ok": True, "status": "break_closed", "breakSeconds": 15 * 60}
    assert day_closed["status"] == "day_closed"
    assert day_closed["daySeconds"] == 9 * 3600
    assert repo.db.daily_author_activity.items[0]["breakSeconds"] == 15 * 60
    telegram_activity = next(item for item in repo.db.daily_author_activity.items if item.get("source") == "telegram")
    assert telegram_activity["daySeconds"] == 9 * 3600


def test_repeated_afk_does_not_reset_break_start():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})

    first = repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:00:00Z")
    second = repo.record_break_event("dmitry_shane", "afk", "2026-04-28T12:10:00Z")
    closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:20:00Z")

    assert first["status"] == "break_started"
    assert second["status"] == "duplicate_afk"
    assert second.get("reminderId")
    assert second["afkStartedTimeLocal"] == "12:00"
    afk_reports = [
        row
        for row in repo.db.report_rows.items
        if row.get("source") == "telegram" and row.get("telegramEventType") == "afk"
    ]
    assert len(afk_reports) == 1
    assert len(repo.db.telegram_duplicate_afk_prompts.items) == 1
    assert closed["breakSeconds"] == 20 * 60


def test_duplicate_telegram_online_skips_extra_report_and_break_event():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    assert repo.record_break_event("ta", "online", "2026-04-28T09:00:00Z")["status"] == "online_recorded"
    dup = repo.record_break_event("ta", "online", "2026-04-28T10:00:00Z")
    assert dup["status"] == "duplicate_online"
    assert dup["sinceTimeLocal"] == "09:00"
    online_rows = [
        row
        for row in repo.db.report_rows.items
        if row.get("source") == "telegram" and row.get("telegramEventType") == "online"
    ]
    assert len(online_rows) == 1
    assert len([b for b in repo.db.break_events.items if b.get("eventType") == "online"]) == 1


def test_duplicate_telegram_offline_skips_extra_report():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    repo.record_break_event("ta", "online", "2026-04-28T09:00:00Z")
    assert repo.record_break_event("ta", "offline", "2026-04-28T18:00:00Z")["status"] == "day_closed"
    dup = repo.record_break_event("ta", "offline", "2026-04-28T19:00:00Z")
    assert dup["status"] == "duplicate_offline"
    assert dup["sinceOfflineTimeLocal"] == "18:00"
    offline_rows = [row for row in repo.db.report_rows.items if row.get("telegramEventType") == "offline"]
    assert len(offline_rows) == 1
    assert len([b for b in repo.db.break_events.items if b.get("eventType") == "offline"]) == 1


def test_duplicate_afk_prompt_confirm_online_records_break_closed():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    repo.record_break_event("ta", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("ta", "afk", "2026-04-28T12:00:00Z")
    dup = repo.record_break_event("ta", "afk", "2026-04-28T12:05:00Z")
    assert dup["status"] == "duplicate_afk"
    rid = str(dup["reminderId"])
    repo.db.telegram_duplicate_afk_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_duplicate_afk_prompt(rid, "confirm_online", "2026-04-28T12:30:00Z", "ta")
    assert result["ok"]
    assert result["status"] == "duplicate_afk_prompt_confirmed_online"
    assert not repo.db.break_sessions.items


def test_duplicate_afk_prompt_still_afk_closes_prompt_without_online():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    repo.record_break_event("ta", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("ta", "afk", "2026-04-28T12:00:00Z")
    dup = repo.record_break_event("ta", "afk", "2026-04-28T12:05:00Z")
    rid = str(dup["reminderId"])
    repo.db.telegram_duplicate_afk_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_duplicate_afk_prompt(rid, "still_afk", "2026-04-28T12:06:00Z", "ta")
    assert result["ok"]
    assert result["status"] == "duplicate_afk_prompt_still_afk"
    assert len(repo.db.break_sessions.items) == 1


def test_break_close_handles_naive_mongo_datetime():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "startedAt": dt.datetime(2026, 4, 28, 12, 0),
        }
    )

    closed = repo.record_break_event("dmitry_shane", "online", "2026-04-28T12:20:00Z")

    assert closed["status"] == "break_closed"
    assert closed["breakSeconds"] == 20 * 60


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
    assert focus_deltas["activeDeltaSeconds"] == 20
    assert asset_deltas["activeDeltaSeconds"] == 74
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
    assert repo.get_idle_threshold_for_author("Dmitry Shane") == 120
    assert heartbeat_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["activeDeltaSeconds"] == 0


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


def test_live_telegram_summary_adds_open_day_and_break():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "date": "2026-04-28",
            "startedAt": dt.datetime(2026, 4, 28, 9, 0, tzinfo=dt.UTC),
            "daySeconds": 0,
        }
    )
    repo.db.break_sessions.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "telegramUsername": "dmitry_shane",
            "startedAt": dt.datetime(2026, 4, 28, 9, 30, tzinfo=dt.UTC),
        }
    )
    totals = {"daySeconds": 0, "telegramDaySeconds": 0, "breakSeconds": 0}
    authors = {}

    repo._apply_live_telegram_summary(
        authors,
        {},
        totals,
        repo._profiles_by_raw_author(),
        {},
        {},
        "2026-04-28",
        "2026-04-28",
        None,
        dt.datetime(2026, 4, 28, 10, 0, tzinfo=dt.UTC),
    )

    assert totals["telegramDaySeconds"] == 3600
    assert totals["breakSeconds"] == 1800
    assert authors["Dmitry Shane"]["telegramDaySeconds"] == 3600
    assert authors["Dmitry Shane"]["breakSeconds"] == 1800


def test_hourly_break_subtracts_idle_with_active_priority():
    source = _empty_hourly_activity()
    source[16]["activeSeconds"] = 10 * 60
    source[16]["idleSeconds"] = 50 * 60
    breaks = _empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60

    hourly = _apply_breaks_to_hourly_activity(source, breaks)

    assert hourly[16]["activeSeconds"] == 10 * 60
    assert hourly[16]["breakSeconds"] == 30 * 60
    assert hourly[16]["idleSeconds"] == 20 * 60


def test_hourly_break_consumption_prevents_double_counting_across_sources():
    first_source = _empty_hourly_activity()
    first_source[16]["idleSeconds"] = 50 * 60
    second_source = _empty_hourly_activity()
    second_source[16]["idleSeconds"] = 50 * 60
    breaks = _empty_hourly_activity()
    breaks[16]["breakSeconds"] = 30 * 60
    consumed = _empty_hourly_activity()

    first_hourly = _apply_breaks_to_hourly_activity(first_source, breaks, consumed)
    second_hourly = _apply_breaks_to_hourly_activity(second_source, breaks, consumed)

    assert first_hourly[16]["breakSeconds"] == 30 * 60
    assert second_hourly[16]["breakSeconds"] == 0
    assert consumed[16]["breakSeconds"] == 30 * 60


def test_totals_should_use_report_aggregates_not_hourly_buckets():
    source = _empty_hourly_activity()
    source[16]["activeSeconds"] = 60
    source[16]["idleSeconds"] = 60
    breaks = _empty_hourly_activity()
    hourly = _apply_breaks_to_hourly_activity(source, breaks)

    report_active_seconds = 20 * 60
    report_idle_seconds = 60 * 60
    break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly)
    effective_active_seconds = report_active_seconds
    effective_idle_seconds = max(0, report_idle_seconds - break_seconds)

    assert effective_active_seconds == report_active_seconds
    assert effective_idle_seconds == report_idle_seconds


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


def test_hourly_break_splits_across_hours():
    buckets = {("Dmitry Shane", "2026-04-28"): _empty_hourly_activity()}

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 16, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 28, 17, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert buckets[("Dmitry Shane", "2026-04-28")][16]["breakSeconds"] == 10 * 60
    assert buckets[("Dmitry Shane", "2026-04-28")][17]["breakSeconds"] == 20 * 60


def test_hourly_break_splits_across_midnight():
    buckets = {
        ("Dmitry Shane", "2026-04-28"): _empty_hourly_activity(),
        ("Dmitry Shane", "2026-04-29"): _empty_hourly_activity(),
    }

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry Shane",
        dt.datetime(2026, 4, 28, 23, 50, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 0, 20, tzinfo=dt.UTC),
        "UTC",
    )

    assert buckets[("Dmitry Shane", "2026-04-28")][23]["breakSeconds"] == 10 * 60
    assert buckets[("Dmitry Shane", "2026-04-29")][0]["breakSeconds"] == 20 * 60


def test_hourly_break_uses_author_time_zone():
    buckets = {("Dmitry", "2026-04-29"): _empty_hourly_activity()}

    _add_break_interval_to_buckets(
        buckets,
        "Dmitry",
        dt.datetime(2026, 4, 29, 9, 36, 39, tzinfo=dt.UTC),
        dt.datetime(2026, 4, 29, 10, 31, 59, tzinfo=dt.UTC),
        "Europe/Madrid",
    )

    assert buckets[("Dmitry", "2026-04-29")][9]["breakSeconds"] == 0
    assert buckets[("Dmitry", "2026-04-29")][10]["breakSeconds"] == 0
    assert buckets[("Dmitry", "2026-04-29")][11]["breakSeconds"] == 1401
    assert buckets[("Dmitry", "2026-04-29")][12]["breakSeconds"] == 1919


def test_hourly_break_suppresses_small_idle_artifact():
    source = _empty_hourly_activity()
    break_buckets = _empty_hourly_activity()
    source[15]["activeSeconds"] = 553
    source[15]["idleSeconds"] = 2991
    break_buckets[15]["breakSeconds"] = 2806

    hourly = _apply_breaks_to_hourly_activity(source, break_buckets)

    assert hourly[15]["activeSeconds"] == 553
    assert hourly[15]["breakSeconds"] == 2991
    assert hourly[15]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_small_report_gap_as_idle():
    source = _empty_hourly_activity()
    source[14]["activeSeconds"] = 3379

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[14]["activeSeconds"] == 3379
    assert hourly[14]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_past_hour_gap_as_idle():
    source = _empty_hourly_activity()
    source[10]["activeSeconds"] = 1743
    source[10]["idleSeconds"] = 1143

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[10]["activeSeconds"] == 1743
    assert hourly[10]["idleSeconds"] == 1143


def test_hourly_activity_keeps_dmitriy_zero_idle_gap_zero():
    source = _empty_hourly_activity()
    source[18]["activeSeconds"] = 3446

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 3446
    assert hourly[18]["idleSeconds"] == 0
    assert hourly[18]["breakSeconds"] == 0


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
    assert batch["hourlyActivityDelta"][18]["activeSeconds"] == 3600
    assert batch["hourlyActivityDelta"][18]["idleSeconds"] == 0

    hourly = _apply_breaks_to_hourly_activity(batch["hourlyActivityDelta"], _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 3600
    assert hourly[18]["idleSeconds"] == 0


def test_hourly_activity_does_not_infer_current_hour_idle():
    source = _empty_hourly_activity()
    source[18]["activeSeconds"] = 1800

    hourly = _apply_breaks_to_hourly_activity(source, _empty_hourly_activity())

    assert hourly[18]["activeSeconds"] == 1800
    assert hourly[18]["idleSeconds"] == 0
    assert hourly[19]["idleSeconds"] == 0
