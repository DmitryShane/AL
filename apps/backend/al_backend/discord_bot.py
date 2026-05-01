from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord


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


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_DISCORD_LOG_LEVEL", "INFO"))
    config = load_config()
    asyncio.run(run_bot(config))


def load_config() -> DiscordBotConfig:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    backend_url = os.getenv("AL_BACKEND_URL", "https://activity.mempic.com").strip().rstrip("/")
    bot_secret = os.getenv("AL_DISCORD_BOT_SECRET", "").strip()
    guild_id = _parse_required_int("DISCORD_GUILD_ID")
    meeting_channel_id = _parse_required_int("DISCORD_MEETING_CHANNEL_ID")
    afk_channel_id = _parse_required_int("DISCORD_AFK_CHANNEL_ID")

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
        self.settings_loaded_at: datetime | None = None

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
            return

        if before_channel_id == self.config.meeting_channel_id:
            if member.id in self.auto_moved_member_ids and after_channel_id == self.config.afk_channel_id:
                self.auto_moved_member_ids.discard(member.id)
                await self.refresh_solo_state()
                return

            await self.submit_voice_event(member, "leave")
            await self.refresh_solo_state()

    async def monitor_solo_meeting(self) -> None:
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                await self.refresh_settings_if_needed()
                await self.refresh_solo_state()
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

        moved_at = datetime.now(timezone.utc)
        self.auto_moved_member_ids.add(member.id)
        await member.move_to(afk_channel, reason="AL auto-AFK: alone in meeting channel for over 10 minutes")
        self.solo_notified_member_ids.add(member.id)
        await self.submit_auto_afk_event(member, self.solo_started_at, moved_at)
        await self.refresh_solo_state()

    async def refresh_settings_if_needed(self) -> None:
        now = datetime.now(timezone.utc)

        if self.settings_loaded_at and (now - self.settings_loaded_at).total_seconds() < SETTINGS_REFRESH_SECONDS:
            return

        settings = await asyncio.to_thread(fetch_discord_settings, self.config)
        timeout_seconds = settings.get("meetingAutoAfkTimeoutSeconds")

        if isinstance(timeout_seconds, int) and timeout_seconds >= 60:
            self.solo_timeout_seconds = timeout_seconds

        self.settings_loaded_at = now

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
        }
        await asyncio.to_thread(submit_auto_afk_event, self.config, payload)


def submit_voice_event(config: DiscordBotConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_backend_event(config, "/api/v1/discord/voice-events", payload)


def submit_auto_afk_event(config: DiscordBotConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_backend_event(config, "/api/v1/discord/meeting-auto-afk", payload)


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
