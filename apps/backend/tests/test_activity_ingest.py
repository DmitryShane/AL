import datetime as dt
import json
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import quote

import al_backend.discord_bot as discord_bot_module
from fastapi import HTTPException
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
from al_backend.report_worker import ReportWorker, ReportWorkerConfig
from test_protocol import PRIVATE_KEY, _encode
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

def test_submit_report_queues_decoded_payload_without_materializing_events():
    from al_backend.models import ReportIn
    from al_backend.routers.reports import submit_report

    repo = fake_repository()
    payload = _event_payload(event_count=1000)
    repo.db.report_challenges.insert_one(
        {
            "challengeId": "challenge-queued",
            "source": "ual",
            "author": "Queue Author",
            "authorEmail": "queue@example.com",
            "projectId": "bike-rush-2",
            "sessionId": "session-queue",
            "deviceId": "device-queue",
            "privateKeyPem": PRIVATE_KEY,
            "expiresAt": dt.datetime(2099, 6, 12, 12, 0, tzinfo=dt.UTC),
        }
    )

    result = submit_report(
        ReportIn(
            source="ual",
            pluginVersion="0.1.10",
            challengeId="challenge-queued",
            deviceId="device-queue",
            encryptedPacket=_encode(payload),
        ),
        service=repo,
    )

    assert result.ok
    assert result.report_id
    assert len(repo.db.raw_reports.items) == 1
    raw_report = repo.db.raw_reports.items[0]
    assert raw_report["status"] == "queued"
    assert raw_report["payload"]["events"][999]["eventId"] == "queued-999"
    assert raw_report["attempts"] == 0
    assert repo.db.raw_event_batches.items == []
    assert repo.db.raw_activity_events.items == []
    assert repo.db.report_rows.items == []

def test_process_queued_report_materializes_event_batch_and_marks_processed():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.10",
        encrypted_packet="packet",
        challenge_id="challenge-process",
        device_id="device-process",
        payload=_event_payload(event_count=3),
    )

    assert repo.db.raw_reports.items[0]["status"] == "queued"
    assert repo.db.raw_event_batches.items == []

    assert repo.process_queued_report(report_id)

    raw_report = repo.db.raw_reports.find_one({"_id": report_id})
    assert raw_report["status"] == "processed"
    assert raw_report["processedAt"]
    assert len(repo.db.raw_event_batches.items) == 1
    assert len(repo.db.raw_activity_events.items) == 3
    assert repo.db.report_rows.items

def test_queued_report_failure_retries_then_fails_at_attempt_limit():
    repo = fake_repository()
    report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.10",
        encrypted_packet="packet",
        challenge_id="challenge-fail",
        device_id="device-fail",
        payload=_event_payload(event_count=1),
    )

    def fail_save_event_batch(*_args, **_kwargs):
        raise RuntimeError("boom")

    repo._save_event_batch = fail_save_event_batch

    first = repo.claim_next_queued_report(worker_id="worker-1", max_attempts=2)
    assert first is not None
    assert first["attempts"] == 1
    assert repo.process_claimed_report(first, max_attempts=2) is False
    raw_report = repo.db.raw_reports.find_one({"_id": report_id})
    assert raw_report["status"] == "queued"
    assert "boom" in raw_report["lastError"]

    second = repo.claim_next_queued_report(worker_id="worker-1", max_attempts=2)
    assert second is not None
    assert second["attempts"] == 2
    assert repo.process_claimed_report(second, max_attempts=2) is False
    raw_report = repo.db.raw_reports.find_one({"_id": report_id})
    assert raw_report["status"] == "failed"
    assert raw_report["failedAt"]

def test_stale_processing_report_can_be_reclaimed():
    repo = fake_repository()
    report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.10",
        encrypted_packet="packet",
        challenge_id="challenge-stale",
        device_id="device-stale",
        payload=_event_payload(event_count=1),
    )
    first = repo.claim_next_queued_report(worker_id="worker-1", lease_seconds=300)
    assert first is not None
    assert first["leaseOwner"] == "worker-1"
    repo.db.raw_reports.update_one(
        {"_id": report_id},
        {"$set": {"leaseExpiresAt": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)}},
    )

    second = repo.claim_next_queued_report(worker_id="worker-2", lease_seconds=300)

    assert second is not None
    assert second["_id"] == report_id
    assert second["leaseOwner"] == "worker-2"
    assert second["attempts"] == 2

def test_report_worker_run_once_processes_one_queued_report():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.10",
        encrypted_packet="packet",
        challenge_id="challenge-worker",
        device_id="device-worker",
        payload=_event_payload(event_count=2),
    )

    class Container:
        report_ingest = repo

    worker = ReportWorker(
        Container(),
        ReportWorkerConfig(
            poll_interval_seconds=0.1,
            lease_seconds=300,
            max_attempts=5,
            batch_limit=1,
        ),
    )

    assert worker.run_once() == 1
    assert repo.db.raw_reports.find_one({"_id": report_id})["status"] == "processed"
    assert len(repo.db.raw_activity_events.items) == 2

def test_queued_report_retry_reuses_existing_raw_event_batch():
    repo = fake_repository()
    report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.10",
        encrypted_packet="packet",
        challenge_id="challenge-retry-batch",
        device_id="device-retry-batch",
        payload=_event_payload(event_count=2),
    )
    repo.db.raw_event_batches.insert_one(
        {
            "batchId": "existing-batch",
            "rawReportId": report_id,
            "source": "ual",
            "pluginVersion": "0.1.10",
            "author": "Queue Author",
            "authorEmail": "queue@example.com",
            "projectId": "bike-rush-2",
            "sessionId": "session-queue",
            "deviceId": "device-retry-batch",
            "receivedAt": dt.datetime(2026, 6, 12, 10, 0, tzinfo=dt.UTC),
            "eventCount": 2,
            "reportType": "auto",
        }
    )

    assert repo.process_queued_report(report_id)

    assert len(repo.db.raw_event_batches.items) == 1
    assert {event["batchId"] for event in repo.db.raw_activity_events.items} == {"existing-batch"}

