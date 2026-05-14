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
    assert notifications[0]["telegramTemplate"] == DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE
    assert "Meeting summary" in repo.recent_meeting_activity()[1]["recording"]["telegramMessage"]
    assert "Discord summaries" in repo.recent_meeting_activity()[1]["recording"]["telegramMessage"]

def test_meeting_recording_summary_uses_voice_event_window_when_recording_started_late():
    repo = fake_repository()

    for discord_user_id, raw_author, username in (
        ("1", "Dmitry Shane", "dmitryshane"),
        ("2", "Denis Ostrovskiy", "vedamir"),
        ("3", "Igor Mats", "igor.mats"),
    ):
        repo.db.author_profiles.insert_one(
            {
                "rawAuthor": raw_author,
                "displayName": raw_author,
                "telegramUsername": username.replace(".", ""),
                "discordUserId": discord_user_id,
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

    for discord_user_id, raw_author, username, joined_at, left_at in (
        ("1", "Dmitry Shane", "dmitryshane", "2026-05-14T14:51:14+00:00", "2026-05-14T15:20:11+00:00"),
        ("2", "Denis Ostrovskiy", "vedamir", "2026-05-14T14:51:23+00:00", "2026-05-14T15:20:12+00:00"),
        ("3", "Igor Mats", "igor.mats", "2026-05-14T14:51:19+00:00", "2026-05-14T15:20:19+00:00"),
    ):
        repo.record_discord_voice_event(discord_user_id, username, "join", "guild", "channel", joined_at)
        repo.record_discord_voice_event(discord_user_id, username, "leave", "guild", "channel", left_at)

    class FakeSummary:
        transcript = "The team discussed production meeting summary duration."
        summary = "Discussed:\n- Production meeting summary duration.\n\nDecisions:\n- Use the voice channel window.\n\nAction items:\n- Ship the fix.\n\nOpen questions:\n- None."

    result = repo.process_meeting_recording_finished(
        recording_id="recording-late",
        guild_id="guild",
        channel_id="channel",
        started_at="2026-05-14T15:14:44+00:00",
        ended_at="2026-05-14T15:20:19+00:00",
        participant_discord_user_ids=["1", "2"],
        participant_names=["Dmitry", "Denis"],
        audio_path="/tmp/missing.wav",
        summary_generator=lambda path, people, language, prompt_template, progress_callback=None: FakeSummary(),
    )
    recording = repo.db.meeting_recordings.find_one({"recordingId": "recording-late"})
    summary = repo.db.meeting_summaries.find_one({"recordingId": "recording-late"})

    assert result["status"] == "summary_created"
    assert recording["startedAt"] == dt.datetime(2026, 5, 14, 14, 51, 14, tzinfo=dt.UTC)
    assert recording["endedAt"] == dt.datetime(2026, 5, 14, 15, 20, 19, tzinfo=dt.UTC)
    assert recording["durationSeconds"] == 1745
    assert recording["participantNames"] == ["dmitryshane", "vedamir", "igor.mats"]
    assert summary["durationSeconds"] == 1745
    assert summary["participantNames"] == ["dmitryshane", "vedamir", "igor.mats"]

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

def test_meeting_summary_message_uses_custom_telegram_template():
    message = format_meeting_summary_message(
        {
            "startedAt": "2026-05-01T10:00:00+00:00",
            "durationSeconds": 180,
            "participantNames": ["Dmitry", "Igor"],
            "telegramTemplate": "Daily meeting {date}\nPeople: {participants}\n{summary}",
        },
        "Discussed:\n- Backend status UI.",
    )

    assert message == "Daily meeting 2026-05-01\nPeople: @Dmitry, @Igor\nDiscussed:\n- Backend status UI."

def test_meeting_summary_message_falls_back_on_invalid_telegram_template():
    message = format_meeting_summary_message(
        {
            "startedAt": "2026-05-01T10:00:00+00:00",
            "durationSeconds": 180,
            "participantNames": ["Dmitry", "Igor"],
            "telegramTemplate": "Broken {missing}",
        },
        "Discussed:\n- Backend status UI.",
    )

    assert "Meeting summary" in message
    assert "Participants: @Dmitry, @Igor" in message

def test_meeting_recording_notification_message_starts_with_hi_and_lists_participants_without_mentions():
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

    assert start_message == "Hi dmitryshane, igormats. Your meeting has started. Hope it goes smoothly!"
    assert end_message == "Hi dmitryshane, igormats. Your meeting has ended. Thanks everyone, please wait for the summary."

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
    repo.db.meeting_events.insert_one(
        {
            "discordUserId": "123",
            "discordUsername": "future",
            "rawAuthor": "Future Artist",
            "eventType": "live",
            "timestamp": dt.datetime(2026, 5, 4, 10, 15, tzinfo=dt.UTC),
            "date": "2026-05-04",
            "meetingSeconds": 600,
        }
    )
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
    assert not any(item["itemType"] == "voice_event" and item["eventType"] == "live" for item in items)
    assert any(item["itemType"] == "recording" and item["recording"]["recordingId"] == "recording-1" for item in items)

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
