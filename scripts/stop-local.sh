#!/usr/bin/env bash
set -euo pipefail

UID_VALUE="$(id -u)"
BACKEND_LABEL="com.al.backend"
FRONTEND_LABEL="com.al.frontend"
BACKEND_PLIST="$HOME/Library/LaunchAgents/$BACKEND_LABEL.plist"
FRONTEND_PLIST="$HOME/Library/LaunchAgents/$FRONTEND_LABEL.plist"
RUNTIME_DIR="/tmp/al-runtime"

stop_service() {
  local name="$1"
  local label="$2"

  if launchctl print "gui/$UID_VALUE/$label" >/dev/null 2>&1; then
    echo "$name: stopping"
    launchctl bootout "gui/$UID_VALUE/$label"
  else
    echo "$name: not loaded"
  fi
}

stop_port_processes() {
  local name="$1"
  local port="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"

  if [ -z "$pids" ]; then
    return
  fi

  echo "$name: stopping remaining process on port $port: $pids"
  kill $pids >/dev/null 2>&1 || true
}

stop_service "Frontend" "$FRONTEND_LABEL"
stop_service "Backend" "$BACKEND_LABEL"

stop_port_processes "Frontend" 5173
stop_port_processes "Backend" 8000

rm -f "$FRONTEND_PLIST" "$BACKEND_PLIST"
rm -rf "$RUNTIME_DIR"

echo "Stopped AL backend and frontend."
