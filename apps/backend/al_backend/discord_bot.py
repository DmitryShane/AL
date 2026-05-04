from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import time
from ctypes.util import find_library
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import opus
from discord.ext import voice_recv
from discord.ext.voice_recv.opus import PacketDecoder


LOGGER = logging.getLogger("al.discord_bot")
DEFAULT_SOLO_TIMEOUT_SECONDS = 10 * 60
SOLO_CHECK_SECONDS = 15
SETTINGS_REFRESH_SECONDS = 60
DAVE_READY_TIMEOUT_SECONDS = 2.0
PCM_SAMPLE_RATE = 48000
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2
PCM_FRAME_BYTES = PCM_CHANNELS * PCM_SAMPLE_WIDTH
PCM_FRAME_SAMPLES = opus.Decoder.SAMPLES_PER_FRAME
PCM_FRAME_SIZE = PCM_FRAME_SAMPLES * PCM_FRAME_BYTES
OUTPUT_SAMPLE_RATE = 16000
CORRUPTED_PACKET_DEGRADED_RATIO = 0.05
CORRUPTED_PACKET_SKIP_RATIO = 0.2
MAX_TRACK_GAP_SECONDS = 30 * 60
CORRUPTED_OPUS_PACKETS = 0


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    backend_url: str
    bot_secret: str
    guild_id: int
    meeting_channel_id: int
    afk_channel_id: int
    recording_temp_dir: str


@dataclass
class RecordingSession:
    recording_id: str
    started_at: datetime
    audio_path: str
    participant_ids: set[int]
    participant_names: dict[int, str]
    voice_client: Any
    sink: "MeetingAudioSink"
    cleanup_future: asyncio.Future[Exception | None]


@dataclass
class UserPcmTrack:
    user_id: int
    user_name: str
    path: str
    file: Any
    bytes_written: int = 0
    first_timestamp: int | None = None
    first_position_samples: int = 0
    frame_count: int = 0
    non_silent_frame_count: int = 0
    silence_padding_frames: int = 0
    out_of_order_frames: int = 0


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_DISCORD_LOG_LEVEL", "INFO"))
    ensure_opus_loaded()
    skip_corrupted_opus_packets()
    config = load_config()
    asyncio.run(run_bot(config))


def load_config() -> DiscordBotConfig:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    backend_url = os.getenv("AL_BACKEND_URL", "https://activity.mempic.com").strip().rstrip("/")
    bot_secret = os.getenv("AL_DISCORD_BOT_SECRET", "").strip()
    guild_id = _parse_required_int("DISCORD_GUILD_ID")
    meeting_channel_id = _parse_required_int("DISCORD_MEETING_CHANNEL_ID")
    afk_channel_id = _parse_required_int("DISCORD_AFK_CHANNEL_ID")
    recording_temp_dir = os.getenv("AL_DISCORD_RECORDING_TEMP_DIR", tempfile.gettempdir()).strip()

    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")

    if not bot_secret:
        raise RuntimeError("AL_DISCORD_BOT_SECRET is required")

    return DiscordBotConfig(
        token=token,
        backend_url=backend_url,
        bot_secret=bot_secret,
        guild_id=guild_id,
        meeting_channel_id=meeting_channel_id,
        afk_channel_id=afk_channel_id,
        recording_temp_dir=recording_temp_dir,
    )


async def run_bot(config: DiscordBotConfig) -> None:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    client = MeetingClient(config=config, intents=intents)
    await client.start(config.token)


