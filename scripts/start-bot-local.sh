#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Volumes/MacMiniExternal2TB/Development/AL"
BACKEND_DIR="$ROOT_DIR/apps/backend"
ENV_FILE="$ROOT_DIR/.env.telegram-bot"
RUNTIME_DIR="/tmp/al-runtime"
BOT_LOG="$RUNTIME_DIR/telegram-bot.log"
BOT_ERR="$RUNTIME_DIR/telegram-bot.err.log"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BOT_LABEL="com.al.telegram-bot"
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

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is required."
  echo "Create $ENV_FILE or run:"
  echo "TELEGRAM_BOT_TOKEN=... TELEGRAM_ALLOWED_CHAT_ID=... scripts/start-bot-local.sh"
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LAUNCH_AGENTS_DIR"

is_loaded() {
  launchctl print "gui/$UID_VALUE/$BOT_LABEL" >/dev/null 2>&1
}

write_bot_plist() {
  local allowed_chat_xml=""

  if [ -n "${TELEGRAM_ALLOWED_CHAT_ID:-}" ]; then
    allowed_chat_xml="    <key>TELEGRAM_ALLOWED_CHAT_ID</key>
    <string>$TELEGRAM_ALLOWED_CHAT_ID</string>"
  fi

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
    <string>cd "$BACKEND_DIR" &amp;&amp; exec "$UV_BIN" run python -m al_backend.telegram_bot</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TELEGRAM_BOT_TOKEN</key>
    <string>$TELEGRAM_BOT_TOKEN</string>
    <key>AL_BACKEND_URL</key>
    <string>$AL_BACKEND_URL_VALUE</string>
$allowed_chat_xml
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
  echo "Telegram bot: already loaded"
  exit 0
fi

write_bot_plist
echo "Telegram bot: starting"
launchctl bootstrap "gui/$UID_VALUE" "$BOT_PLIST"
launchctl enable "gui/$UID_VALUE/$BOT_LABEL"

echo
echo "Bot logs: $BOT_LOG"
echo "Bot errors: $BOT_ERR"
