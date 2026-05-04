#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

echo "[al-apt-cache-clean] Before cleanup:"
df -h /

apt-get clean

echo "[al-apt-cache-clean] After cleanup:"
df -h /
