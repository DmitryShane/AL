from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import discord


LOGGER = logging.getLogger("al.discord_bot")


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    backend_url: str
    bot_secret: str
    guild_id: int
    meeting_channel_id: int


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

    async def on_ready(self) -> None:
        LOGGER.info("Discord bot started as %s. Backend: %s", self.user, self.config.backend_url)
        await self.reconcile_meeting_channel()

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
            return

        if before_channel_id == self.config.meeting_channel_id:
            await self.submit_voice_event(member, "leave")

    async def reconcile_meeting_channel(self) -> None:
        guild = self.get_guild(self.config.guild_id)

        if guild is None:
            LOGGER.warning("Guild %s is not available to the bot.", self.config.guild_id)
            return

        channel = guild.get_channel(self.config.meeting_channel_id)

        if channel is None or not hasattr(channel, "members"):
            LOGGER.warning("Meeting channel %s is not available to the bot.", self.config.meeting_channel_id)
            return

        for member in getattr(channel, "members", []):
            if member.bot:
                continue

            await self.submit_voice_event(member, "reconcile")

    async def submit_voice_event(self, member: discord.Member, event_type: str) -> None:
        payload = {
            "discordUserId": str(member.id),
            "discordUsername": member.name,
            "eventType": event_type,
            "guildId": str(member.guild.id),
            "channelId": str(self.config.meeting_channel_id),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        await asyncio.to_thread(submit_voice_event, self.config, payload)


def submit_voice_event(config: DiscordBotConfig, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config.backend_url}/api/v1/discord/voice-events",
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
            LOGGER.info("Recorded Discord %s for %s: %s", payload.get("eventType"), payload.get("discordUserId"), result)
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
