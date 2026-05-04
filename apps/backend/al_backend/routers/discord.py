from __future__ import annotations

import json
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from ..api_security import require_discord_bot_secret, require_permission
from ..container import BackendServices
from ..dependencies import get_discord_service, get_settings
from ..meeting_summary import generate_meeting_summary
from ..models import (
    DiscordMeetingAutoAfkIn,
    DiscordMeetingRecordingFailIn,
    DiscordMeetingRecordingStartIn,
    DiscordMeetingRecordingStatusIn,
    DiscordVoiceEventIn,
)
from ..router_utils import parse_json_object
from ..settings import Settings


router = APIRouter()


@router.get("/api/v1/discord/meeting-recordings/recent")
def recent_discord_meeting_recordings(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    return {
        "recordings": service.recent_meeting_recordings(limit=10),
        "items": service.recent_meeting_activity(limit=40),
    }


@router.post("/api/v1/discord/voice-events")
def record_discord_voice_event(
    event: DiscordVoiceEventIn,
    request: Request,
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    require_discord_bot_secret(request)
    return service.record_discord_voice_event(
        discord_user_id=event.discord_user_id,
        discord_username=event.discord_username,
        event_type=event.event_type,
        guild_id=event.guild_id,
        channel_id=event.channel_id,
        timestamp=event.timestamp,
    )


@router.post("/api/v1/discord/meeting-auto-afk")
def record_discord_meeting_auto_afk(
    event: DiscordMeetingAutoAfkIn,
    request: Request,
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    require_discord_bot_secret(request)
    return service.record_discord_meeting_auto_afk(
        discord_user_id=event.discord_user_id,
        discord_username=event.discord_username,
        guild_id=event.guild_id,
        meeting_channel_id=event.meeting_channel_id,
        afk_channel_id=event.afk_channel_id,
        solo_started_at=event.solo_started_at,
        moved_at=event.moved_at,
        threshold_seconds=event.threshold_seconds,
    )


@router.post("/api/v1/discord/meeting-recordings/start")
def record_discord_meeting_recording_started(
    event: DiscordMeetingRecordingStartIn,
    request: Request,
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    require_discord_bot_secret(request)
    return service.record_meeting_recording_started(
        recording_id=event.recording_id,
        guild_id=event.guild_id,
        channel_id=event.channel_id,
        started_at=event.started_at,
        participant_discord_user_ids=event.participant_discord_user_ids,
        participant_names=event.participant_names,
    )


@router.post("/api/v1/discord/meeting-recordings/fail")
def record_discord_meeting_recording_failed(
    event: DiscordMeetingRecordingFailIn,
    request: Request,
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    require_discord_bot_secret(request)
    return service.mark_meeting_recording_failed(
        recording_id=event.recording_id,
        ended_at=event.ended_at,
        error=event.error,
    )


@router.post("/api/v1/discord/meeting-recordings/status")
def update_discord_meeting_recording_status(
    event: DiscordMeetingRecordingStatusIn,
    request: Request,
    service: BackendServices = Depends(get_discord_service),
) -> dict:
    require_discord_bot_secret(request)
    return service.update_meeting_recording_status(
        recording_id=event.recording_id,
        status=event.status,
    )


@router.post("/api/v1/discord/meeting-recordings/finish")
def record_discord_meeting_recording_finished(
    request: Request,
    recording_id: str = Form(alias="recordingId"),
    guild_id: str | None = Form(default=None, alias="guildId"),
    channel_id: str | None = Form(default=None, alias="channelId"),
    started_at: str = Form(alias="startedAt"),
    ended_at: str = Form(alias="endedAt"),
    participant_discord_user_ids: str = Form(default="[]", alias="participantDiscordUserIds"),
    participant_names: str = Form(default="[]", alias="participantNames"),
    audio_frame_count: int = Form(default=0, alias="audioFrameCount"),
    non_silent_frame_count: int = Form(default=0, alias="nonSilentFrameCount"),
    corrupted_packet_count: int = Form(default=0, alias="corruptedPacketCount"),
    unknown_source_frame_count: int = Form(default=0, alias="unknownSourceFrameCount"),
    bot_frame_count: int = Form(default=0, alias="botFrameCount"),
    empty_pcm_frame_count: int = Form(default=0, alias="emptyPcmFrameCount"),
    silence_padding_frame_count: int = Form(default=0, alias="silencePaddingFrameCount"),
    out_of_order_frame_count: int = Form(default=0, alias="outOfOrderFrameCount"),
    mixed_user_count: int = Form(default=0, alias="mixedUserCount"),
    per_user_frame_counts: str = Form(default="{}", alias="perUserFrameCounts"),
    per_user_non_silent_frame_counts: str = Form(default="{}", alias="perUserNonSilentFrameCounts"),
    listen_error_count: int = Form(default=0, alias="listenErrorCount"),
    listen_error: str = Form(default="", alias="listenError"),
    audio_quality_status: str = Form(default="", alias="audioQualityStatus"),
    audio_size_bytes: int = Form(default=0, alias="audioSizeBytes"),
    audio: UploadFile = File(),
    service: BackendServices = Depends(get_discord_service),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_discord_bot_secret(request)
    participant_ids = json.loads(participant_discord_user_ids or "[]")
    names = json.loads(participant_names or "[]")
    temp_path = ""

    try:
        suffix = os.path.splitext(audio.filename or "meeting.wav")[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            temp_file.write(audio.file.read())

        return service.process_meeting_recording_finished(
            recording_id=recording_id,
            guild_id=guild_id,
            channel_id=channel_id,
            started_at=started_at,
            ended_at=ended_at,
            participant_discord_user_ids=[str(item) for item in participant_ids],
            participant_names=[str(item) for item in names],
            audio_frame_count=audio_frame_count,
            non_silent_frame_count=non_silent_frame_count,
            corrupted_packet_count=corrupted_packet_count,
            unknown_source_frame_count=unknown_source_frame_count,
            bot_frame_count=bot_frame_count,
            empty_pcm_frame_count=empty_pcm_frame_count,
            silence_padding_frame_count=silence_padding_frame_count,
            out_of_order_frame_count=out_of_order_frame_count,
            mixed_user_count=mixed_user_count,
            per_user_frame_counts=parse_json_object(per_user_frame_counts),
            per_user_non_silent_frame_counts=parse_json_object(per_user_non_silent_frame_counts),
            listen_error_count=listen_error_count,
            listen_error=listen_error,
            audio_quality_status=audio_quality_status,
            audio_size_bytes=audio_size_bytes,
            audio_path=temp_path,
            summary_generator=lambda path, people, language, prompt_template, progress_callback=None: generate_meeting_summary(
                settings,
                path,
                participant_names=people,
                language=language,
                prompt_template=prompt_template,
                progress_callback=progress_callback,
            ),
        )
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