class MeetingClient(discord.Client):
    def __init__(self, *, config: DiscordBotConfig, intents: discord.Intents):
        super().__init__(intents=intents)
        self.config = config
        self.solo_member_id: int | None = None
        self.solo_started_at: datetime | None = None
        self.solo_notified_member_ids: set[int] = set()
        self.auto_moved_member_ids: set[int] = set()
        self.solo_monitor_task: asyncio.Task | None = None
        self.solo_timeout_seconds = DEFAULT_SOLO_TIMEOUT_SECONDS
        self.meeting_summaries_enabled = False
        self.meeting_summary_min_participants = 2
        self.meeting_audio_retention_seconds = 0
        self.settings_loaded_at: datetime | None = None
        self.recording: RecordingSession | None = None

    async def on_ready(self) -> None:
        LOGGER.info("Discord bot started as %s. Backend: %s", self.user, self.config.backend_url)
        await self.refresh_solo_state()

        if self.solo_monitor_task is None or self.solo_monitor_task.done():
            self.solo_monitor_task = asyncio.create_task(self.monitor_solo_meeting())

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild.id != self.config.guild_id:
            return

        before_channel_id = before.channel.id if before.channel else None
        after_channel_id = after.channel.id if after.channel else None

        if before_channel_id == after_channel_id:
            return

        if after_channel_id == self.config.meeting_channel_id:
            await self.submit_voice_event(member, "join")
            await self.refresh_solo_state()
            await self.refresh_recording_state()
            return

        if before_channel_id == self.config.meeting_channel_id:
            if member.id in self.auto_moved_member_ids and after_channel_id == self.config.afk_channel_id:
                self.auto_moved_member_ids.discard(member.id)
                await self.refresh_solo_state()
                await self.finish_recording_if_needed(force=True)
                return

            await self.submit_voice_event(member, "leave")
            await self.refresh_solo_state()
            await self.refresh_recording_state()

    async def monitor_solo_meeting(self) -> None:
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                await self.refresh_settings_if_needed()
                await self.refresh_solo_state()
                await self.refresh_recording_state()
                await self.move_solo_member_if_due()
            except Exception:
                LOGGER.exception("Discord solo meeting monitor failed.")

            await asyncio.sleep(SOLO_CHECK_SECONDS)

    async def refresh_solo_state(self) -> None:
        channel = self.get_channel(self.config.meeting_channel_id)

        if not isinstance(channel, discord.VoiceChannel):
            return

        human_members = [member for member in channel.members if not member.bot]

        if len(human_members) != 1:
            self.solo_member_id = None
            self.solo_started_at = None
            self.solo_notified_member_ids.clear()
            return

        member = human_members[0]

        if self.solo_member_id == member.id and self.solo_started_at is not None:
            return

        self.solo_member_id = member.id
        self.solo_started_at = datetime.now(timezone.utc)
        self.solo_notified_member_ids.discard(member.id)

    async def move_solo_member_if_due(self) -> None:
        if self.solo_member_id is None or self.solo_started_at is None:
            return

        if self.solo_member_id in self.solo_notified_member_ids:
            return

        elapsed_seconds = (datetime.now(timezone.utc) - self.solo_started_at).total_seconds()

        if elapsed_seconds < self.solo_timeout_seconds:
            return

        guild = self.get_guild(self.config.guild_id)

        if guild is None:
            return

        member = guild.get_member(self.solo_member_id)
        afk_channel = guild.get_channel(self.config.afk_channel_id)

        if member is None or not isinstance(afk_channel, discord.VoiceChannel):
            return

        if member.voice is None or member.voice.channel is None or member.voice.channel.id != self.config.meeting_channel_id:
            await self.refresh_solo_state()
            return

        solo_started_at = self.solo_started_at
        moved_at = datetime.now(timezone.utc)
        self.auto_moved_member_ids.add(member.id)
        await member.move_to(afk_channel, reason="AL auto-AFK: alone in meeting channel for over 10 minutes")
        self.solo_notified_member_ids.add(member.id)
        await self.submit_auto_afk_event(member, solo_started_at, moved_at)
        await self.refresh_solo_state()

    async def refresh_settings_if_needed(self) -> None:
        now = datetime.now(timezone.utc)

        if self.settings_loaded_at and (now - self.settings_loaded_at).total_seconds() < SETTINGS_REFRESH_SECONDS:
            return

        settings = await asyncio.to_thread(fetch_discord_settings, self.config)
        timeout_seconds = settings.get("meetingAutoAfkTimeoutSeconds")

        if isinstance(timeout_seconds, int) and timeout_seconds >= 60:
            self.solo_timeout_seconds = timeout_seconds
        self.meeting_summaries_enabled = bool(settings.get("meetingSummariesEnabled", False))
        self.meeting_summary_min_participants = int(settings.get("meetingSummaryMinParticipants") or 2)
        self.meeting_audio_retention_seconds = int(settings.get("meetingAudioRetentionSeconds") or 0)

        self.settings_loaded_at = now

    async def refresh_recording_state(self) -> None:
        channel = self.get_channel(self.config.meeting_channel_id)

        if not isinstance(channel, discord.VoiceChannel):
            return

        human_members = [member for member in channel.members if not member.bot]

        if self.recording:
            for member in human_members:
                self.recording.participant_ids.add(member.id)
                self.recording.participant_names[member.id] = member.name

            if not human_members:
                await self.finish_recording_if_needed(force=True)

            return

        if not self.meeting_summaries_enabled or len(human_members) < self.meeting_summary_min_participants:
            return

        await self.start_recording(channel, human_members)

    async def start_recording(self, channel: discord.VoiceChannel, human_members: list[discord.Member]) -> None:
        Path(self.config.recording_temp_dir).mkdir(parents=True, exist_ok=True)
        recording_id = uuid.uuid4().hex
        audio_path = str(Path(self.config.recording_temp_dir) / f"al-meeting-{recording_id}.m4a")
        loop = asyncio.get_running_loop()
        cleanup_future: asyncio.Future[Exception | None] = loop.create_future()
        sink = MeetingAudioSink(audio_path=audio_path)

        def after_recording(error: Exception | None) -> None:
            if error:
                sink.record_listen_error(error)

            if not cleanup_future.done():
                loop.call_soon_threadsafe(cleanup_future.set_result, error)

        LOGGER.info(
            "Starting Discord meeting recording %s with participants: %s",
            recording_id,
            ", ".join(member.name for member in human_members),
        )
        reset_corrupted_opus_packet_count()
        voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=True)
        await wait_for_dave_ready(voice_client)
        voice_client.listen(sink, after=after_recording)
        started_at = datetime.now(timezone.utc)
        self.recording = RecordingSession(
            recording_id=recording_id,
            started_at=started_at,
            audio_path=audio_path,
            participant_ids={member.id for member in human_members},
            participant_names={member.id: member.name for member in human_members},
            voice_client=voice_client,
            sink=sink,
            cleanup_future=cleanup_future,
        )
        await asyncio.to_thread(submit_recording_started, self.config, self.recording)
        LOGGER.info("Discord meeting recording %s registered in backend.", recording_id)

    async def finish_recording_if_needed(self, *, force: bool = False) -> None:
        if not self.recording:
            return

        recording = self.recording
        self.recording = None
        ended_at = datetime.now(timezone.utc)
        failure_error = ""

        try:
            cleanup_old_retained_recordings(self.config.recording_temp_dir)
            if hasattr(recording.voice_client, "stop_listening"):
                recording.voice_client.stop_listening()

            await asyncio.wait_for(recording.cleanup_future, timeout=10)
            await recording.voice_client.disconnect(force=force)
            await self.update_recording_status(recording.recording_id, "compressing_audio")
            await asyncio.to_thread(recording.sink.finalize)
            corruption_ratio = corrupted_packet_ratio(recording.sink.frames_written)
            LOGGER.info(
                (
                    "Submitting Discord meeting recording %s for summary processing. "
                    "frames=%s non_silent_frames=%s corrupted_packets=%s corruption_ratio=%.3f "
                    "unknown_source_frames=%s bot_frames=%s mixed_users=%s silence_padding_frames=%s "
                    "listen_errors=%s per_user_frames=%s size=%.2f MB"
                ),
                recording.recording_id,
                recording.sink.frames_written,
                recording.sink.non_silent_frames,
                CORRUPTED_OPUS_PACKETS,
                corruption_ratio,
                recording.sink.unknown_source_frames,
                recording.sink.bot_frames,
                recording.sink.mixed_user_count,
                recording.sink.silence_padding_frames,
                recording.sink.listen_error_count,
                recording.sink.per_user_frame_counts(),
                file_size_mb(recording.audio_path),
            )
            result = await asyncio.to_thread(submit_recording_finished, self.config, recording, ended_at)

            if not result.get("ok"):
                error = str(result.get("error") or "Meeting recording upload failed")
                await asyncio.to_thread(submit_recording_failed, self.config, recording.recording_id, ended_at, error)
        except Exception as exc:
            failure_error = str(exc)
            LOGGER.exception("Discord meeting recording %s failed.", recording.recording_id)
            await asyncio.to_thread(submit_recording_failed, self.config, recording.recording_id, ended_at, str(exc))
        finally:
            if self.meeting_audio_retention_seconds > 0:
                retain_recording_audio(recording.audio_path, self.meeting_audio_retention_seconds)
                if failure_error:
                    retain_recording_recovery_files(recording, self.meeting_audio_retention_seconds, failure_error)
            else:
                try:
                    os.remove(recording.audio_path)
                except FileNotFoundError:
                    pass
                recording.sink.delete_track_files()

    async def submit_voice_event(self, member: discord.Member, event_type: str) -> None:
        payload = {
            "discordUserId": str(member.id),
            "discordUsername": member.name,
            "eventType": event_type,
            "guildId": str(member.guild.id),
            "channelId": str(self.config.meeting_channel_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await asyncio.to_thread(submit_voice_event, self.config, payload)

    async def submit_auto_afk_event(self, member: discord.Member, solo_started_at: datetime, moved_at: datetime) -> None:
        payload = {
            "discordUserId": str(member.id),
            "discordUsername": member.name,
            "guildId": str(member.guild.id),
            "meetingChannelId": str(self.config.meeting_channel_id),
            "afkChannelId": str(self.config.afk_channel_id),
            "soloStartedAt": solo_started_at.isoformat(),
            "movedAt": moved_at.isoformat(),
            "thresholdSeconds": self.solo_timeout_seconds,
        }
        await asyncio.to_thread(submit_auto_afk_event, self.config, payload)

    async def update_recording_status(self, recording_id: str, status: str) -> None:
        payload = {
            "recordingId": recording_id,
            "status": status,
        }
        await asyncio.to_thread(submit_backend_event, self.config, "/api/v1/discord/meeting-recordings/status", payload)


def ensure_opus_loaded() -> None:
    if discord.opus.is_loaded():
        return

    opus_library = find_library("opus")

    if not opus_library:
        raise RuntimeError("Discord voice recording requires libopus. Install the libopus0 system package.")

    discord.opus.load_opus(opus_library)
    LOGGER.info("Loaded Discord Opus library: %s", opus_library)


async def wait_for_dave_ready(voice_client: Any) -> None:
    started_at = time.monotonic()
    saw_dave_session = False

    while True:
        connection = getattr(voice_client, "_connection", None)
        dave_session = getattr(connection, "dave_session", None)

        if dave_session is not None:
            saw_dave_session = True

            if bool(getattr(dave_session, "ready", False)):
                LOGGER.info("Discord DAVE session is ready for audio receive.")
                return

        if time.monotonic() - started_at >= DAVE_READY_TIMEOUT_SECONDS:
            if saw_dave_session:
                LOGGER.warning("Discord DAVE session was not ready before recording started.")

            return

        await asyncio.sleep(0.1)


def skip_corrupted_opus_packets() -> None:
    if getattr(PacketDecoder._decode_packet, "_al_corruption_patch", False):
        return

    original_decode_packet = PacketDecoder._decode_packet
    silence = b"\x00" * PCM_FRAME_SIZE

    def decode_packet(self: PacketDecoder, packet: Any) -> tuple[Any, bytes]:
        global CORRUPTED_OPUS_PACKETS

        try:
            return original_decode_packet(self, packet)
        except opus.OpusError as exc:
            CORRUPTED_OPUS_PACKETS += 1
            LOGGER.debug("Skipped corrupted Discord Opus packet: %s", exc)

            decoder = getattr(self, "_decoder", None)

            if decoder is not None:
                try:
                    return packet, decoder.decode(None, fec=False)
                except opus.OpusError:
                    pass

            return packet, silence

    setattr(decode_packet, "_al_corruption_patch", True)
    PacketDecoder._decode_packet = decode_packet


def reset_corrupted_opus_packet_count() -> None:
    global CORRUPTED_OPUS_PACKETS
    CORRUPTED_OPUS_PACKETS = 0


def corrupted_packet_ratio(frame_count: int) -> float:
    if frame_count <= 0:
        return 0.0

    return CORRUPTED_OPUS_PACKETS / frame_count


class MeetingAudioSink(voice_recv.AudioSink):
    def __init__(self, *, audio_path: str):
        super().__init__()
        self.audio_path = audio_path
        self.started_at = time.monotonic()
        self.tracks: dict[int, UserPcmTrack] = {}
        self.frames_written = 0
        self.non_silent_frames = 0
        self.unknown_source_frames = 0
        self.bot_frames = 0
        self.empty_pcm_frames = 0
        self.silence_padding_frames = 0
        self.out_of_order_frames = 0
        self.listen_error_count = 0
        self.listen_error = ""
        self._lock = threading.RLock()
        self._closed = False

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.Member | discord.User | None, data: Any) -> None:
        with self._lock:
            self.frames_written += 1

            if user is None:
                self.unknown_source_frames += 1
                return

            if bool(getattr(user, "bot", False)):
                self.bot_frames += 1
                return

            pcm = bytes(getattr(data, "pcm", b"") or b"")

            if not pcm:
                self.empty_pcm_frames += 1
                return

            if any(pcm):
                self.non_silent_frames += 1

            track = self._track_for(user)
            self._write_track_frame(track, data, pcm)

    def cleanup(self) -> None:
        self._close_tracks()

    @property
    def mixed_user_count(self) -> int:
        return len(self.tracks)

    def per_user_frame_counts(self) -> dict[str, int]:
        return {track.user_name: track.frame_count for track in self.tracks.values()}

    def per_user_non_silent_frame_counts(self) -> dict[str, int]:
        return {track.user_name: track.non_silent_frame_count for track in self.tracks.values()}

    def record_listen_error(self, error: Exception) -> None:
        with self._lock:
            self.listen_error_count += 1
            self.listen_error = str(error)[:1000]

    def audio_quality_status(self, corrupted_packets: int) -> str:
        if self.frames_written <= 0:
            return "unknown"

        if self.non_silent_frames <= 0:
            return "silent"

        ratio = corrupted_packets / self.frames_written

        if ratio >= CORRUPTED_PACKET_SKIP_RATIO:
            return "corrupted"

        if ratio >= CORRUPTED_PACKET_DEGRADED_RATIO or self.listen_error_count > 0:
            return "degraded"

        return "ok"

    def finalize(self) -> None:
        with self._lock:
            self._close_tracks()
            tracks = [track for track in self.tracks.values() if track.bytes_written > 0]

        if not tracks:
            self._encode_silence()
            return

        self._mix_tracks(tracks)
        self.delete_track_files()

    def _track_for(self, user: discord.Member | discord.User) -> UserPcmTrack:
        user_id = int(user.id)
        track = self.tracks.get(user_id)

        if track is not None:
            return track

        safe_user_id = str(user_id)
        track_path = f"{self.audio_path}.{safe_user_id}.pcm"
        track = UserPcmTrack(
            user_id=user_id,
            user_name=str(getattr(user, "display_name", None) or getattr(user, "name", None) or user_id),
            path=track_path,
            file=open(track_path, "wb"),
        )
        self.tracks[user_id] = track
        return track

    def _write_track_frame(self, track: UserPcmTrack, data: Any, pcm: bytes) -> None:
        frame_samples = max(1, len(pcm) // PCM_FRAME_BYTES)
        target_samples = self._target_position_samples(track, data, frame_samples)
        current_samples = track.bytes_written // PCM_FRAME_BYTES

        if target_samples > current_samples:
            missing_samples = target_samples - current_samples
            missing_bytes = missing_samples * PCM_FRAME_BYTES
            track.file.write(b"\x00" * missing_bytes)
            track.bytes_written += missing_bytes
            missing_frames = max(1, round(missing_samples / PCM_FRAME_SAMPLES))
            track.silence_padding_frames += missing_frames
            self.silence_padding_frames += missing_frames
        elif target_samples < current_samples:
            overlap_samples = current_samples - target_samples
            track.out_of_order_frames += 1
            self.out_of_order_frames += 1

            if overlap_samples >= frame_samples:
                return

            pcm = pcm[overlap_samples * PCM_FRAME_BYTES :]

        track.file.write(pcm)
        track.bytes_written += len(pcm)
        track.frame_count += 1

        if any(pcm):
            track.non_silent_frame_count += 1

    def _target_position_samples(self, track: UserPcmTrack, data: Any, frame_samples: int) -> int:
        packet = getattr(data, "packet", None)
        timestamp = getattr(packet, "timestamp", None)
        wall_position_samples = max(0, round((time.monotonic() - self.started_at) * PCM_SAMPLE_RATE) - frame_samples)

        if timestamp is None:
            return wall_position_samples

        timestamp = int(timestamp)

        if track.first_timestamp is None:
            track.first_timestamp = timestamp
            track.first_position_samples = wall_position_samples
            return wall_position_samples

        timestamp_delta = (timestamp - track.first_timestamp) % (2**32)

        if timestamp_delta > MAX_TRACK_GAP_SECONDS * PCM_SAMPLE_RATE:
            track.first_timestamp = timestamp
            track.first_position_samples = max(track.bytes_written // PCM_FRAME_BYTES, wall_position_samples)
            return track.first_position_samples

        return track.first_position_samples + timestamp_delta

    def _close_tracks(self) -> None:
        if self._closed:
            return

        for track in self.tracks.values():
            try:
                track.file.close()
            except Exception:
                LOGGER.warning("Failed to close Discord PCM track %s", track.path, exc_info=True)

        self._closed = True

    def _mix_tracks(self, tracks: list[UserPcmTrack]) -> None:
        args = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "warning"]

        for track in tracks:
            args.extend(["-f", "s16le", "-ar", str(PCM_SAMPLE_RATE), "-ac", str(PCM_CHANNELS), "-i", track.path])

        if len(tracks) > 1:
            args.extend(
                [
                    "-filter_complex",
                    f"amix=inputs={len(tracks)}:duration=longest:dropout_transition=0:normalize=1[aout]",
                    "-map",
                    "[aout]",
                ]
            )

        args.extend(["-vn", "-ac", "1", "-ar", str(OUTPUT_SAMPLE_RATE), "-b:a", "32k", "-movflags", "+faststart", self.audio_path])
        self._run_ffmpeg(args)

    def _encode_silence(self) -> None:
        args = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-loglevel",
            "warning",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={OUTPUT_SAMPLE_RATE}:cl=mono",
            "-t",
            "1",
            "-b:a",
            "32k",
            "-movflags",
            "+faststart",
            self.audio_path,
        ]
        self._run_ffmpeg(args)

    def _run_ffmpeg(self, args: list[str]) -> None:
        try:
            result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required to create Discord meeting audio. Install ffmpeg on the bot host.") from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg failed to create Discord meeting audio: {stderr}")

    def delete_track_files(self) -> None:
        for track in self.tracks.values():
            try:
                os.remove(track.path)
            except FileNotFoundError:
                pass


