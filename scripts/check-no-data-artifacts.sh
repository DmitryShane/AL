#!/usr/bin/env bash
set -euo pipefail

blocked_pattern='(^|/)(dump|dumps|backup|backups|mongo-dump|mongo-backup|al-prod-sync)(/|$)|\.(archive\.gz|bson|dump|sql|sqlite|sqlite3)$|(^|/)(backend|telegram-bot)\.env$'

blocked_files="$(
  git ls-files -z \
    | python3 -c 'import re, sys
pattern = re.compile(sys.argv[1])
files = [item.decode() for item in sys.stdin.buffer.read().split(b"\0") if item]
for path in files:
    if pattern.search(path):
        print(path)
' "${blocked_pattern}"
)"

if [[ -n "${blocked_files}" ]]; then
  echo "Refusing to deploy tracked database dumps, backups, or env snapshots:" >&2
  printf '%s\n' "${blocked_files}" >&2
  echo "Remove these files from git history/staging and keep them outside the repository." >&2
  exit 1
fi

echo "No tracked database dumps, backups, or env snapshots found."
