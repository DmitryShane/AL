#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Volumes/MacMiniExternal2TB/Development/AL"
BACKEND_DIR="$ROOT_DIR/apps/backend"
FRONTEND_DIR="$ROOT_DIR/apps/frontend"
LOCAL_ENV_FILE="$ROOT_DIR/.env"
RUNTIME_DIR="/tmp/al-runtime"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
BACKEND_ERR="$RUNTIME_DIR/backend.err.log"
BACKEND_RUNNER="$RUNTIME_DIR/backend-runner.sh"
BACKEND_LOCAL_ENV="$RUNTIME_DIR/backend.env"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
FRONTEND_ERR="$RUNTIME_DIR/frontend.err.log"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BACKEND_LABEL="com.al.backend"
FRONTEND_LABEL="com.al.frontend"
BACKEND_PLIST="$LAUNCH_AGENTS_DIR/$BACKEND_LABEL.plist"
FRONTEND_PLIST="$LAUNCH_AGENTS_DIR/$FRONTEND_LABEL.plist"
UID_VALUE="$(id -u)"
UV_BIN="$(command -v uv)"
NPM_BIN="$(command -v npm)"

mkdir -p "$RUNTIME_DIR" "$LAUNCH_AGENTS_DIR"

ensure_mongo() {
  if mongosh --quiet --eval 'db.adminCommand({ ping: 1 })' >/dev/null 2>&1; then
    echo "MongoDB: running"
    return
  fi

  echo "MongoDB: starting brew service"
  brew services start mongodb/brew/mongodb-community >/dev/null
}

is_loaded() {
  local label="$1"
  launchctl print "gui/$UID_VALUE/$label" >/dev/null 2>&1
}

write_backend_plist() {
  if [ -f "$LOCAL_ENV_FILE" ]; then
    cp "$LOCAL_ENV_FILE" "$BACKEND_LOCAL_ENV"
  else
    : >"$BACKEND_LOCAL_ENV"
  fi

  cat >"$BACKEND_RUNNER" <<SH
#!/usr/bin/env zsh
set -e

cd "$BACKEND_DIR"

if [ -f "$BACKEND_LOCAL_ENV" ]; then
  while IFS='=' read -r key value; do
    [[ -z "\$key" || "\$key" == \#* ]] && continue
    export "\$key=\$value"
  done < "$BACKEND_LOCAL_ENV"
fi

exec "$UV_BIN" run uvicorn al_backend.main:app --host 127.0.0.1 --port 8000
SH
  chmod +x "$BACKEND_RUNNER"

  cat >"$BACKEND_PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$BACKEND_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BACKEND_RUNNER</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$BACKEND_LOG</string>
  <key>StandardErrorPath</key>
  <string>$BACKEND_ERR</string>
</dict>
</plist>
XML
}

write_frontend_plist() {
  cat >"$FRONTEND_PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$FRONTEND_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "$FRONTEND_DIR" &amp;&amp; exec "$NPM_BIN" run dev -- --host 127.0.0.1 --port 5173</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$FRONTEND_LOG</string>
  <key>StandardErrorPath</key>
  <string>$FRONTEND_ERR</string>
</dict>
</plist>
XML
}

start_service() {
  local name="$1"
  local label="$2"
  local plist="$3"
  local port="$4"

  if is_loaded "$label"; then
    echo "$name: already loaded"
    return
  fi

  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$name: port $port is already in use"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN
    return
  fi

  echo "$name: starting"
  launchctl bootstrap "gui/$UID_VALUE" "$plist"
  launchctl enable "gui/$UID_VALUE/$label"
}

wait_for_url() {
  local name="$1"
  local url="$2"

  for _ in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name: ready"
      return 0
    fi

    sleep 0.5
  done

  echo "$name: not ready yet, check logs in $RUNTIME_DIR"
  return 1
}

ensure_mongo
write_backend_plist
write_frontend_plist
start_service "Backend" "$BACKEND_LABEL" "$BACKEND_PLIST" 8000
start_service "Frontend" "$FRONTEND_LABEL" "$FRONTEND_PLIST" 5173

wait_for_url "Backend" "http://127.0.0.1:8000/api/v1/health" || true
wait_for_url "Frontend" "http://127.0.0.1:5173/" || true

echo
echo "Site:    http://127.0.0.1:5173/"
echo "API:     http://127.0.0.1:8000"
echo "API doc: http://127.0.0.1:8000/docs"
