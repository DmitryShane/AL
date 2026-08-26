#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(pwd -P)"
PROJECT_ROOT="$(git -C "$CURRENT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"

case "$PROJECT_ROOT" in
  /Volumes/MacMiniExternal2TB/Development/corecry)
    exec "$PROJECT_ROOT/stop.server" "$@"
    ;;
  /Volumes/MacMiniExternal2TB/Development/AL)
    ;;
  *)
    echo "stop: unsupported project directory: $CURRENT_DIR" >&2
    echo "Supported projects: AL and corecry" >&2
    exit 1
    ;;
esac

UID_VALUE="$(id -u)"
BACKEND_LABEL="com.al.backend"
REPORT_WORKER_LABEL="com.al.report-worker"
FRONTEND_LABEL="com.al.frontend"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BACKEND_PLIST="$LAUNCH_AGENTS_DIR/$BACKEND_LABEL.plist"
REPORT_WORKER_PLIST="$LAUNCH_AGENTS_DIR/$REPORT_WORKER_LABEL.plist"
FRONTEND_PLIST="$LAUNCH_AGENTS_DIR/$FRONTEND_LABEL.plist"
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
stop_service "Report worker" "$REPORT_WORKER_LABEL"
stop_service "Backend" "$BACKEND_LABEL"

stop_port_processes "Frontend" 5173
stop_port_processes "Backend" 8000

rm -f "$FRONTEND_PLIST" "$REPORT_WORKER_PLIST" "$BACKEND_PLIST"
rm -rf "$RUNTIME_DIR"

echo "Stopped AL backend and frontend."
