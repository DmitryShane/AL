#!/usr/bin/env python3
"""
One-off maintenance: delete raw_activity_events (source=ual) for each author's
local calendar day that occurred strictly before their first "online" break_event
today. Then rebuild aggregates (report_rows, daily_author_activity, etc.).

Run on the server (Mongo settings are root-only in /etc/al/backend.env; do not `source` that file in shell if passwords contain metacharacters):

  cd /opt/al/current/apps/backend && ./.venv/bin/python ../../scripts/prod_strip_ual_before_online_today.py --env-file /etc/al/backend.env
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Allow running when cwd is apps/backend
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "apps", "backend")
if os.path.isdir(_BACKEND):
    sys.path.insert(0, _BACKEND)

from al_backend.repository import Repository
from al_backend.settings import load_settings


def _apply_env_file(path: str, keys: frozenset[str]) -> None:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()

            if key in keys:
                os.environ[key] = value


def _zone(tz_id: str | None) -> ZoneInfo:
    if not tz_id:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_id)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _ensure_utc_aware(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts.astimezone(dt.UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip pre-online UAL raw events for local today and rebuild aggregates")
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="Parse KEY=value lines for AL_MONGO_URI and AL_MONGO_DATABASE only (safe for passwords with shell metacharacters)",
    )
    args = parser.parse_args()

    if args.env_file:
        _apply_env_file(args.env_file, frozenset({"AL_MONGO_URI", "AL_MONGO_DATABASE"}))

    settings = load_settings()
    repo = Repository(settings)
    now_utc = dt.datetime.now(dt.UTC)
    deleted_total = 0
    affected: list[str] = []

    try:
        cursor = repo.db.author_profiles.find(
            {"telegramUsername": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "rawAuthor": 1, "telegramUsername": 1, "timeZoneId": 1},
        )

        for profile in cursor:
            raw_author = str(profile.get("rawAuthor") or "").strip()
            if not raw_author:
                continue

            tz = _zone(str(profile.get("timeZoneId") or "") or None)
            day_str = now_utc.astimezone(tz).date().isoformat()

            first_online = repo.db.break_events.find_one(
                {"rawAuthor": raw_author, "eventType": "online", "date": day_str},
                {"_id": 0, "timestamp": 1},
                sort=[("timestamp", 1)],
            )
            if not first_online:
                continue

            ts_raw = first_online.get("timestamp")
            if not isinstance(ts_raw, dt.datetime):
                continue
            online_at = _ensure_utc_aware(ts_raw)

            result = repo.db.raw_activity_events.delete_many(
                {
                    "author": raw_author,
                    "source": "ual",
                    "date": day_str,
                    "occurredAtUtc": {"$lt": online_at},
                }
            )
            n = int(result.deleted_count)
            if n > 0:
                affected.append(raw_author)
                deleted_total += n

        print(f"strip_ual_before_online: deleted_raw_events={deleted_total} authors={affected}")

        if deleted_total > 0:
            print("strip_ual_before_online: starting rebuild_aggregates_if_needed(force=True) …")
            repo.rebuild_aggregates_if_needed(force=True)
            print("strip_ual_before_online: rebuild finished")
        else:
            print("strip_ual_before_online: nothing deleted; skip rebuild")
    finally:
        repo.client.close()


if __name__ == "__main__":
    main()
