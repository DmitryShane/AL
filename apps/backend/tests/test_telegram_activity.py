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
    handle_update,
    send_break_activity_prompt_message,
    send_duplicate_afk_prompt_message,
    send_online_prompt_message,
    send_plain_message,
    send_reminder_message,
    telegram_username,
)
from tests.fakes import fake_repository, set_idle_threshold
from tests.activity_status_helpers import _author_status, _insert_presence_daily_activity


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

def test_telegram_bot_ignores_empty_messages_without_crashing(monkeypatch):
    calls = []
    monkeypatch.setattr("al_backend.telegram_bot.submit_break_event", lambda *args, **kwargs: calls.append(args))

    config = BotConfig(token="token", backend_url="http://backend", allowed_chat_id=123, bot_secret="secret")

    handle_update(
        config,
        {
            "message": {
                "chat": {"id": 123, "type": "group"},
                "from": {"username": "future_artist"},
                "text": "   ",
            }
        },
    )
    handle_update(
        config,
        {
            "message": {
                "chat": {"id": 123, "type": "group"},
                "from": {"username": "future_artist"},
            }
        },
    )

    assert calls == []

def test_telegram_bot_warns_when_offline_has_no_online_today(monkeypatch):
    sent_messages = []

    monkeypatch.setattr(
        "al_backend.telegram_bot.submit_break_event",
        lambda *args, **kwargs: {"ok": True, "status": "offline_without_online"},
    )
    monkeypatch.setattr(
        "al_backend.telegram_bot.send_plain_message",
        lambda token, chat_id, text: sent_messages.append({"token": token, "chat_id": chat_id, "text": text}) or {"ok": True},
    )

    config = BotConfig(token="token", backend_url="http://backend", allowed_chat_id=123, bot_secret="secret")

    handle_update(
        config,
        {
            "message": {
                "chat": {"id": 123, "type": "group"},
                "from": {"username": "future_artist"},
                "text": "offline",
                "date": 1_777_777_777,
            }
        },
    )

    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == 123
    assert "not online today yet" in sent_messages[0]["text"]

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
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")
    hourly_author = next(author for author in summary["hourlyActivityByAuthor"] if author["rawAuthor"] == "Future Artist")
    hourly_by_hour = {hour["hour"]: hour for hour in hourly_author["hourlyActivity"]}

    assert author["telegramToFirstActivitySeconds"] == 77 * 60 + 30
    assert author["idleSeconds"] == 77 * 60 + 30
    assert author["pluginDaySeconds"] == 60
    assert author["rawPluginDaySeconds"] == 60
    assert author["productivity"] == 1.27
    assert hourly_by_hour[9]["totals"]["idleSeconds"] == 3600
    assert hourly_by_hour[10]["totals"]["idleSeconds"] == 17 * 60 + 30

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
            "hourlyActivity": empty_hourly_activity(),
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
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-30", end_date="2026-04-30")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry")

    assert author["telegramToFirstActivitySeconds"] == 9 * 60 + 31

def test_telegram_online_after_offline_restores_normal_presence():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    _insert_presence_daily_activity(repo, dt.datetime(2026, 4, 28, 18, 10, tzinfo=dt.UTC))
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    repo.record_break_event("future_artist", "offline", "2026-04-28T18:05:00Z")
    repo.record_break_event("future_artist", "online", "2026-04-28T18:10:00Z")

    assert _author_status(repo, dt.datetime(2026, 4, 28, 18, 11, tzinfo=dt.UTC)) == "online"

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

def test_telegram_reminder_does_not_repeat_after_offline_without_new_activity():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")

    assert repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 31, tzinfo=dt.UTC)) == []

def test_telegram_reminder_does_not_repeat_after_activity_following_offline_close():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist", "telegramUsername": "future_artist"})
    repo.record_break_event("future_artist", "online", "2026-04-28T09:00:00Z")
    reminder = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 0, tzinfo=dt.UTC))[0]
    repo.close_telegram_day_from_reminder(reminder["reminderId"], "offline", "2026-04-28T19:15:00Z")
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": dt.datetime(2026, 4, 28, 19, 30, tzinfo=dt.UTC),
            "activeSeconds": 60,
            "idleSeconds": 0,
        }
    )

    assert repo.claim_due_telegram_day_reminders(dt.datetime(2026, 4, 28, 19, 31, tzinfo=dt.UTC)) == []

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

def test_telegram_online_prompt_not_scheduled_during_night_overtime_window():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "Europe/Madrid"})

    repo._schedule_telegram_online_prompt_if_needed(
        "A",
        "2026-05-11",
        "dev",
        dt.datetime(2026, 5, 11, 0, 15, tzinfo=dt.UTC),
    )

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

def test_telegram_online_prompt_claim_closes_stale_dates():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    t0 = dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", "2026-04-30", "ual", t0)

    assert repo.claim_due_telegram_online_prompts(dt.datetime(2026, 5, 2, 8, 20, tzinfo=dt.UTC)) == []

    doc = repo.db.telegram_online_prompts.items[0]
    assert doc["status"] == "closed"
    assert doc["closeAction"] == "stale_date"