def test_submit_report_source_mismatch_rejects_without_queue_write():
    from al_backend.models import ReportIn
    from al_backend.routers.reports import submit_report

    repo = fake_repository()
    repo.db.report_challenges.insert_one(
        {
            "challengeId": "challenge-mismatch",
            "source": "ual",
            "author": "Queue Author",
            "authorEmail": "queue@example.com",
            "projectId": "bike-rush-2",
            "sessionId": "session-queue",
            "deviceId": "device-queue",
            "privateKeyPem": PRIVATE_KEY,
            "expiresAt": dt.datetime(2099, 6, 12, 12, 0, tzinfo=dt.UTC),
        }
    )

    try:
        submit_report(
            ReportIn(
                source="ual",
                pluginVersion="0.1.10",
                challengeId="challenge-mismatch",
                deviceId="device-queue",
                encryptedPacket=_encode({"source": "cur", "author": "Queue Author"}),
            ),
            service=repo,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("submit_report should reject source mismatch")

    assert repo.db.raw_reports.items == []

def _event_payload(event_count: int) -> dict:
    base = dt.datetime(2026, 6, 12, 10, 0, tzinfo=dt.UTC)
    events = []

    for index in range(event_count):
        occurred = base + dt.timedelta(seconds=index * 10)
        events.append(
            {
                "eventId": f"queued-{index}",
                "eventType": "focus" if index == 0 else "editor_input",
                "date": "2026-06-12",
                "occurredAtUtc": occurred.isoformat().replace("+00:00", "Z"),
                "occurredAtLocal": occurred.isoformat(),
            }
        )

    return {
        "source": "ual",
        "author": "Queue Author",
        "authorEmail": "queue@example.com",
        "projectId": "bike-rush-2",
        "sessionId": "session-queue",
        "deviceId": "device-queue",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "sentAt": "2026-06-12T10:10:00Z",
        "events": events,
    }


def _chunk_payload(logical_report_id: str, chunk_index: int, chunk_count: int, events: list[dict], total_event_count: int) -> dict:
    payload = _event_payload(0)
    payload.update(
        {
            "logicalReportId": logical_report_id,
            "chunkIndex": chunk_index,
            "chunkCount": chunk_count,
            "chunkEventCount": len(events),
            "totalEventCount": total_event_count,
            "events": events,
        }
    )
    return payload


def test_chunked_unity_report_waits_until_all_chunks_before_rows():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    payload = _event_payload(3500)
    chunks = [
        payload["events"][0:1000],
        payload["events"][1000:2000],
        payload["events"][2000:3000],
        payload["events"][3000:3500],
    ]

    for index in (2, 1, 3):
        report_id = repo.queue_decoded_report(
            source="ual",
            plugin_version="0.1.12",
            encrypted_packet=f"packet-{index}",
            challenge_id=f"challenge-{index}",
            device_id="device-queue",
            payload=_chunk_payload("logical-1", index, 4, chunks[index - 1], 3500),
        )
        assert repo.process_queued_report(report_id)

    assert len(repo.db.raw_report_chunks.items) == 3
    assert repo.db.raw_event_batches.items == []
    assert repo.db.raw_activity_events.items == []
    assert repo.db.report_rows.items == []
    queue_status = repo.get_reports_queue_status()
    chunk_row = next(row for row in queue_status["recentReports"] if row["id"] == "logical-1")
    assert chunk_row["status"] == "receiving"
    assert chunk_row["chunksReceived"] == 3
    assert chunk_row["chunkCount"] == 4
    assert len(chunk_row["chunks"]) == 3

    final_report_id = repo.queue_decoded_report(
        source="ual",
        plugin_version="0.1.12",
        encrypted_packet="packet-4",
        challenge_id="challenge-4",
        device_id="device-queue",
        payload=_chunk_payload("logical-1", 4, 4, chunks[3], 3500),
    )
    assert repo.process_queued_report(final_report_id)

    assert len(repo.db.raw_event_batches.items) == 1
    assert repo.db.raw_event_batches.items[0]["rawReportId"] == "logical-1"
    assert repo.db.raw_event_batches.items[0]["eventCount"] == 3500
    assert len(repo.db.raw_activity_events.items) == 3500
    assert len(repo.db.report_rows.items) == 1
    queue_status = repo.get_reports_queue_status()
    chunk_row = next(row for row in queue_status["recentReports"] if row["id"] == "logical-1")
    assert chunk_row["status"] == "processed"
    assert chunk_row["chunksReceived"] == 4
    assert chunk_row["chunksProcessed"] == 4
    assert chunk_row["eventsReceived"] == 3500
    assert chunk_row["attempts"] == 1
    assert chunk_row["processingSeconds"] is not None
    assert all(chunk["attempts"] == 1 for chunk in chunk_row["chunks"])


def test_chunked_unity_report_duplicate_chunk_does_not_duplicate_events():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    payload = _event_payload(3)
    chunk = _chunk_payload("logical-dup", 1, 2, payload["events"][:2], 3)

    first = repo.queue_decoded_report("ual", "0.1.12", "packet-1", chunk, "challenge-1", "device-queue")
    duplicate = repo.queue_decoded_report("ual", "0.1.12", "packet-dup", chunk, "challenge-dup", "device-queue")
    second = repo.queue_decoded_report(
        "ual",
        "0.1.12",
        "packet-2",
        _chunk_payload("logical-dup", 2, 2, payload["events"][2:], 3),
        "challenge-2",
        "device-queue",
    )

    assert repo.process_queued_report(first)
    assert repo.process_queued_report(duplicate)
    assert repo.process_queued_report(second)

    assert len(repo.db.raw_report_chunks.items) == 2
    assert len(repo.db.raw_activity_events.items) == 3
    assert len(repo.db.report_rows.items) == 1

def test_deleted_author_profile_blocks_plugin_config_profile_recreation():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Zhdamarov",
            "displayName": "Dmitry Zhdamarov",
            "authorEmail": "dmitry.zhdamarov@mempic.org",
        }
    )

    delete_result = repo.delete_author_profile("Dmitry Zhdamarov")

    result = plugin_config(
        source="cur",
        author="Dmitry Zhdamarov",
        author_email="dmitry.zhdamarov@mempic.org",
        project_id="AL",
        device_id="mac-mini",
        service=repo,
    )

    assert delete_result["ok"]
    assert repo.db.deleted_author_profiles.find_one({"rawAuthor": "Dmitry Zhdamarov"}) is not None
    assert result.enabled is False
    assert repo.db.author_profiles.items == []

