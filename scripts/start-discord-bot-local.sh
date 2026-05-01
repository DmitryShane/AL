#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Volumes/MacMiniExternal2TB/Development/AL"
BACKEND_DIR="$ROOT_DIR/apps/backend"
ENV_FILE="$ROOT_DIR/.env.discord-bot"
RUNTIME_DIR="/tmp/al-runtime"
BOT_LOG="$RUNTIME_DIR/discord-bot.log"
BOT_ERR="$RUNTIME_DIR/discord-bot.err.log"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BOT_LABEL="com.al.discord-bot"
BOT_PLIST="$LAUNCH_AGENTS_DIR/$BOT_LABEL.plist"
UID_VALUE="$(id -u)"
UV_BIN="$(command -v uv)"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

AL_BACKEND_URL_VALUE="${AL_BACKEND_URL:-https://activity.mempic.com}"

if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
  echo "DISCORD_BOT_TOKEN is required."
  echo "Create $ENV_FILE from .env.discord-bot.example or run:"
  echo "DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=... DISCORD_MEETING_CHANNEL_ID=... DISCORD_AFK_CHANNEL_ID=... AL_DISCORD_BOT_SECRET=... scripts/start-discord-bot-local.sh"
  exit 1
fi

if [ -z "${DISCORD_GUILD_ID:-}" ]; then
  echo "DISCORD_GUILD_ID is required."
  exit 1
fi

if [ -z "${DISCORD_MEETING_CHANNEL_ID:-}" ]; then
  echo "DISCORD_MEETING_CHANNEL_ID is required."
  exit 1
fi

if [ -z "${DISCORD_AFK_CHANNEL_ID:-}" ]; then
  echo "DISCORD_AFK_CHANNEL_ID is required."
  exit 1
fi

if [ -z "${AL_DISCORD_BOT_SECRET:-}" ]; then
  echo "AL_DISCORD_BOT_SECRET is required."
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LAUNCH_AGENTS_DIR"

is_loaded() {
  launchctl print "gui/$UID_VALUE/$BOT_LABEL" >/dev/null 2>&1
}

write_bot_plist() {
  cat >"$BOT_PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$BOT_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "$BACKEND_DIR" &amp;&amp; exec "$UV_BIN" run python -m al_backend.discord_bot</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DISCORD_BOT_TOKEN</key>
    <string>$DISCORD_BOT_TOKEN</string>
    <key>DISCORD_GUILD_ID</key>
    <string>$DISCORD_GUILD_ID</string>
    <key>DISCORD_MEETING_CHANNEL_ID</key>
    <string>$DISCORD_MEETING_CHANNEL_ID</string>
    <key>DISCORD_AFK_CHANNEL_ID</key>
    <string>$DISCORD_AFK_CHANNEL_ID</string>
    <key>AL_BACKEND_URL</key>
    <string>$AL_BACKEND_URL_VALUE</string>
    <key>AL_DISCORD_BOT_SECRET</key>
    <string>$AL_DISCORD_BOT_SECRET</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$BOT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$BOT_ERR</string>
</dict>
</plist>
XML
}

if is_loaded; then
  echo "Discord bot: already loaded"
  exit 0
fi

write_bot_plist
echo "Discord bot: starting"
launchctl bootstrap "gui/$UID_VALUE" "$BOT_PLIST"
launchctl enable "gui/$UID_VALUE/$BOT_LABEL"

echo
echo "Bot logs: $BOT_LOG"
echo "Bot errors: $BOT_ERR"