def test_telegram_day_reminder_not_created_for_stale_day_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "UTC"})
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "A",
            "telegramUsername": "ta",
            "date": "2026-04-30",
            "startedAt": dt.datetime(2026, 4, 30, 8, 0, tzinfo=dt.UTC),
        }
    )

    reminders = repo.claim_due_telegram_day_reminders(dt.datetime(2026, 5, 2, 20, 0, tzinfo=dt.UTC))

    assert reminders == []
    assert repo.db.telegram_day_reminders.items == []

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
    assert telegram_reports[0]["recordedAt"] == "2026-04-30T07:59:00+00:00"
    assert telegram_reports[0]["receivedAt"] == dt.datetime(2026, 4, 30, 7, 59, tzinfo=dt.UTC)

def test_telegram_online_prompt_confirm_uses_first_activity_minus_one_minute_and_is_exempt_from_status_filter():
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
    t0 = dt.datetime(2026, 4, 30, 11, 23, tzinfo=dt.UTC)
    repo._schedule_telegram_online_prompt_if_needed("A", day, "ual", t0)
    rid = repo.db.telegram_online_prompts.items[0]["reminderId"]
    repo.db.telegram_online_prompts.items[0]["status"] = "sent"

    result = repo.close_telegram_online_prompt(rid, "confirm_online", "2026-04-30T18:00:00Z", "ta")

    assert result["ok"]
    telegram_reports = [r for r in repo.db.report_rows.items if r.get("source") == "telegram" and r.get("telegramEventType") == "online"]
    assert len(telegram_reports) == 1
    telegram_row = telegram_reports[0]
    assert telegram_row["recordedAt"] == "2026-04-30T11:22:00+00:00"
    assert telegram_row["receivedAt"] == dt.datetime(2026, 4, 30, 11, 22, tzinfo=dt.UTC)
    assert telegram_row["lastReceivedAt"] == dt.datetime(2026, 4, 30, 11, 22, tzinfo=dt.UTC)
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

def test_closed_telegram_day_does_not_receive_live_delta_from_other_day_session():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Denis Ostrovskiy", "displayName": "Denis Ostrovskiy", "timeZoneId": "Europe/Kyiv"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "telegram",
            "author": "Denis Ostrovskiy",
            "date": "2026-05-07",
            "daySeconds": 32133,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "hourlyActivity": [],
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "date": "2026-05-07",
            "startedAt": dt.datetime(2026, 5, 7, 8, 23, 3, tzinfo=dt.UTC),
            "lastOnlineAt": dt.datetime(2026, 5, 7, 11, 50, 41, tzinfo=dt.UTC),
            "lastOfflineAt": dt.datetime(2026, 5, 7, 17, 18, 36, tzinfo=dt.UTC),
            "daySeconds": 32133,
            "timeZoneId": "Europe/Kyiv",
        }
    )
    repo.db.day_sessions.insert_one(
        {
            "rawAuthor": "Denis Ostrovskiy",
            "date": "2026-05-08",
            "startedAt": dt.datetime(2026, 5, 8, 8, 0, tzinfo=dt.UTC),
            "daySeconds": 0,
            "timeZoneId": "Europe/Kyiv",
        }
    )

    summary = repo.activity_summary(
        start_date="2026-05-07",
        end_date="2026-05-07",
        now=dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC),
    )
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Denis Ostrovskiy")

    assert author["telegramDaySeconds"] == 32133
    assert summary["totals"]["telegramDaySeconds"] == 32133

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

def test_telegram_offline_without_online_does_not_create_activity_rows():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {"rawAuthor": "Dmitry Shane", "telegramUsername": "dmitry_shane", "timeZoneId": "Europe/Madrid"}
    )

    assert repo.record_break_event("dmitry_shane", "online", "2026-05-17T16:10:46Z")["status"] == "online_recorded"
    result = repo.record_break_event("dmitry_shane", "offline", "2026-05-17T22:13:59Z")

    assert result["status"] == "offline_without_online"
    assert result["eventDate"] == "2026-05-18"
    assert result["eventTimeLocal"] == "00:13"
    previous_session = repo.db.day_sessions.find_one({"rawAuthor": "Dmitry Shane", "date": "2026-05-17"})
    assert previous_session is not None
    assert previous_session.get("lastOfflineAt") is None
    assert repo.db.day_sessions.find_one({"rawAuthor": "Dmitry Shane", "date": "2026-05-18"}) is None
    assert not [
        event
        for event in repo.db.break_events.items
        if event.get("rawAuthor") == "Dmitry Shane" and event.get("eventType") == "offline" and event.get("date") == "2026-05-18"
    ]
    assert not [
        row
        for row in repo.db.report_rows.items
        if row.get("author") == "Dmitry Shane" and row.get("telegramEventType") == "offline" and row.get("date") == "2026-05-18"
    ]

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