def test_deleted_author_profile_blocks_raw_report_writes():
    repo = fake_repository()
    repo.db.deleted_author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Zhdamarov",
            "authorEmail": "dmitry.zhdamarov@mempic.org",
            "deletedAt": dt.datetime(2026, 5, 21, tzinfo=dt.UTC),
        }
    )
    payload = {
        "author": "Dmitry Zhdamarov",
        "authorEmail": "dmitry.zhdamarov@mempic.org",
        "projectId": "AL",
        "sessionId": "session-1",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "events": [
            {
                "eventId": "blocked-1",
                "eventType": "focus",
                "date": "2026-05-21",
                "occurredAtUtc": "2026-05-21T08:00:00Z",
                "occurredAtLocal": "2026-05-21T08:00:00+00:00",
            }
        ],
    }

    report_id = repo.save_report("cur", "1.0.0", "encrypted", payload, "challenge-1")

    assert report_id == ""
    assert repo.db.raw_reports.items == []
    assert repo.db.raw_event_batches.items == []
    assert repo.db.raw_activity_events.items == []
    assert repo.db.report_rows.items == []
    assert repo.db.author_profiles.items == []

def test_idle_only_plugin_batch_does_not_schedule_telegram_online_prompt():
    repo = fake_repository()
    set_idle_threshold(repo, 60)
    repo.db.author_profiles.insert_one({"rawAuthor": "A", "telegramUsername": "ta", "timeZoneId": "Europe/Madrid"})
    received_at = dt.datetime(2026, 5, 19, 5, 10, 1, tzinfo=dt.UTC)
    payload = {
        "author": "A",
        "authorEmail": "",
        "projectId": "p",
        "sessionId": "s",
        "deviceId": "d",
        "timeZoneId": "Europe/Madrid",
        "events": [
            {
                "eventId": "idle-start",
                "eventType": "selection",
                "date": "2026-05-19",
                "occurredAtUtc": "2026-05-19T05:00:00Z",
                "occurredAtLocal": "2026-05-19T07:00:00+02:00",
            },
            {
                "eventId": "idle-end",
                "eventType": "heartbeat",
                "date": "2026-05-19",
                "occurredAtUtc": "2026-05-19T05:10:00Z",
                "occurredAtLocal": "2026-05-19T07:10:00+02:00",
            },
        ],
    }

    repo._save_event_batch("ual", "1.0.0", payload, "raw-1", "auto", received_at, "challenge-1", None)

    assert repo.db.report_rows.items
    assert repo.db.report_rows.items[0]["idleDeltaSeconds"] == 600
    assert repo.db.report_rows.items[0]["activeDeltaSeconds"] == 0
    assert repo.db.telegram_online_prompts.items == []

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
    assert late_deltas["activityCountDeltas"] == []
    assert late_deltas["overtimeActivityCountDeltas"] == [{"type": "focus", "count": 1}]

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
            "source": "dev-ios",
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
            "source": "dev-ios",
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
    daily = repo.db.daily_author_activity.find_one({"author": "Device1", "date": "2026-05-04", "source": "dev-ios"})
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


def test_unity_scene_view_navigation_duration_counts_as_active_time():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    base_event = {
        "source": "ual",
        "author": "Evgeniy Dotsenko",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "date": "2026-06-03",
        "timeZoneId": "UTC",
    }

    first_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:02Z",
            "occurredAtLocal": "2026-06-03T10:00:02+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 2, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:02Z",
                "navigationDurationSeconds": 2,
            },
        }
    )
    second_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:04Z",
            "occurredAtLocal": "2026-06-03T10:00:04+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 4, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:04Z",
                "navigationDurationSeconds": 4,
            },
        }
    )
    duplicate_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:05Z",
            "occurredAtLocal": "2026-06-03T10:00:05+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 5, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:04Z",
                "navigationDurationSeconds": 4,
            },
        }
    )

    assert first_deltas["activeDeltaSeconds"] == 2
    assert second_deltas["activeDeltaSeconds"] == 2
    assert duplicate_deltas["activeDeltaSeconds"] == 0
    daily = repo.db.daily_author_activity.find_one({"author": "Evgeniy Dotsenko", "date": "2026-06-03", "source": "ual"})
    assert daily is not None
    assert daily["activeSeconds"] == 4
    assert {item["type"]: item["count"] for item in daily["activityCounts"]} == {"scene_view_navigation": 3}


def test_unity_scene_view_navigation_heartbeat_keeps_navigation_time_active():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    base_event = {
        "source": "ual",
        "author": "Evgeniy Dotsenko",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "date": "2026-06-03",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:10Z",
            "occurredAtLocal": "2026-06-03T10:00:10+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 10, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:10Z",
                "navigationDurationSeconds": 10,
            },
        }
    )
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-06-03T10:01:20Z",
            "occurredAtLocal": "2026-06-03T10:01:20+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 1, 20, tzinfo=dt.UTC),
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Evgeniy Dotsenko", "date": "2026-06-03", "source": "ual"})
    assert daily is not None
    assert daily["activeSeconds"] == 10
    assert heartbeat_deltas["idleDeltaSeconds"] == 70
    assert daily["idleSeconds"] == 70


def test_unity_editor_input_and_scene_object_events_keep_gspawn_workflow_active():
    repo = fake_repository()
    received_at = dt.datetime(2026, 6, 3, 10, 10, tzinfo=dt.UTC)
    payload = {
        "author": "Dmitry Shane",
        "authorEmail": "dmitry@example.com",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "editor-input-1",
                "eventType": "editor_input",
                "date": "2026-06-03",
                "occurredAtUtc": "2026-06-03T10:00:00Z",
                "occurredAtLocal": "2026-06-03T10:00:00+00:00",
                "metadata": {"name": "GSpawn", "state": "MouseDown", "isGSpawnContext": True},
            },
            {
                "eventId": "object-created-1",
                "eventType": "scene_object_created",
                "date": "2026-06-03",
                "occurredAtUtc": "2026-06-03T10:04:30Z",
                "occurredAtLocal": "2026-06-03T10:04:30+00:00",
                "metadata": {
                    "name": "GSpawn",
                    "state": "CreateGameObjectHierarchy",
                    "isGSpawnContext": True,
                },
            },
            {
                "eventId": "object-changed-1",
                "eventType": "scene_object_changed",
                "date": "2026-06-03",
                "occurredAtUtc": "2026-06-03T10:09:00Z",
                "occurredAtLocal": "2026-06-03T10:09:00+00:00",
                "metadata": {
                    "name": "GSpawn",
                    "state": "ChangeGameObjectOrComponentProperties",
                    "isGSpawnContext": True,
                },
            },
            {
                "eventId": "heartbeat-1",
                "eventType": "heartbeat",
                "date": "2026-06-03",
                "occurredAtUtc": "2026-06-03T10:10:00Z",
                "occurredAtLocal": "2026-06-03T10:10:00+00:00",
            },
        ],
    }

    repo._save_event_batch("ual", "0.1.10", payload, "raw-1", "auto", received_at, "challenge-1", None)

    assert repo.db.report_rows.items
    row = repo.db.report_rows.items[0]
    assert row["activeDeltaSeconds"] == 540
    assert row["idleDeltaSeconds"] == 0
    assert {item["type"]: item["count"] for item in row["activityCountDeltas"]} == {
        "editor_input": 1,
        "object_created": 1,
        "object_changed": 1,
    }


