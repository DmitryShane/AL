#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-al}"
APP_ROOT="${APP_ROOT:-/opt/al}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
NODE_MAJOR="${NODE_MAJOR:-22}"
MONGODB_MAJOR="${MONGODB_MAJOR:-8.0}"
SWAP_SIZE="${SWAP_SIZE:-2G}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates certbot curl gnupg git nginx python3-certbot-nginx ufw

if ! swapon --show | grep -q .; then
  fallocate -l "${SWAP_SIZE}" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${APP_ROOT}" --shell /bin/bash "${APP_USER}"
fi

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}" "${APP_ROOT}/python" "${APP_ROOT}/.cache" "${APP_ROOT}/.cache/uv" "${APP_ROOT}/.config" "${APP_ROOT}/run"
install -d -m 0700 -o "${APP_USER}" -g "${APP_USER}" "${APP_ROOT}/.ssh"
install -d -m 0750 /etc/al

if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq "^v${NODE_MAJOR}\\."; then
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  apt-get install -y nodejs
fi

if ! command -v mongod >/dev/null 2>&1; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL "https://www.mongodb.org/static/pgp/server-${MONGODB_MAJOR}.asc" | gpg --dearmor -o /etc/apt/keyrings/mongodb-server.gpg
  chmod 0644 /etc/apt/keyrings/mongodb-server.gpg
  . /etc/os-release
  echo "deb [ arch=amd64,arm64 signed-by=/etc/apt/keyrings/mongodb-server.gpg ] https://repo.mongodb.org/apt/ubuntu ${VERSION_CODENAME}/mongodb-org/${MONGODB_MAJOR} multiverse" > /etc/apt/sources.list.d/mongodb-org.list
  apt-get update
  apt-get install -y mongodb-org
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

sudo -H -u "${APP_USER}" env \
  HOME="${APP_ROOT}" \
  XDG_CONFIG_HOME="${APP_ROOT}/.config" \
  UV_CACHE_DIR="${APP_ROOT}/.cache/uv" \
  UV_PYTHON_INSTALL_DIR="${APP_ROOT}/python" \
  uv --no-config python install "${PYTHON_VERSION}"

systemctl enable --now mongod
systemctl enable --now nginx

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw delete allow 8000/tcp >/dev/null 2>&1 || true
ufw --force enable

ssh-keyscan github.com >> "${APP_ROOT}/.ssh/known_hosts" 2>/dev/null || true
chmod 0600 "${APP_ROOT}/.ssh/known_hosts" 2>/dev/null || true
chown -R "${APP_USER}:${APP_USER}" "${APP_ROOT}"

echo "Bootstrap complete. Next: clone the repo into ${APP_ROOT}/current and create /etc/al/*.env files."
