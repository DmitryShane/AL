from __future__ import annotations

import datetime as dt
import os
from typing import Any

from pymongo import DESCENDING

from ..activity_math import (
    MICROSECONDS_PER_SECOND,
    _add_meeting_interval_to_buckets,
    _coerce_datetime,
    _empty_event_deltas,
    _empty_hourly_activity,
    _iso,
    _looks_like_missing_transcript_summary,
    _looks_like_no_work_content_summary,
    _meeting_audio_quality_status,
    _new_id,
    _normalize_discord_user_id,
    _normalize_telegram_username,
    _parse_timestamp,
    _telegram_event_date,
    _valid_time_zone_id,
)
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


MEETING_RECORDING_SUCCESS_STATUSES = {
    "queued_for_summary",
    "uploading_audio",
    "transcribing_openai",
    "summarizing_openai",
    "waiting_for_telegram",
    "telegram_claimed",
    "telegram_sent",
}


class DiscordMeetingService(MongoComposableMixin):
    def materialize_live_meeting_reports(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(dt.UTC)
        inserted = 0

        for session in self.db.meeting_sessions.find({}, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            interval_seconds = max(1, int(composed(self).get_interval_for_author(raw_author)))
            last_report_at = _coerce_datetime(session.get("lastLiveReportAt")) or started_at
            next_report_at = last_report_at + dt.timedelta(seconds=interval_seconds)

            while next_report_at <= now:
                inserted += self._insert_discord_meeting_live_report_row(session, last_report_at, next_report_at)
                last_report_at = next_report_at
                next_report_at = last_report_at + dt.timedelta(seconds=interval_seconds)

            if last_report_at != (_coerce_datetime(session.get("lastLiveReportAt")) or started_at):
                self.db.meeting_sessions.update_one(
                    {"discordUserId": session.get("discordUserId")},
                    {"$set": {"lastLiveReportAt": last_report_at, "updatedAt": now}},
                )

        return inserted

    def _meeting_participant_telegram_usernames(
        self,
        participant_discord_user_ids: list[str],
        participant_names: list[str],
    ) -> list[str]:
        discord_ids = [_normalize_discord_user_id(item) for item in participant_discord_user_ids]
        lookup_ids = [item for item in discord_ids if item]
        profiles_by_discord_id = {
            str(profile.get("discordUserId") or ""): profile
            for profile in self.db.author_profiles.find(
                {"discordUserId": {"$in": lookup_ids}},
                {"_id": 0, "discordUserId": 1, "telegramUsername": 1},
            )
        }
        resolved: list[str] = []

        for index, discord_id in enumerate(discord_ids):
            fallback_name = participant_names[index] if index < len(participant_names) else ""
            profile = profiles_by_discord_id.get(discord_id, {})
            telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
            resolved.append(telegram_username or str(fallback_name or "").strip())

        return [item for item in resolved if item]

    def record_discord_voice_event(
        self,
        discord_user_id: str,
        discord_username: str | None,
        event_type: str,
        guild_id: str | None = None,
        channel_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        normalized_discord_user_id = _normalize_discord_user_id(discord_user_id)
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"discordUserId": normalized_discord_user_id})

        if not profile:
            return {"ok": False, "error": "Unknown Discord user"}

        raw_author = profile["rawAuthor"]
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)
        normalized_discord_username = str(discord_username or profile.get("discordUsername") or "").strip()
        event_type = event_type if event_type in {"join", "leave", "reconcile"} else "reconcile"
        composed(self).invalidate_activity_summary_cache([event_date])

        self.db.meeting_events.insert_one(
            {
                "discordUserId": normalized_discord_user_id,
                "discordUsername": normalized_discord_username,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "guildId": str(guild_id or ""),
                "channelId": str(channel_id or ""),
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
            }
        )

        if normalized_discord_username and normalized_discord_username != profile.get("discordUsername"):
            self.db.author_profiles.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"discordUsername": normalized_discord_username, "updatedAt": received_at}},
            )

        if event_type in {"join", "reconcile"}:
            session = self.db.meeting_sessions.find_one({"discordUserId": normalized_discord_user_id})

            if session:
                self._insert_discord_meeting_report_row(
                    raw_author,
                    normalized_discord_user_id,
                    normalized_discord_username,
                    event_type,
                    event_time,
                    event_date,
                    time_zone_id,
                    received_at,
                    "meeting_already_started",
                    guild_id,
                    channel_id,
                )
                return {"ok": True, "status": "meeting_already_started"}

            self.db.meeting_sessions.update_one(
                {"discordUserId": normalized_discord_user_id},
                {
                    "$set": {
                        "discordUserId": normalized_discord_user_id,
                        "discordUsername": normalized_discord_username,
                        "rawAuthor": raw_author,
                        "guildId": str(guild_id or ""),
                        "channelId": str(channel_id or ""),
                        "startedAt": event_time,
                        "date": event_date,
                        "timeZoneId": time_zone_id,
                    }
                },
                upsert=True,
            )
            self._insert_discord_meeting_report_row(
                raw_author,
                normalized_discord_user_id,
                normalized_discord_username,
                event_type,
                event_time,
                event_date,
                time_zone_id,
                received_at,
                "meeting_started",
                guild_id,
                channel_id,
            )
            return {"ok": True, "status": "meeting_started"}

        meeting_result = self._close_meeting_session(
            normalized_discord_user_id,
            raw_author,
            normalized_discord_username,
            event_time,
            received_at,
            guild_id,
            channel_id,
        )
        status = "meeting_closed" if meeting_result else "meeting_leave_without_join"
        self._insert_discord_meeting_report_row(
            raw_author,
            normalized_discord_user_id,
            normalized_discord_username,
            event_type,
            event_time,
            event_date,
            time_zone_id,
            received_at,
            status,
            guild_id,
            channel_id,
            meeting_result,
        )
        return {"ok": True, "status": status, **meeting_result}

    def _close_meeting_session(
        self,
        normalized_discord_user_id: str,
        raw_author: str,
        discord_username: str,
        event_time: dt.datetime,
        received_at: dt.datetime,
        guild_id: str | None,
        channel_id: str | None,
    ) -> dict[str, Any]:
        session = self.db.meeting_sessions.find_one({"discordUserId": normalized_discord_user_id})

        if not session:
            return {}

        started_at = _coerce_datetime(session["startedAt"]) or event_time
        meeting_seconds = max(0, int((event_time - started_at).total_seconds()))
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        meeting_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
        self.db.meeting_sessions.delete_one({"discordUserId": normalized_discord_user_id})
        self.db.meeting_intervals.insert_one(
            {
                "discordUserId": normalized_discord_user_id,
                "discordUsername": discord_username or session.get("discordUsername", ""),
                "rawAuthor": raw_author,
                "guildId": str(guild_id or session.get("guildId") or ""),
                "channelId": str(channel_id or session.get("channelId") or ""),
                "startedAt": started_at,
                "endedAt": event_time,
                "date": meeting_date,
                "timeZoneId": time_zone_id,
                "meetingSeconds": meeting_seconds,
                "createdAt": received_at,
            }
        )
        return {"meetingSeconds": meeting_seconds}

    def _insert_discord_meeting_report_row(
        self,
        raw_author: str,
        discord_user_id: str,
        discord_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        guild_id: str | None,
        channel_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        should_materialize = getattr(self, "_should_materialize_aggregate_date", None)

        if callable(should_materialize) and not should_materialize(event_date, raw_author):
            return

        deltas = _empty_event_deltas()
        self.db.report_rows.insert_one(
            {
                "source": "discord",
                "pluginVersion": "discord-bot",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "discord",
                "sessionId": discord_user_id,
                "deviceId": "",
                "date": event_date,
                "recordedAt": event_time.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": event_time.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "meeting",
                "activityType": f"meeting_{event_type}",
                "discordEventType": event_type,
                "discordStatus": status,
                "discordUserId": discord_user_id,
                "discordUsername": discord_username,
                "metadata": {"guildId": str(guild_id or ""), "channelId": str(channel_id or ""), **(metadata or {})},
                **deltas,
            }
        )
        composed(self)._schedule_telegram_online_prompt_if_needed(
            raw_author,
            event_date,
            "discord",
            event_time,
        )

    def _insert_discord_meeting_live_report_row(
        self,
        session: dict[str, Any],
        started_at: dt.datetime,
        ended_at: dt.datetime,
    ) -> int:
        raw_author = str(session.get("rawAuthor") or "Unknown User")
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(ended_at, time_zone_id)
        discord_user_id = str(session.get("discordUserId") or "")
        meeting_seconds = max(0, int((ended_at - started_at).total_seconds()))

        if meeting_seconds <= 0:
            return 0

        should_materialize = getattr(self, "_should_materialize_aggregate_date", None)

        if callable(should_materialize) and not should_materialize(event_date, raw_author):
            return 0

        report_id = f"discord-meeting-live:{discord_user_id}:{ended_at.isoformat()}"

        if self.db.report_rows.find_one({"reportId": report_id}, {"_id": 1}):
            return 0

        hourly_activity_delta = _empty_hourly_activity()
        _add_meeting_interval_to_buckets(
            {(raw_author, event_date): hourly_activity_delta},
            raw_author,
            started_at,
            ended_at,
            time_zone_id,
        )
        deltas = _empty_event_deltas()
        deltas["meetingSeconds"] = meeting_seconds
        deltas["meetingMicroseconds"] = meeting_seconds * MICROSECONDS_PER_SECOND
        deltas["hourlyActivityDelta"] = hourly_activity_delta
        row = {
            "reportId": report_id,
            "source": "discord",
            "pluginVersion": "discord-bot",
            "author": raw_author,
            "authorEmail": "",
            "projectId": "discord",
            "sessionId": discord_user_id,
            "deviceId": "",
            "date": event_date,
            "recordedAt": ended_at.isoformat(),
            "receivedAt": ended_at,
            "lastRecordedAt": ended_at.isoformat(),
            "lastReceivedAt": ended_at,
            "timeZoneId": time_zone_id,
            "timeZoneDisplayName": time_zone_id,
            "reportType": "meeting",
            "activityType": "meeting_live",
            "discordEventType": "live",
            "discordStatus": "meeting_live",
            "discordUserId": discord_user_id,
            "discordUsername": str(session.get("discordUsername") or ""),
            "metadata": {
                "guildId": str(session.get("guildId") or ""),
                "channelId": str(session.get("channelId") or ""),
                "live": True,
                "startedAt": started_at.isoformat(),
                "endedAt": ended_at.isoformat(),
            },
            **deltas,
        }
        self.db.report_rows.insert_one(row)
        composed(self)._update_daily_author_activity(row, deltas)
        composed(self).invalidate_activity_summary_cache([event_date])
        return 1

    def record_discord_meeting_auto_afk(
        self,
        discord_user_id: str,
        discord_username: str | None,
        guild_id: str | None = None,
        meeting_channel_id: str | None = None,
        afk_channel_id: str | None = None,
        solo_started_at: str | None = None,
        moved_at: str | None = None,
        threshold_seconds: int | None = None,
    ) -> dict[str, Any]:
        normalized_discord_user_id = _normalize_discord_user_id(discord_user_id)
        solo_start = _parse_timestamp(solo_started_at)
        moved_time = _parse_timestamp(moved_at)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"discordUserId": normalized_discord_user_id})

        if not profile:
            return {"ok": False, "error": "Unknown Discord user"}

        raw_author = profile["rawAuthor"]
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(solo_start, time_zone_id)
        normalized_discord_username = str(discord_username or profile.get("discordUsername") or "").strip()
        auto_afk_event_id = f"{normalized_discord_user_id}:{solo_start.isoformat()}"
        threshold_seconds = int(threshold_seconds or composed(self).get_discord_settings()["meetingAutoAfkTimeoutSeconds"])

        if self.db.meeting_events.find_one({"autoAfkEventId": auto_afk_event_id}, {"_id": 1}):
            return {"ok": True, "status": "auto_afk_already_recorded"}

        composed(self).invalidate_activity_summary_cache([event_date])
        meeting_result: dict[str, Any] = {}
        session = self.db.meeting_sessions.find_one({"discordUserId": normalized_discord_user_id})

        if session:
            meeting_result = self._close_meeting_session(
                normalized_discord_user_id,
                raw_author,
                normalized_discord_username,
                solo_start,
                received_at,
                guild_id,
                meeting_channel_id,
            )

        self.db.meeting_events.insert_one(
            {
                "discordUserId": normalized_discord_user_id,
                "discordUsername": normalized_discord_username,
                "rawAuthor": raw_author,
                "eventType": "auto_afk",
                "guildId": str(guild_id or ""),
                "channelId": str(meeting_channel_id or ""),
                "afkChannelId": str(afk_channel_id or ""),
                "timestamp": moved_time,
                "soloStartedAt": solo_start,
                "movedAt": moved_time,
                "thresholdSeconds": threshold_seconds,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "autoAfkEventId": auto_afk_event_id,
                "createdAt": received_at,
            }
        )
        self._insert_discord_meeting_report_row(
            raw_author,
            normalized_discord_user_id,
            normalized_discord_username,
            "reconcile",
            moved_time,
            event_date,
            time_zone_id,
            received_at,
            "meeting_auto_afk",
            guild_id,
            meeting_channel_id,
            {
                "autoAfkEventId": auto_afk_event_id,
                "soloStartedAt": solo_start.isoformat(),
                "movedAt": moved_time.isoformat(),
                "afkChannelId": str(afk_channel_id or ""),
                "thresholdSeconds": threshold_seconds,
                **meeting_result,
            },
        )
        self._schedule_telegram_meeting_auto_afk_notification(
            auto_afk_event_id,
            raw_author,
            telegram_username,
            event_date,
            time_zone_id,
            solo_start,
            moved_time,
            threshold_seconds,
            meeting_result,
        )
        return {"ok": True, "status": "meeting_auto_afk", "autoAfkEventId": auto_afk_event_id, **meeting_result}

    def _schedule_telegram_meeting_auto_afk_notification(
        self,
        auto_afk_event_id: str,
        raw_author: str,
        telegram_username: str,
        event_date: str,
        time_zone_id: str,
        solo_started_at: dt.datetime,
        moved_at: dt.datetime,
        threshold_seconds: int,
        meeting_result: dict[str, Any],
    ) -> None:
        if not telegram_username:
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_meeting_auto_afk_notifications.update_one(
            {"autoAfkEventId": auto_afk_event_id},
            {
                "$setOnInsert": {
                    "reminderId": _new_id(),
                    "autoAfkEventId": auto_afk_event_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": telegram_username,
                    "date": event_date,
                    "timeZoneId": time_zone_id,
                    "soloStartedAt": solo_started_at,
                    "movedAt": moved_at,
                    "excludedSeconds": max(0, int((moved_at - solo_started_at).total_seconds())),
                    "thresholdSeconds": threshold_seconds,
                    "meetingSeconds": int(meeting_result.get("meetingSeconds", 0)),
                    "status": "pending",
                    "createdAt": now,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )

    def record_meeting_recording_started(
        self,
        *,
        recording_id: str,
        guild_id: str | None,
        channel_id: str | None,
        started_at: str,
        participant_discord_user_ids: list[str],
        participant_names: list[str],
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        started = _parse_timestamp(started_at)
        self.db.meeting_recordings.update_one(
            {"recordingId": recording_id},
            {
                "$setOnInsert": {
                    "recordingId": recording_id,
                    "guildId": str(guild_id or ""),
                    "channelId": str(channel_id or ""),
                    "startedAt": started,
                    "participantDiscordUserIds": participant_discord_user_ids,
                    "participantNames": participant_names,
                    "status": "recording",
                    "createdAt": now,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        return {"ok": True, "status": "recording_started"}

    def process_meeting_recording_finished(
        self,
        *,
        recording_id: str,
        guild_id: str | None,
        channel_id: str | None,
        started_at: str,
        ended_at: str,
        participant_discord_user_ids: list[str],
        participant_names: list[str],
        audio_frame_count: int = 0,
        non_silent_frame_count: int = 0,
        corrupted_packet_count: int = 0,
        unknown_source_frame_count: int = 0,
        bot_frame_count: int = 0,
        empty_pcm_frame_count: int = 0,
        silence_padding_frame_count: int = 0,
        out_of_order_frame_count: int = 0,
        mixed_user_count: int = 0,
        per_user_frame_counts: dict[str, int] | None = None,
        per_user_non_silent_frame_counts: dict[str, int] | None = None,
        listen_error_count: int = 0,
        listen_error: str = "",
        audio_quality_status: str = "",
        audio_size_bytes: int = 0,
        audio_path: str,
        summary_generator: Any,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        started = _parse_timestamp(started_at)
        ended = _parse_timestamp(ended_at)
        duration_seconds = max(0, int((ended - started).total_seconds()))
        settings = composed(self).get_discord_settings()
        audio_stats = {
            "audioFrameCount": max(0, int(audio_frame_count or 0)),
            "nonSilentFrameCount": max(0, int(non_silent_frame_count or 0)),
            "corruptedPacketCount": max(0, int(corrupted_packet_count or 0)),
            "unknownSourceFrameCount": max(0, int(unknown_source_frame_count or 0)),
            "botFrameCount": max(0, int(bot_frame_count or 0)),
            "emptyPcmFrameCount": max(0, int(empty_pcm_frame_count or 0)),
            "silencePaddingFrameCount": max(0, int(silence_padding_frame_count or 0)),
            "outOfOrderFrameCount": max(0, int(out_of_order_frame_count or 0)),
            "mixedUserCount": max(0, int(mixed_user_count or 0)),
            "perUserFrameCounts": per_user_frame_counts or {},
            "perUserNonSilentFrameCounts": per_user_non_silent_frame_counts or {},
            "listenErrorCount": max(0, int(listen_error_count or 0)),
            "listenError": str(listen_error or "")[:1000],
            "audioSizeBytes": max(0, int(audio_size_bytes or 0)),
        }
        audio_stats["audioQualityStatus"] = audio_quality_status or _meeting_audio_quality_status(audio_stats)
        self._update_meeting_recording_pipeline_status(
            recording_id,
            "uploading_audio",
            ended_at=ended,
            duration_seconds=duration_seconds,
            updated_at=now,
            extra_fields=audio_stats,
        )

        if len(participant_discord_user_ids) < int(settings["meetingSummaryMinParticipants"]):
            status = "skipped_not_enough_participants"
            self._mark_meeting_recording_finished(recording_id, status, ended, duration_seconds, now)
            return {"ok": True, "status": status}

        if duration_seconds < int(settings["meetingSummaryMinDurationSeconds"]):
            status = "skipped_too_short"
            self._mark_meeting_recording_finished(recording_id, status, ended, duration_seconds, now)
            return {"ok": True, "status": status}

        if audio_stats["audioQualityStatus"] == "corrupted":
            status = "skipped_corrupted_audio"
            self._mark_meeting_recording_finished(recording_id, status, ended, duration_seconds, now)
            return {"ok": True, "status": status}

        if audio_stats["audioQualityStatus"] == "silent":
            status = "skipped_silent_audio"
            self._mark_meeting_recording_finished(recording_id, status, ended, duration_seconds, now)
            return {"ok": True, "status": status}

        existing = self.db.meeting_summaries.find_one({"recordingId": recording_id}, {"_id": 0})

        if existing:
            return {"ok": True, "status": "summary_already_created", "summaryId": existing.get("summaryId")}

        try:
            result = summary_generator(
                audio_path,
                participant_names,
                str(settings["meetingSummaryLanguage"]),
                str(settings["meetingSummaryPrompt"]),
                progress_callback=lambda status: self._update_meeting_recording_pipeline_status(recording_id, status),
            )
        except Exception as exc:
            status = "summary_failed"
            self.db.meeting_recordings.update_one(
                {"recordingId": recording_id},
                {"$set": {"status": status, "error": str(exc), "endedAt": ended, "durationSeconds": duration_seconds, "updatedAt": now}},
                upsert=True,
            )
            return {"ok": False, "status": status, "error": str(exc)}

        transcript = str(getattr(result, "transcript", "") or "").strip()
        summary = str(getattr(result, "summary", "") or "").strip()

        if (
            not transcript
            or len(transcript) < 20
            or not summary
            or _looks_like_missing_transcript_summary(summary)
            or _looks_like_no_work_content_summary(summary)
        ):
            status = "skipped_empty_transcript"
            self._mark_meeting_recording_finished(recording_id, status, ended, duration_seconds, now)
            return {"ok": True, "status": status}

        summary_id = _new_id()
        participant_telegram_usernames = self._meeting_participant_telegram_usernames(
            participant_discord_user_ids,
            participant_names,
        )
        self.db.meeting_summaries.insert_one(
            {
                "summaryId": summary_id,
                "recordingId": recording_id,
                "guildId": str(guild_id or ""),
                "channelId": str(channel_id or ""),
                "startedAt": started,
                "endedAt": ended,
                "durationSeconds": duration_seconds,
                "participantDiscordUserIds": participant_discord_user_ids,
                "participantNames": participant_names,
                "participantTelegramUsernames": participant_telegram_usernames,
                "summary": summary,
                "status": "pending",
                "createdAt": now,
                "updatedAt": now,
            }
        )
        self.db.meeting_recordings.update_one(
            {"recordingId": recording_id},
            {
                "$set": {
                    "status": "waiting_for_telegram",
                    "endedAt": ended,
                    "durationSeconds": duration_seconds,
                    "summaryId": summary_id,
                    "updatedAt": now,
                },
                "$unset": {"error": ""},
            },
            upsert=True,
        )
        return {"ok": True, "status": "summary_created", "summaryId": summary_id}

    def queue_meeting_recording_summary_processing(
        self,
        *,
        recording_id: str,
        ended_at: str,
        duration_seconds: int,
        audio_frame_count: int = 0,
        non_silent_frame_count: int = 0,
        corrupted_packet_count: int = 0,
        unknown_source_frame_count: int = 0,
        bot_frame_count: int = 0,
        empty_pcm_frame_count: int = 0,
        silence_padding_frame_count: int = 0,
        out_of_order_frame_count: int = 0,
        mixed_user_count: int = 0,
        per_user_frame_counts: dict[str, int] | None = None,
        per_user_non_silent_frame_counts: dict[str, int] | None = None,
        listen_error_count: int = 0,
        listen_error: str = "",
        audio_quality_status: str = "",
        audio_size_bytes: int = 0,
    ) -> dict[str, Any]:
        audio_stats = {
            "audioFrameCount": max(0, int(audio_frame_count or 0)),
            "nonSilentFrameCount": max(0, int(non_silent_frame_count or 0)),
            "corruptedPacketCount": max(0, int(corrupted_packet_count or 0)),
            "unknownSourceFrameCount": max(0, int(unknown_source_frame_count or 0)),
            "botFrameCount": max(0, int(bot_frame_count or 0)),
            "emptyPcmFrameCount": max(0, int(empty_pcm_frame_count or 0)),
            "silencePaddingFrameCount": max(0, int(silence_padding_frame_count or 0)),
            "outOfOrderFrameCount": max(0, int(out_of_order_frame_count or 0)),
            "mixedUserCount": max(0, int(mixed_user_count or 0)),
            "perUserFrameCounts": per_user_frame_counts or {},
            "perUserNonSilentFrameCounts": per_user_non_silent_frame_counts or {},
            "listenErrorCount": max(0, int(listen_error_count or 0)),
            "listenError": str(listen_error or "")[:1000],
            "audioSizeBytes": max(0, int(audio_size_bytes or 0)),
        }
        audio_stats["audioQualityStatus"] = audio_quality_status or _meeting_audio_quality_status(audio_stats)
        self._update_meeting_recording_pipeline_status(
            recording_id,
            "queued_for_summary",
            ended_at=_parse_timestamp(ended_at),
            duration_seconds=duration_seconds,
            extra_fields=audio_stats,
        )
        return {"ok": True, "status": "summary_queued"}

    def process_queued_meeting_recording_summary(
        self,
        *,
        delete_audio_after_processing: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return self.process_meeting_recording_finished(**kwargs)
        finally:
            if delete_audio_after_processing:
                audio_path = str(kwargs.get("audio_path") or "")

                if audio_path:
                    try:
                        os.remove(audio_path)
                    except FileNotFoundError:
                        pass

    def _update_meeting_recording_pipeline_status(
        self,
        recording_id: str,
        status: str,
        *,
        ended_at: dt.datetime | None = None,
        duration_seconds: int | None = None,
        updated_at: dt.datetime | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        now = updated_at or dt.datetime.now(dt.UTC)
        fields: dict[str, Any] = {
            "status": status,
            "updatedAt": now,
        }

        if ended_at is not None:
            fields["endedAt"] = ended_at

        if duration_seconds is not None:
            fields["durationSeconds"] = duration_seconds

        if extra_fields:
            fields.update(extra_fields)

        operation: dict[str, Any] = {"$set": fields}

        if status in MEETING_RECORDING_SUCCESS_STATUSES:
            operation["$unset"] = {"error": ""}

        self.db.meeting_recordings.update_one({"recordingId": recording_id}, operation, upsert=True)

    def _mark_meeting_recording_finished(
        self,
        recording_id: str,
        status: str,
        ended_at: dt.datetime,
        duration_seconds: int,
        updated_at: dt.datetime,
    ) -> None:
        self.db.meeting_recordings.update_one(
            {"recordingId": recording_id},
            {
                "$set": {
                    "status": status,
                    "endedAt": ended_at,
                    "durationSeconds": duration_seconds,
                    "updatedAt": updated_at,
                }
            },
            upsert=True,
        )

    def mark_meeting_recording_failed(self, *, recording_id: str, ended_at: str, error: str) -> dict[str, Any]:
        ended = _parse_timestamp(ended_at)
        recording = self.db.meeting_recordings.find_one({"recordingId": recording_id}, {"_id": 0, "startedAt": 1}) or {}
        started = _coerce_datetime(recording.get("startedAt"))
        duration_seconds = 0

        if started:
            duration_seconds = max(0, int((ended - started).total_seconds()))

        self.db.meeting_recordings.update_one(
            {"recordingId": recording_id},
            {
                "$set": {
                    "status": "recording_failed",
                    "endedAt": ended,
                    "durationSeconds": duration_seconds,
                    "error": error,
                    "updatedAt": dt.datetime.now(dt.UTC),
                }
            },
            upsert=True,
        )
        return {"ok": True, "status": "recording_failed"}

    def update_meeting_recording_status(self, *, recording_id: str, status: str) -> dict[str, Any]:
        self._update_meeting_recording_pipeline_status(recording_id, status)
        return {"ok": True, "status": status}

    def recent_meeting_recordings(self, limit: int = 10) -> list[dict[str, Any]]:
        summaries_by_recording_id = {
            str(summary.get("recordingId") or ""): summary
            for summary in self.db.meeting_summaries.find({}, {"_id": 0})
            if summary.get("recordingId")
        }
        items: list[dict[str, Any]] = []

        for recording in self.db.meeting_recordings.find({}, {"_id": 0}).sort("startedAt", DESCENDING).limit(limit):
            summary = summaries_by_recording_id.get(str(recording.get("recordingId") or ""))
            status = str(recording.get("status") or "")

            if summary:
                summary_status = str(summary.get("status") or "")

                if summary.get("telegramSentAt") or summary.get("sentAt"):
                    status = "telegram_sent"
                elif status not in {"telegram_claimed"} and summary_status:
                    status = f"summary_{summary_status}"

            items.append(
                {
                    "recordingId": recording.get("recordingId", ""),
                    "summaryId": (summary or {}).get("summaryId") or recording.get("summaryId"),
                    "status": status,
                    "recordingStatus": recording.get("status", ""),
                    "summaryStatus": (summary or {}).get("status"),
                    "startedAt": _iso(recording.get("startedAt")),
                    "endedAt": _iso(recording.get("endedAt")),
                    "durationSeconds": int(recording.get("durationSeconds") or 0),
                    "participantNames": recording.get("participantNames", []),
                    "participantCount": len(recording.get("participantNames") or []),
                    "audioFrameCount": int(recording.get("audioFrameCount") or 0),
                    "nonSilentFrameCount": int(recording.get("nonSilentFrameCount") or 0),
                    "corruptedPacketCount": int(recording.get("corruptedPacketCount") or 0),
                    "unknownSourceFrameCount": int(recording.get("unknownSourceFrameCount") or 0),
                    "botFrameCount": int(recording.get("botFrameCount") or 0),
                    "emptyPcmFrameCount": int(recording.get("emptyPcmFrameCount") or 0),
                    "silencePaddingFrameCount": int(recording.get("silencePaddingFrameCount") or 0),
                    "outOfOrderFrameCount": int(recording.get("outOfOrderFrameCount") or 0),
                    "mixedUserCount": int(recording.get("mixedUserCount") or 0),
                    "perUserFrameCounts": recording.get("perUserFrameCounts") or {},
                    "perUserNonSilentFrameCounts": recording.get("perUserNonSilentFrameCounts") or {},
                    "listenErrorCount": int(recording.get("listenErrorCount") or 0),
                    "listenError": recording.get("listenError") or "",
                    "audioQualityStatus": recording.get("audioQualityStatus") or "",
                    "audioSizeBytes": int(recording.get("audioSizeBytes") or 0),
                    "recipient": (summary or {}).get("recipient"),
                    "telegramSentAt": _iso((summary or {}).get("telegramSentAt") or (summary or {}).get("sentAt")),
                    "error": recording.get("error") or (summary or {}).get("error"),
                    "updatedAt": _iso(recording.get("updatedAt")),
                }
            )

        return items

    def recent_meeting_activity(self, limit: int = 40) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        for recording in self.recent_meeting_recordings(limit=limit):
            timestamp = recording.get("updatedAt") or recording.get("endedAt") or recording.get("startedAt") or ""
            items.append(
                {
                    "itemType": "recording",
                    "id": f"recording:{recording.get('recordingId', '')}",
                    "date": _activity_item_date(timestamp),
                    "timestamp": timestamp,
                    "recording": recording,
                }
            )

        for event in self.db.meeting_events.find({}, {"_id": 0}).sort("timestamp", DESCENDING).limit(limit):
            timestamp = _iso(event.get("timestamp"))
            items.append(
                {
                    "itemType": "voice_event",
                    "id": _meeting_event_item_id(event),
                    "date": str(event.get("date") or _activity_item_date(timestamp)),
                    "timestamp": timestamp,
                    "eventType": event.get("eventType") or "",
                    "rawAuthor": event.get("rawAuthor") or "",
                    "discordUsername": event.get("discordUsername") or "",
                    "channelId": event.get("channelId") or "",
                    "afkChannelId": event.get("afkChannelId") or "",
                    "meetingSeconds": int(event.get("meetingSeconds") or 0),
                }
            )

        items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        items = items[:limit]
        result: list[dict[str, Any]] = []
        current_date = ""

        for item in items:
            item_date = str(item.get("date") or _activity_item_date(item.get("timestamp")))

            if item_date and item_date != current_date:
                result.append(
                    {
                        "itemType": "day_separator",
                        "id": f"day:{item_date}",
                        "date": item_date,
                        "timestamp": item.get("timestamp") or "",
                    }
                )
                current_date = item_date

            result.append(item)

        return result


def _activity_item_date(timestamp: Any) -> str:
    value = _coerce_datetime(timestamp)

    if not value:
        return ""

    return value.date().isoformat()


def _meeting_event_item_id(event: dict[str, Any]) -> str:
    timestamp = _iso(event.get("timestamp")) or _iso(event.get("createdAt")) or ""
    discord_user_id = str(event.get("discordUserId") or "")
    event_type = str(event.get("eventType") or "")
    return f"voice:{event_type}:{discord_user_id}:{timestamp}"