def test_unity_scene_view_navigation_accounts_idle_gap_before_navigation():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    base_event = {
        "source": "ual",
        "author": "Evgeniy Dotsenko",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "date": "2026-06-03",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "selection",
            "occurredAtUtc": "2026-06-03T10:00:00Z",
            "occurredAtLocal": "2026-06-03T10:00:00+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 0, tzinfo=dt.UTC),
        }
    )
    navigation_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:10:00Z",
            "occurredAtLocal": "2026-06-03T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 10, 0, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:08:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:10:00Z",
                "navigationDurationSeconds": 120,
            },
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Evgeniy Dotsenko", "date": "2026-06-03", "source": "ual"})
    assert daily is not None
    assert navigation_deltas["activeDeltaSeconds"] == 120
    assert navigation_deltas["idleDeltaSeconds"] == 480
    assert daily["activeSeconds"] == 120
    assert daily["idleSeconds"] == 480


def test_unity_scene_view_navigation_duplicate_does_not_suppress_later_idle():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    base_event = {
        "source": "ual",
        "author": "Evgeniy Dotsenko",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "date": "2026-06-03",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:10Z",
            "occurredAtLocal": "2026-06-03T10:00:10+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 10, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:10Z",
                "navigationDurationSeconds": 10,
            },
        }
    )
    duplicate_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:01:10Z",
            "occurredAtLocal": "2026-06-03T10:01:10+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 1, 10, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:10Z",
                "navigationDurationSeconds": 10,
            },
        }
    )
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-06-03T10:02:10Z",
            "occurredAtLocal": "2026-06-03T10:02:10+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 2, 10, tzinfo=dt.UTC),
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Evgeniy Dotsenko", "date": "2026-06-03", "source": "ual"})
    assert daily is not None
    assert duplicate_deltas["activeDeltaSeconds"] == 0
    assert duplicate_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["idleDeltaSeconds"] == 120
    assert daily["activeSeconds"] == 10
    assert daily["idleSeconds"] == 120


def test_unity_scene_view_navigation_new_session_resets_duration_delta():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    base_event = {
        "source": "ual",
        "author": "Evgeniy Dotsenko",
        "projectId": "bike-rush-2",
        "sessionId": "unity-session",
        "deviceId": "unity-device",
        "date": "2026-06-03",
        "timeZoneId": "UTC",
    }
    repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:00:10Z",
            "occurredAtLocal": "2026-06-03T10:00:10+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 0, 10, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:00:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:00:10Z",
                "navigationDurationSeconds": 10,
            },
        }
    )
    new_session_deltas = repo._apply_raw_event_to_aggregates(
        {
            **base_event,
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:01:05Z",
            "occurredAtLocal": "2026-06-03T10:01:05+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 1, 5, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:01:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:01:05Z",
                "navigationDurationSeconds": 5,
            },
        }
    )

    daily = repo.db.daily_author_activity.find_one({"author": "Evgeniy Dotsenko", "date": "2026-06-03", "source": "ual"})
    assert daily is not None
    assert new_session_deltas["activeDeltaSeconds"] == 5
    assert daily["activeSeconds"] == 15


def test_late_unity_scene_view_navigation_does_not_roll_back_author_state():
    repo = fake_repository()
    repo.db.interval_settings.insert_one({"kind": "global", "idleThresholdSeconds": 60})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "cur",
            "author": "Evgeniy Dotsenko",
            "projectId": "al",
            "sessionId": "cursor-session",
            "deviceId": "mac-mini",
            "date": "2026-06-03",
            "timeZoneId": "UTC",
            "eventType": "focus",
            "occurredAtUtc": "2026-06-03T10:20:00Z",
            "occurredAtLocal": "2026-06-03T10:20:00+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 20, 0, tzinfo=dt.UTC),
        }
    )
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Evgeniy Dotsenko",
            "projectId": "bike-rush-2",
            "sessionId": "unity-session",
            "deviceId": "unity-device",
            "date": "2026-06-03",
            "timeZoneId": "UTC",
            "eventType": "scene_view_navigation",
            "occurredAtUtc": "2026-06-03T10:10:00Z",
            "occurredAtLocal": "2026-06-03T10:10:00+00:00",
            "receivedAt": dt.datetime(2026, 6, 3, 10, 25, 0, tzinfo=dt.UTC),
            "metadata": {
                "firstNavigationAtUtc": "2026-06-03T10:08:00Z",
                "lastNavigationAtUtc": "2026-06-03T10:10:00Z",
                "navigationDurationSeconds": 120,
            },
        }
    )

    state = repo.db.aggregate_session_state.find_one({"_id": "author_day_activity_v1|Evgeniy Dotsenko|2026-06-03"})
    assert state is not None
    assert state["state"]["lastActivityAt"] == "2026-06-03T10:20:00+00:00"
    assert state["state"]["lastAccountingAt"] == "2026-06-03T10:20:00+00:00"