def submit_voice_event(config: DiscordBotConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_backend_event(config, "/api/v1/discord/voice-events", payload)


def submit_auto_afk_event(config: DiscordBotConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_backend_event(config, "/api/v1/discord/meeting-auto-afk", payload)


def submit_recording_started(config: DiscordBotConfig, recording: RecordingSession) -> dict[str, Any]:
    payload = {
        "recordingId": recording.recording_id,
        "guildId": str(config.guild_id),
        "channelId": str(config.meeting_channel_id),
        "startedAt": recording.started_at.isoformat(),
        "participantDiscordUserIds": [str(item) for item in sorted(recording.participant_ids)],
        "participantNames": [recording.participant_names[item] for item in sorted(recording.participant_names.keys())],
    }
    return submit_backend_event(config, "/api/v1/discord/meeting-recordings/start", payload)


def submit_recording_finished(config: DiscordBotConfig, recording: RecordingSession, ended_at: datetime) -> dict[str, Any]:
    fields = {
        "recordingId": recording.recording_id,
        "guildId": str(config.guild_id),
        "channelId": str(config.meeting_channel_id),
        "startedAt": recording.started_at.isoformat(),
        "endedAt": ended_at.isoformat(),
        "participantDiscordUserIds": json.dumps([str(item) for item in sorted(recording.participant_ids)]),
        "participantNames": json.dumps([recording.participant_names[item] for item in sorted(recording.participant_names.keys())]),
        "audioFrameCount": str(recording.sink.frames_written),
        "nonSilentFrameCount": str(recording.sink.non_silent_frames),
        "corruptedPacketCount": str(CORRUPTED_OPUS_PACKETS),
        "unknownSourceFrameCount": str(recording.sink.unknown_source_frames),
        "botFrameCount": str(recording.sink.bot_frames),
        "emptyPcmFrameCount": str(recording.sink.empty_pcm_frames),
        "silencePaddingFrameCount": str(recording.sink.silence_padding_frames),
        "outOfOrderFrameCount": str(recording.sink.out_of_order_frames),
        "mixedUserCount": str(recording.sink.mixed_user_count),
        "perUserFrameCounts": json.dumps(recording.sink.per_user_frame_counts()),
        "perUserNonSilentFrameCounts": json.dumps(recording.sink.per_user_non_silent_frame_counts()),
        "listenErrorCount": str(recording.sink.listen_error_count),
        "listenError": recording.sink.listen_error,
        "audioQualityStatus": recording.sink.audio_quality_status(CORRUPTED_OPUS_PACKETS),
        "audioSizeBytes": str(file_size_bytes(recording.audio_path)),
    }
    return submit_multipart_event(config, "/api/v1/discord/meeting-recordings/finish", fields, "audio", recording.audio_path)


def submit_recording_failed(config: DiscordBotConfig, recording_id: str, ended_at: datetime, error: str) -> dict[str, Any]:
    return submit_backend_event(
        config,
        "/api/v1/discord/meeting-recordings/fail",
        {
            "recordingId": recording_id,
            "endedAt": ended_at.isoformat(),
            "error": error[:2000],
        },
    )


def fetch_discord_settings(config: DiscordBotConfig) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config.backend_url}/api/v1/discord/settings",
        headers={
            "Accept": "application/json",
            "x-al-discord-bot-secret": config.bot_secret,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        LOGGER.warning("Discord settings rejected with HTTP %s: %s", exc.code, body)
        return {}


def submit_multipart_event(
    config: DiscordBotConfig,
    path: str,
    fields: dict[str, str],
    file_field: str,
    file_path: str,
) -> dict[str, Any]:
    boundary = f"----ALBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    content_type = "audio/mp4" if file_path.endswith(".m4a") else "audio/wav"
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))

    with open(file_path, "rb") as audio_file:
        body.extend(audio_file.read())

    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        f"{config.backend_url}{path}",
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "x-al-discord-bot-secret": config.bot_secret,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
            LOGGER.info("Recorded Discord multipart event %s: %s", path, result)
            return result
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        LOGGER.warning("Discord multipart event rejected with HTTP %s: %s", exc.code, body_text)
        return {"ok": False, "error": body_text}


