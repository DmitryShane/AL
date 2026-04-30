#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-al}"
APP_ROOT="${APP_ROOT:-/opt/al}"
APP_DIR="${APP_DIR:-${APP_ROOT}/current}"
REPO_URL="${REPO_URL:-https://github.com/DmitryShane/AL.git}"
DEPLOY_REF="${1:-origin/main}"
PUBLIC_HOST="${PUBLIC_HOST:-activity.mempic.com}"
PUBLIC_API_URL="${PUBLIC_API_URL:-https://${PUBLIC_HOST}}"
PUBLIC_SITE_ORIGIN="${PUBLIC_SITE_ORIGIN:-https://${PUBLIC_HOST}}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

BACKEND_ENV="/etc/al/backend.env"
TELEGRAM_ENV="/etc/al/telegram-bot.env"
DISCORD_ENV="/etc/al/discord-bot.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ ! -f "${BACKEND_ENV}" ]]; then
  echo "Missing ${BACKEND_ENV}" >&2
  exit 1
fi

if [[ ! -f "${TELEGRAM_ENV}" ]]; then
  echo "Missing ${TELEGRAM_ENV}" >&2
  exit 1
fi

if [[ ! -f "${DISCORD_ENV}" ]]; then
  echo "Missing ${DISCORD_ENV}" >&2
  exit 1
fi

require_env_key() {
  local env_file="$1"
  local key="$2"

  if ! grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}=" "${env_file}"; then
    echo "Missing ${key} in ${env_file}" >&2
    exit 1
  fi
}

require_env_key "${BACKEND_ENV}" "AL_DISCORD_BOT_SECRET"
require_env_key "${DISCORD_ENV}" "DISCORD_BOT_TOKEN"
require_env_key "${DISCORD_ENV}" "DISCORD_GUILD_ID"
require_env_key "${DISCORD_ENV}" "DISCORD_MEETING_CHANNEL_ID"
require_env_key "${DISCORD_ENV}" "AL_BACKEND_URL"
require_env_key "${DISCORD_ENV}" "AL_DISCORD_BOT_SECRET"

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}" "${APP_ROOT}/python" "${APP_ROOT}/.cache" "${APP_ROOT}/.cache/uv" "${APP_ROOT}/.config"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  rm -rf "${APP_DIR}"
  sudo -H -u "${APP_USER}" env HOME="${APP_ROOT}" git clone "${REPO_URL}" "${APP_DIR}"
fi

sudo -H -u "${APP_USER}" env HOME="${APP_ROOT}" git -C "${APP_DIR}" fetch --prune origin
sudo -H -u "${APP_USER}" env HOME="${APP_ROOT}" git -C "${APP_DIR}" checkout --force "${DEPLOY_REF}"
sudo -H -u "${APP_USER}" env HOME="${APP_ROOT}" git -C "${APP_DIR}" reset --hard "${DEPLOY_REF}"

sudo -H -u "${APP_USER}" env \
  HOME="${APP_ROOT}" \
  XDG_CONFIG_HOME="${APP_ROOT}/.config" \
  UV_CACHE_DIR="${APP_ROOT}/.cache/uv" \
  UV_PYTHON_INSTALL_DIR="${APP_ROOT}/python" \
  uv --no-config python install "${PYTHON_VERSION}"

sudo -H -u "${APP_USER}" env \
  HOME="${APP_ROOT}" \
  XDG_CONFIG_HOME="${APP_ROOT}/.config" \
  UV_CACHE_DIR="${APP_ROOT}/.cache/uv" \
  UV_PYTHON_INSTALL_DIR="${APP_ROOT}/python" \
  bash -lc "cd '${APP_DIR}/apps/backend' && uv --no-config sync --no-dev --python '${PYTHON_VERSION}'"

sudo -H -u "${APP_USER}" env \
  HOME="${APP_ROOT}" \
  VITE_API_URL="${PUBLIC_API_URL}" \
  bash -lc "cd '${APP_DIR}/apps/frontend' && npm ci && npm run build"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__APP_USER__#${APP_USER}#g" \
  -e "s#__APP_ROOT__#${APP_ROOT}#g" \
  "${APP_DIR}/deploy/systemd/al-backend.service" > /etc/systemd/system/al-backend.service

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__APP_USER__#${APP_USER}#g" \
  -e "s#__APP_ROOT__#${APP_ROOT}#g" \
  "${APP_DIR}/deploy/systemd/al-telegram-bot.service" > /etc/systemd/system/al-telegram-bot.service

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__APP_USER__#${APP_USER}#g" \
  -e "s#__APP_ROOT__#${APP_ROOT}#g" \
  "${APP_DIR}/deploy/systemd/al-discord-bot.service" > /etc/systemd/system/al-discord-bot.service

SSL_SERVER_BLOCK=""

if [[ -f "/etc/letsencrypt/live/${PUBLIC_HOST}/fullchain.pem" && -f "/etc/letsencrypt/live/${PUBLIC_HOST}/privkey.pem" ]]; then
  SSL_SERVER_BLOCK="$(cat <<EOF

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${PUBLIC_HOST};

    ssl_certificate /etc/letsencrypt/live/${PUBLIC_HOST}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${PUBLIC_HOST}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root ${APP_DIR}/apps/frontend/dist;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
)"
fi

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__APP_USER__#${APP_USER}#g" \
  -e "s#__APP_ROOT__#${APP_ROOT}#g" \
  -e "s#__PUBLIC_HOST__#${PUBLIC_HOST}#g" \
  "${APP_DIR}/deploy/nginx/al.conf" > /etc/nginx/sites-available/al.conf

if [[ -n "${SSL_SERVER_BLOCK}" ]]; then
  printf '%s\n' "${SSL_SERVER_BLOCK}" >> /etc/nginx/sites-available/al.conf
fi

ln -sf /etc/nginx/sites-available/al.conf /etc/nginx/sites-enabled/al.conf
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable mongod nginx al-backend al-telegram-bot al-discord-bot
systemctl restart mongod
systemctl restart al-backend
set -a
# shellcheck disable=SC1090
source "${BACKEND_ENV}"
set +a
bash -lc "cd '${APP_DIR}/apps/backend' && .venv/bin/python -m al_backend.discord_author_mappings"
systemctl restart al-telegram-bot
systemctl restart al-discord-bot
nginx -t
systemctl reload nginx

echo "Deployed ${DEPLOY_REF} to ${APP_DIR}"
echo "Site: ${PUBLIC_SITE_ORIGIN}"
echo "API:  ${PUBLIC_API_URL}"