def test_device_ios_payload_is_stored_as_ios_device_source():
    repo = fake_repository()
    repo.save_report(
        source="dev",
        plugin_version="0.1.0",
        encrypted_packet="packet",
        challenge_id="challenge",
        device_id="ios-device-1",
        payload={
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "sessionId": "ios-session",
            "deviceId": "ios-device-1",
            "events": [
                {
                    "eventId": "ios-click-1",
                    "eventType": "click",
                    "occurredAtUtc": "2026-05-04T10:00:00Z",
                    "occurredAtLocal": "2026-05-04T10:00:00+00:00",
                    "date": "2026-05-04",
                    "metadata": {"platform": "IPhonePlayer"},
                },
                {
                    "eventId": "ios-hold-1",
                    "eventType": "hold",
                    "occurredAtUtc": "2026-05-04T10:00:05Z",
                    "occurredAtLocal": "2026-05-04T10:00:05+00:00",
                    "date": "2026-05-04",
                    "metadata": {"platform": "IPhonePlayer", "holdDurationSeconds": 5},
                },
            ],
        },
    )

    assert repo.db.raw_reports.items[-1]["source"] == "dev-ios"
    assert repo.db.raw_event_batches.items[-1]["source"] == "dev-ios"
    assert repo.db.raw_activity_events.items[0]["source"] == "dev-ios"
    assert repo.db.daily_author_activity.find_one({"author": "Device1", "source": "dev-ios"}) is not None
    assert repo.db.report_rows.count_documents({"author": "Device1", "source": "dev-ios"}) == 1

def test_device_android_payload_is_stored_as_android_device_source():
    repo = fake_repository()
    repo.save_report(
        source="dev",
        plugin_version="0.1.0",
        encrypted_packet="packet",
        challenge_id="challenge",
        device_id="android-device-1",
        payload={
            "source": "dev",
            "author": "Device1",
            "projectId": "Bike Rush 2",
            "sessionId": "android-session",
            "deviceId": "android-device-1",
            "events": [
                {
                    "eventId": "android-click-1",
                    "eventType": "click",
                    "occurredAtUtc": "2026-05-04T10:00:00Z",
                    "occurredAtLocal": "2026-05-04T10:00:00+00:00",
                    "date": "2026-05-04",
                    "metadata": {"runtimePlatform": "Android"},
                },
                {
                    "eventId": "android-heartbeat-1",
                    "eventType": "heartbeat",
                    "occurredAtUtc": "2026-05-04T10:00:10Z",
                    "occurredAtLocal": "2026-05-04T10:00:10+00:00",
                    "date": "2026-05-04",
                    "metadata": {"runtimePlatform": "Android"},
                },
            ],
        },
    )

    assert repo.db.raw_reports.items[-1]["source"] == "dev-android"
    assert repo.db.raw_event_batches.items[-1]["source"] == "dev-android"
    assert repo.db.daily_author_activity.find_one({"author": "Device1", "source": "dev-android"}) is not None

def test_device_legacy_editor_metadata_is_ignored():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Device1", "displayName": "Device1"})
    legacy_editor_metadata = {"is" + "Editor": True, "is" + "EditorPlayMode": True}

    report_id = repo.save_report(
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
                    "metadata": legacy_editor_metadata,
                },
                {
                    "eventId": "editor-hold-1",
                    "eventType": "hold",
                    "occurredAtUtc": "2026-05-04T10:00:05Z",
                    "occurredAtLocal": "2026-05-04T10:00:05+00:00",
                    "date": "2026-05-04",
                    "metadata": {**legacy_editor_metadata, "holdDurationSeconds": 5},
                },
            ],
        },
    )

    assert report_id == ""
    assert repo.db.raw_reports.items == []
    assert repo.db.raw_activity_events.items == []
    assert repo.db.raw_event_batches.items == []
    assert repo.db.daily_author_activity.items == []
    assert repo.db.report_rows.items == []
    assert repo.db.device_report_identities.items == []

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


def test_unity_scene_touched_appears_in_activity_summary_saved_files():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})

    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "deviceId": "mac-mini",
            "date": "2026-05-05",
            "eventType": "selection",
            "occurredAtUtc": "2026-05-05T09:59:00Z",
            "occurredAtLocal": "2026-05-05T11:59:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 5, 9, 59, 1, tzinfo=dt.UTC),
        }
    )
    deltas = repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "deviceId": "mac-mini",
            "date": "2026-05-05",
            "eventType": "scene_touched",
            "occurredAtUtc": "2026-05-05T10:00:00Z",
            "occurredAtLocal": "2026-05-05T12:00:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 5, 10, 0, 1, tzinfo=dt.UTC),
            "metadata": {
                "path": "Assets/Project/Levels/Level.011/Level.011.unity",
                "name": "Level.011",
                "state": "scene_activity",
            },
        }
    )
    repo._apply_raw_event_to_aggregates(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "bike-rush-2",
            "deviceId": "mac-mini",
            "date": "2026-05-05",
            "eventType": "editor_input",
            "occurredAtUtc": "2026-05-05T10:01:00Z",
            "occurredAtLocal": "2026-05-05T12:01:00+02:00",
            "receivedAt": dt.datetime(2026, 5, 5, 10, 1, 1, tzinfo=dt.UTC),
        }
    )

    summary = repo.activity_summary(start_date="2026-05-05", end_date="2026-05-05")
    author = next(item for item in summary["authors"] if item["rawAuthor"] == "Future Artist")
    expected = {"path": "Assets/Project/Levels/Level.011/Level.011.unity", "name": "Level.011", "saveCount": 1}

    assert deltas["savedPrefabDeltas"] == [expected]
    assert expected in author["savedPrefabs"]
    assert next(item for item in author["savedPrefabsBySource"] if item["source"] == "ual")["savedPrefabs"] == [expected]


def test_device_report_author_is_stable_for_same_device_id():
    repo = fake_repository()

    first = repo.resolve_device_report_author("dev", "advertising-id-1")
    second = repo.resolve_device_report_author("dev", "advertising-id-1")
    third = repo.resolve_device_report_author("dev", "advertising-id-2")

    assert first == "Device1"
    assert second == "Device1"
    assert third == "Device2"

def test_device_report_author_uses_next_free_device_number():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-2", "rawAuthor": "Device2"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-7", "rawAuthor": "Device7"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-8", "rawAuthor": "Device8"})

    assert repo.resolve_device_report_author("dev", "advertising-id-new") == "Device9"

def test_device_report_author_does_not_reuse_historical_device_name_without_identity():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-1", "rawAuthor": "Device1"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-2", "rawAuthor": "Device2"})
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-7", "rawAuthor": "Device7"})
    repo.db.report_rows.insert_one({"author": "Device8", "date": "2026-05-05", "source": "dev"})

    assert repo.resolve_device_report_author("dev", "advertising-id-new") == "Device9"