def submit_backend_event(config: DiscordBotConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config.backend_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-al-discord-bot-secret": config.bot_secret,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            LOGGER.info("Recorded Discord event %s for %s: %s", path, payload.get("discordUserId"), result)
            return result
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        LOGGER.warning("Discord event rejected with HTTP %s: %s", exc.code, body)
        return {"ok": False, "error": body}


def _parse_required_int(name: str) -> int:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"{name} is required")

    return int(value)


def file_size_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0


def file_size_mb(path: str) -> float:
    return file_size_bytes(path) / 1024 / 1024


def retain_recording_audio(path: str, retention_seconds: int) -> None:
    if not os.path.exists(path):
        return

    expires_at = int(time.time()) + max(0, retention_seconds)
    retained_path = f"{path}.keep-until-{expires_at}"
    os.replace(path, retained_path)
    LOGGER.info("Retained Discord meeting recording for debugging until %s: %s", expires_at, retained_path)


def retain_recording_recovery_files(recording: RecordingSession, retention_seconds: int, error: str) -> None:
    expires_at = int(time.time()) + max(0, retention_seconds)
    retained_tracks = []

    for track in recording.sink.tracks.values():
        if not os.path.exists(track.path):
            continue

        retained_path = f"{track.path}.keep-until-{expires_at}"
        os.replace(track.path, retained_path)
        retained_tracks.append(
            {
                "userId": str(track.user_id),
                "userName": track.user_name,
                "path": retained_path,
                "bytesWritten": track.bytes_written,
                "frameCount": track.frame_count,
                "nonSilentFrameCount": track.non_silent_frame_count,
            }
        )

    manifest = {
        "recordingId": recording.recording_id,
        "audioPath": recording.audio_path,
        "startedAt": recording.started_at.isoformat(),
        "participantDiscordUserIds": [str(item) for item in sorted(recording.participant_ids)],
        "participantNames": [recording.participant_names[item] for item in sorted(recording.participant_names.keys())],
        "error": error[:1000],
        "expiresAt": expires_at,
        "tracks": retained_tracks,
    }
    manifest_path = f"{recording.audio_path}.recovery.json.keep-until-{expires_at}"

    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)

    LOGGER.info("Retained Discord meeting recovery files until %s: %s", expires_at, manifest_path)


def cleanup_old_retained_recordings(recording_temp_dir: str) -> None:
    now = int(time.time())

    for path in Path(recording_temp_dir).glob("al-meeting-*.keep-until-*"):
        try:
            expires_at = int(str(path).rsplit(".keep-until-", 1)[1])
        except (IndexError, ValueError):
            continue

        if expires_at <= now:
            try:
                path.unlink()
                LOGGER.info("Deleted expired retained Discord meeting recording: %s", path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
