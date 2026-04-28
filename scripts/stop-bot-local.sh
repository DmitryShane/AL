#!/usr/bin/env bash
set -euo pipefail

UID_VALUE="$(id -u)"
BOT_LABEL="com.al.telegram-bot"
BOT_PLIST="$HOME/Library/LaunchAgents/$BOT_LABEL.plist"

if launchctl print "gui/$UID_VALUE/$BOT_LABEL" >/dev/null 2>&1; then
  echo "Telegram bot: stopping"
  launchctl bootout "gui/$UID_VALUE/$BOT_LABEL"
else
  echo "Telegram bot: not loaded"
fi

rm -f "$BOT_PLIST"
echo "Stopped AL Telegram bot."