def test_new_device_report_author_does_not_reuse_aliased_device_name():
    repo = fake_repository()
    repo.db.device_report_identities.insert_one({"source": "dev", "deviceIdHash": "hash-7", "rawAuthor": "Device7"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Device7", "targetRawAuthor": "Igor Mats"})

    raw_author = repo.resolve_device_report_author("dev", "advertising-id-new")

    assert raw_author == "Device8"
    assert repo.resolve_author_alias(raw_author) == "Device8"
    assert repo.resolve_author_alias("Device7") == "Igor Mats"

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
            "source": "dev-ios",
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
            "source": "dev-ios",
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
    assert result["rebuiltActivitySnapshots"]["processed"] == [{"date": "2026-05-02", "rawAuthor": "Future Artist"}]
    assert result["rebuiltActivitySnapshots"]["composedDates"] == ["2026-05-02"]
    assert repo.db.activity_author_day_summary_snapshots.find_one({"date": "2026-05-02", "rawAuthor": "Future Artist"}) is not None
    assert repo.db.activity_day_summary_snapshots.find_one({"date": "2026-05-02", "view": "activity-day"}) is not None

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


def test_count_only_raw_event_batch_creates_report_row():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Dmitry Shane", "displayName": "Dmitry Shane"})
    received_at = dt.datetime(2026, 5, 23, 15, 30, tzinfo=dt.UTC)
    payload = {
        "author": "Dmitry Shane",
        "authorEmail": "dmitry@example.com",
        "projectId": "AL",
        "sessionId": "codex-session",
        "deviceId": "mac-mini",
        "timeZoneId": "UTC",
        "timeZoneDisplayName": "UTC",
        "events": [
            {
                "eventId": "codex-progress-1",
                "eventType": "external",
                "date": "2026-05-23",
                "occurredAtUtc": "2026-05-23T15:30:00Z",
                "occurredAtLocal": "2026-05-23T15:30:00+00:00",
                "metadata": {"codexEventType": "task_progress"},
            }
        ],
    }

    repo._save_event_batch("codex", "0.1.2", payload, "raw-codex-1", "auto", received_at, "challenge-1", None)

    assert repo.db.report_rows.count_documents({"source": "codex", "author": "Dmitry Shane"}) == 1
    row = repo.db.report_rows.find_one({"source": "codex", "author": "Dmitry Shane"})
    assert row["activityType"] == "codex_task_progress"
    assert row["activityCountDeltas"] == [{"type": "codex_task_progress", "count": 1}]
    assert row["projectId"] == "AL"

    repo.rebuild_aggregates_if_needed(force=True)

    rebuilt = repo.db.report_rows.find_one({"source": "codex", "author": "Dmitry Shane"})
    assert rebuilt is not None
    assert rebuilt["activityType"] == "codex_task_progress"
    assert rebuilt["activityCountDeltas"] == [{"type": "codex_task_progress", "count": 1}]


def test_codex_activity_accounts_active_time_between_events():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    first = {
        "eventId": "codex-start",
        "source": "codex",
        "author": "Dmitry Shane",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-05-23",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 30, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T17:30:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 30, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "session_started"},
    }
    second = {
        **first,
        "eventId": "codex-progress",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 32, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T17:32:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 32, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "task_progress"},
    }

    first_deltas = repo._apply_raw_event_to_aggregates(first)
    second_deltas = repo._apply_raw_event_to_aggregates(second)

    assert first_deltas["activeDeltaSeconds"] == 0
    assert second_deltas["activeDeltaSeconds"] == 120
    assert second_deltas["idleDeltaSeconds"] == 0
    assert second_deltas["activityCountDeltas"] == [{"type": "codex_task_progress", "count": 1}]


def test_codex_activity_caps_active_time_without_idle_tail():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    first = {
        "eventId": "codex-start",
        "source": "codex",
        "author": "Dmitry Shane",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-05-23",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 30, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T17:30:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 30, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "session_started"},
    }
    second = {
        **first,
        "eventId": "codex-progress",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 50, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T17:50:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 50, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "task_progress"},
    }

    repo._apply_raw_event_to_aggregates(first)
    second_deltas = repo._apply_raw_event_to_aggregates(second)

    assert second_deltas["activeDeltaSeconds"] == 300
    assert second_deltas["idleDeltaSeconds"] == 0


def test_codex_deferred_night_interval_after_telegram_online_counts_as_active_time():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Dmitry Shane",
            "displayName": "Dmitry Shane",
            "telegramUsername": "dmitryshane",
            "timeZoneId": "Europe/Madrid",
        }
    )
    night_event = {
        "eventId": "codex-night-progress",
        "source": "codex",
        "author": "Dmitry Shane",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-06-10",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 6, 10, 1, 14, 56, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-06-10T03:14:56+02:00",
        "receivedAt": dt.datetime(2026, 6, 10, 1, 14, 57, tzinfo=dt.UTC),
        "timeZoneId": "Europe/Madrid",
        "metadata": {"codexEventType": "task_progress"},
    }
    day_event = {
        **night_event,
        "eventId": "codex-day-start",
        "occurredAtUtc": dt.datetime(2026, 6, 10, 10, 9, 18, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-06-10T12:09:18+02:00",
        "receivedAt": dt.datetime(2026, 6, 10, 10, 9, 19, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "session_started"},
    }

    repo._apply_raw_event_to_aggregates(night_event)
    repo.record_break_event("dmitryshane", "online", "2026-06-10T09:04:18Z")
    day_deltas = repo._apply_raw_event_to_aggregates(day_event)

    assert day_deltas["activeDeltaSeconds"] == 300
    assert day_deltas["overtimeActiveDeltaSeconds"] == 0
    daily = repo.db.daily_author_activity.find_one({"source": "codex", "author": "Dmitry Shane", "date": "2026-06-10"})
    assert daily["activeSeconds"] == 300
    assert daily["overtimeActiveSeconds"] == 0


def test_non_codex_external_activity_keeps_external_type():
    repo = fake_repository()
    event = {
        "eventId": "external-1",
        "source": "bal",
        "author": "Blender Artist",
        "projectId": "Scene",
        "deviceId": "mac-mini",
        "date": "2026-05-23",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 30, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T15:30:00+00:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 30, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "task_progress"},
    }

    deltas = repo._apply_raw_event_to_aggregates(event)

    assert deltas["activityCountDeltas"] == [{"type": "external", "count": 1}]


