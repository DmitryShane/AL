from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
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
    upload_path: str | None
    participant_ids: set[int]
    participant_names: dict[int, str]
    voice_client: Any
    cleanup_future: asyncio.Future[Exception | None]


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_DISCORD_LOG_LEVEL", "INFO"))
    ensure_opus_loaded()
    tolerate_corrupted_opus_packets()
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
        audio_path = str(Path(self.config.recording_temp_dir) / f"al-meeting-{recording_id}.wav")
        loop = asyncio.get_running_loop()
        cleanup_future: asyncio.Future[Exception | None] = loop.create_future()

        def after_recording(error: Exception | None) -> None:
            if not cleanup_future.done():
                loop.call_soon_threadsafe(cleanup_future.set_result, error)

        LOGGER.info(
            "Starting Discord meeting recording %s with participants: %s",
            recording_id,
            ", ".join(member.name for member in human_members),
        )
        voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=True)
        voice_client.listen(voice_recv.WaveSink(audio_path), after=after_recording)
        started_at = datetime.now(timezone.utc)
        self.recording = RecordingSession(
            recording_id=recording_id,
            started_at=started_at,
            audio_path=audio_path,
            upload_path=None,
            participant_ids={member.id for member in human_members},
            participant_names={member.id: member.name for member in human_members},
            voice_client=voice_client,
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

        try:
            if hasattr(recording.voice_client, "stop_listening"):
                recording.voice_client.stop_listening()

            await asyncio.wait_for(recording.cleanup_future, timeout=10)
            await recording.voice_client.disconnect(force=force)
            await self.update_recording_status(recording.recording_id, "compressing_audio")
            recording.upload_path = await asyncio.to_thread(compress_recording_audio, recording.audio_path)
            LOGGER.info("Submitting Discord meeting recording %s for summary processing.", recording.recording_id)
            result = await asyncio.to_thread(submit_recording_finished, self.config, recording, ended_at)

            if not result.get("ok"):
                error = str(result.get("error") or "Meeting recording upload failed")
                await asyncio.to_thread(submit_recording_failed, self.config, recording.recording_id, ended_at, error)
        finally:
            try:
                os.remove(recording.audio_path)
            except FileNotFoundError:
                pass
            if recording.upload_path and recording.upload_path != recording.audio_path:
                try:
                    os.remove(recording.upload_path)
                except FileNotFoundError:
                    pass

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


def tolerate_corrupted_opus_packets() -> None:
    original_decode_packet = PacketDecoder._decode_packet
    silence = b"\x00" * (opus.Decoder.SAMPLES_PER_FRAME * opus.Decoder.SAMPLE_SIZE)

    def decode_packet(self: PacketDecoder, packet: Any) -> tuple[Any, bytes]:
        try:
            return original_decode_packet(self, packet)
        except opus.OpusError:
            LOGGER.warning("Skipping corrupted Discord Opus packet during meeting recording.", exc_info=True)
            return packet, silence

    PacketDecoder._decode_packet = decode_packet


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
    }
    return submit_multipart_event(config, "/api/v1/discord/meeting-recordings/finish", fields, "audio", recording.upload_path or recording.audio_path)


def compress_recording_audio(audio_path: str) -> str:
    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is required to compress meeting recordings")

    compressed_path = str(Path(audio_path).with_suffix(".m4a"))
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        audio_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "32k",
        compressed_path,
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    LOGGER.info(
        "Compressed Discord meeting recording from %.1f MB to %.1f MB.",
        os.path.getsize(audio_path) / 1024 / 1024,
        os.path.getsize(compressed_path) / 1024 / 1024,
    )
    return compressed_path


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


if __name__ == "__main__":
    main()
