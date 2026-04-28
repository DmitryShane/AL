#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-al}"
APP_ROOT="${APP_ROOT:-/opt/al}"
APP_DIR="${APP_DIR:-${APP_ROOT}/current}"
REPO_URL="${REPO_URL:-https://github.com/DmitryShane/AL.git}"
DEPLOY_REF="${1:-origin/main}"
PUBLIC_HOST="${PUBLIC_HOST:-64.225.108.88}"
PUBLIC_API_URL="${PUBLIC_API_URL:-http://${PUBLIC_HOST}}"
PUBLIC_SITE_ORIGIN="${PUBLIC_SITE_ORIGIN:-http://${PUBLIC_HOST}}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

BACKEND_ENV="/etc/al/backend.env"
TELEGRAM_ENV="/etc/al/telegram-bot.env"

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
  -e "s#__PUBLIC_HOST__#${PUBLIC_HOST}#g" \
  "${APP_DIR}/deploy/nginx/al.conf" > /etc/nginx/sites-available/al.conf

ln -sf /etc/nginx/sites-available/al.conf /etc/nginx/sites-enabled/al.conf
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable mongod nginx al-backend al-telegram-bot
systemctl restart mongod
systemctl restart al-backend
systemctl restart al-telegram-bot
nginx -t
systemctl reload nginx

echo "Deployed ${DEPLOY_REF} to ${APP_DIR}"
echo "Site: ${PUBLIC_SITE_ORIGIN}"
echo "API:  ${PUBLIC_API_URL}"