def test_plugin_counts_create_daily_aggregate_after_night_overtime_before_telegram_online():
    repo = fake_repository()
    hourly = empty_hourly_activity()
    hourly[1]["overtimeActiveSeconds"] = 120
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "projectId": "unity",
            "date": "2026-05-23",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 120,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [{"type": "focus", "count": 1}],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": hourly,
        }
    )
    event = {
        "eventId": "figma-count-before-online",
        "source": "fch",
        "author": "Dmitry Shane",
        "projectId": "figma",
        "deviceId": "chrome",
        "date": "2026-05-23",
        "eventType": "selection",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 16, 39, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T18:39:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 16, 39, 1, tzinfo=dt.UTC),
        "timeZoneId": "Europe/Madrid",
        "timeZoneDisplayName": "Europe/Madrid",
    }

    deltas = repo._apply_raw_event_to_aggregates(event)

    assert deltas["activityCountDeltas"] == [{"type": "select", "count": 1}]
    daily = repo.db.daily_author_activity.find_one({"source": "fch", "author": "Dmitry Shane"})
    assert daily is not None
    assert daily["activityCounts"] == [{"type": "select", "count": 1}]


def test_codex_counts_before_telegram_online_after_night_overtime():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    hourly = empty_hourly_activity()
    hourly[1]["overtimeActiveSeconds"] = 120
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "projectId": "unity",
            "date": "2026-05-23",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 120,
            "activityCounts": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [{"type": "focus", "count": 1}],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": hourly,
        }
    )
    first = {
        "eventId": "codex-start-before-online",
        "source": "codex",
        "author": "Dmitry Shane",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-05-23",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 16, 39, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T18:39:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 16, 39, 1, tzinfo=dt.UTC),
        "timeZoneId": "Europe/Madrid",
        "timeZoneDisplayName": "Europe/Madrid",
        "metadata": {"codexEventType": "session_started"},
    }
    second = {
        **first,
        "eventId": "codex-progress-before-online",
        "eventType": "external",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 16, 41, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T18:41:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 16, 41, 1, tzinfo=dt.UTC),
        "metadata": {"codexEventType": "task_progress"},
    }

    first_deltas = repo._apply_raw_event_to_aggregates(first)
    second_deltas = repo._apply_raw_event_to_aggregates(second)

    assert first_deltas["activityCountDeltas"] == [{"type": "codex_session_started", "count": 1}]
    assert second_deltas["activeDeltaSeconds"] == 120
    assert second_deltas["activityCountDeltas"] == [{"type": "codex_task_progress", "count": 1}]
    daily = repo.db.daily_author_activity.find_one({"source": "codex", "author": "Dmitry Shane"})
    assert daily is not None
    assert daily["activeSeconds"] == 120
    assert daily["activityCounts"] == [
        {"type": "codex_session_started", "count": 1},
        {"type": "codex_task_progress", "count": 1},
    ]


def test_codex_native_event_type_creates_activity_count():
    repo = fake_repository()
    event = {
        "eventId": "codex-progress-1",
        "source": "codex",
        "author": "Dmitry Shane",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-05-23",
        "eventType": "task_progress",
        "occurredAtUtc": dt.datetime(2026, 5, 23, 15, 30, tzinfo=dt.UTC),
        "occurredAtLocal": "2026-05-23T17:30:00+02:00",
        "receivedAt": dt.datetime(2026, 5, 23, 15, 30, 1, tzinfo=dt.UTC),
        "metadata": {},
    }

    deltas = repo._apply_raw_event_to_aggregates(event)

    assert deltas["activityCountDeltas"] == [{"type": "codex_task_progress", "count": 1}]


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

def test_night_overtime_after_previous_day_offline_counts_new_day_activity():
    repo = fake_repository()
    set_idle_threshold(repo, 300)
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Igor Mats",
            "displayName": "Igor Mats",
            "telegramUsername": "igor_mats",
            "timeZoneId": "America/Vancouver",
        }
    )
    for break_event in [
        {
            "telegramUsername": "igor_mats",
            "rawAuthor": "Igor Mats",
            "eventType": "online",
            "timestamp": dt.datetime(2026, 5, 12, 14, 0, tzinfo=dt.UTC),
            "date": "2026-05-12",
            "timeZoneId": "America/Vancouver",
        },
        {
            "telegramUsername": "igor_mats",
            "rawAuthor": "Igor Mats",
            "eventType": "offline",
            "timestamp": dt.datetime(2026, 5, 13, 0, 51, 7, tzinfo=dt.UTC),
            "date": "2026-05-12",
            "timeZoneId": "America/Vancouver",
        },
    ]:
        repo.db.break_events.insert_one(break_event)

    base_event = {
        "source": "vsc",
        "author": "Igor Mats",
        "projectId": "AL",
        "deviceId": "mac-mini",
        "date": "2026-05-13",
        "timeZoneId": "America/Vancouver",
        "timeZoneDisplayName": "America/Vancouver",
        "receivedAt": dt.datetime(2026, 5, 13, 7, 0, 1, tzinfo=dt.UTC),
    }

    repo._apply_raw_event_to_aggregates(
        base_event
        | {
            "eventType": "selection",
            "occurredAtUtc": "2026-05-13T07:00:01Z",
            "occurredAtLocal": "2026-05-13T00:00:01-07:00",
        }
    )
    active_deltas = repo._apply_raw_event_to_aggregates(
        base_event
        | {
            "eventType": "selection",
            "occurredAtUtc": "2026-05-13T07:02:01Z",
            "occurredAtLocal": "2026-05-13T00:02:01-07:00",
            "receivedAt": dt.datetime(2026, 5, 13, 7, 2, 1, tzinfo=dt.UTC),
        }
    )
    heartbeat_deltas = repo._apply_raw_event_to_aggregates(
        base_event
        | {
            "eventType": "heartbeat",
            "occurredAtUtc": "2026-05-13T08:15:00Z",
            "occurredAtLocal": "2026-05-13T01:15:00-07:00",
            "receivedAt": dt.datetime(2026, 5, 13, 8, 15, tzinfo=dt.UTC),
        }
    )

    assert active_deltas["overtimeActiveDeltaSeconds"] == 120
    assert active_deltas["activeDeltaSeconds"] == 0
    assert heartbeat_deltas["idleDeltaSeconds"] == 0
    assert heartbeat_deltas["activeDeltaSeconds"] == 0
    assert heartbeat_deltas["overtimeActiveDeltaSeconds"] == 0

    daily = repo.db.daily_author_activity.find_one({"author": "Igor Mats", "source": "vsc", "date": "2026-05-13"}) or {}
    assert daily["overtimeActiveSeconds"] == 120
    assert daily["activeSeconds"] == 0
    assert daily["idleSeconds"] == 0

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

def test_codex_activity_project_appears_in_saved_files_without_file_save():
    repo = fake_repository()
    repo.db.author_profiles.insert_one({"rawAuthor": "Future Artist", "displayName": "Future Artist"})
    repo.db.daily_author_activity.insert_one(
        {
            "source": "codex",
            "author": "Future Artist",
            "projectId": "AL",
            "date": "2026-04-29",
            "activeSeconds": 120,
            "idleSeconds": 0,
            "activityCounts": [{"type": "codex_session_started", "count": 3}],
            "savedPrefabs": [],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

    assert {"path": "codex:AL", "name": "AL", "projectId": "AL", "saveCount": 3} in author["savedPrefabs"]

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

def test_zero_delta_event_save_report_touches_report_liveness_without_report_rows():
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
    assert profile.get("lastRawReportReceivedAt") is not None
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
    repo.db.report_rows.insert_one(
        {
            "source": "cur",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:00:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.UTC),
            "activeDeltaSeconds": 120,
            "idleDeltaSeconds": 0,
        }
    )
    repo.db.report_rows.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "recordedAt": "2026-04-29T09:01:00+00:00",
            "receivedAt": dt.datetime(2026, 4, 29, 9, 1, tzinfo=dt.UTC),
            "activeDeltaSeconds": 90,
            "idleDeltaSeconds": 0,
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

def test_activity_summary_hides_source_breakdown_until_source_has_report_rows():
    repo = fake_repository()
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Dmitry Shane",
            "date": "2026-04-29",
            "activeSeconds": 0,
            "idleSeconds": 0,
            "activityCounts": [{"type": "play_mode", "count": 3}],
            "hourlyActivity": empty_hourly_activity(),
        }
    )

    summary = repo.activity_summary(start_date="2026-04-29", end_date="2026-04-29")
    author = next(author for author in summary["authors"] if author["rawAuthor"] == "Dmitry Shane")

    assert author["activityMix"] == []
    assert author["savedPrefabs"] == []
    assert author["activityMixBySource"] == []

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
    assert asset_deltas["activeDeltaSeconds"] == 0
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
            "occurredAtUtc": "2026-05-01T20:01:33Z",
            "occurredAtLocal": "2026-05-01T13:01:33-07:00",
            "metadata": {"path": "Assets/Project/Materials/Road.mat", "name": "Road"},
        },
    ]:
        repo.db.raw_activity_events.insert_one(event)

    repo.rebuild_aggregates_if_needed(force=True)

    unity_row = repo.db.report_rows.find_one({"author": author, "source": "ual", "batchId": "unity-batch"})
    daily = repo.db.daily_author_activity.find_one({"author": author, "source": "ual", "date": "2026-05-01"})
    assert unity_row is None
    assert daily["activeSeconds"] == 0
    assert {"type": "asset_saved", "count": 1} in daily["activityCounts"]
    assert daily["savedPrefabs"] == []

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
    assert repo.get_interval_for_author("Dmitry Shane", source="dev-ios") == 30
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
    assert repo.get_interval_for_author("Dmitry Shane", source="dev-android") == 1
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

def test_unity_scene_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "scene_saved",
            "metadata": {
                "path": "Assets/Project/Levels/Level.005/Level.005.unity",
                "name": "Level.005",
            },
        }
    )

    assert saved == {
        "path": "Assets/Project/Levels/Level.005/Level.005.unity",
        "name": "Level.005",
        "saveCount": 1,
    }


def test_unity_scene_touched_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "scene_touched",
            "metadata": {
                "path": "Assets/Project/Levels/Level.011/Level.011.unity",
                "name": "Level.011",
            },
        }
    )

    assert saved == {
        "path": "Assets/Project/Levels/Level.011/Level.011.unity",
        "name": "Level.011",
        "saveCount": 1,
    }


def test_unity_scene_touched_requires_unity_scene_path():
    assert (
        _saved_prefab_delta(
            {
                "source": "ual",
                "eventType": "scene_touched",
                "metadata": {"path": "Assets/Project/Levels/Level.011/Level.011.prefab", "name": "Level.011"},
            }
        )
        is None
    )
    assert (
        _saved_prefab_delta(
            {
                "source": "vsc",
                "eventType": "scene_touched",
                "metadata": {"path": "Assets/Project/Levels/Level.011/Level.011.unity", "name": "Level.011"},
            }
        )
        is None
    )


def test_unity_asset_saved_is_not_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "asset_saved",
            "metadata": {
                "path": "Assets/Project/Materials/Road.mat",
                "name": "Road",
            },
        }
    )

    assert saved is None


def test_unity_imported_model_is_not_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "asset_saved",
            "metadata": {
                "path": "Assets/Art/Environment/Garage.Scene/Models/Garage.Scene.fbx",
                "name": "Garage.Scene",
            },
        }
    )

    assert saved is None


def test_unity_meta_asset_saved_is_not_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "asset_saved",
            "metadata": {
                "path": "Assets/Art/Environment/Garage.Scene/Textures/Garage.Scene.png.meta",
                "name": "Garage.Scene.png.meta",
            },
        }
    )

    assert saved is None


def test_unity_generated_texture_asset_is_not_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "asset_saved",
            "metadata": {
                "path": "Packages/com.mempic.ad.provider/Runtime/Textures/Texture.asset",
                "name": "Texture",
            },
        }
    )

    assert saved is None


def test_unity_prefab_saved_is_counted_as_saved_file():
    saved = _saved_prefab_delta(
        {
            "source": "ual",
            "eventType": "prefab_saved",
            "metadata": {
                "path": "Assets/Project/Prefabs/Boost.000.prefab",
                "name": "Boost.000",
            },
        }
    )

    assert saved == {
        "path": "Assets/Project/Prefabs/Boost.000.prefab",
        "name": "Boost.000",
        "saveCount": 1,
    }


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
