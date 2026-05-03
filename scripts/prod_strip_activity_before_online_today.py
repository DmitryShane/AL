#!/usr/bin/env python3
"""
Production maintenance: for each author with Telegram, on their local calendar
\"today\", delete plugin activity that precedes their first \"online\" break_event:
- raw_activity_events (all sources) where date matches and either occurredAtUtc or
  receivedAt is strictly before that online instant (interpreted in the author's TZ)
- activity_snapshots for the same author/date with receivedAt before that instant

Then rebuild aggregates (report_rows, daily_author_activity, etc.).

Run on the server (parse env file; do not `source` it if passwords contain metacharacters):

  cd /opt/al/current/apps/backend && \\
    ./.venv/bin/python ../../scripts/prod_strip_activity_before_online_today.py \\
      --env-file /etc/al/backend.env
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

from al_backend.container import BackendContainer
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


def _online_instant_utc(ts_raw: dt.datetime, tz_id: str | None) -> dt.datetime | None:
    if not isinstance(ts_raw, dt.datetime):
        return None
    tz = _zone(tz_id)
    if ts_raw.tzinfo is None:
        local = ts_raw.replace(tzinfo=tz)
    else:
        local = ts_raw.astimezone(tz)
    return local.astimezone(dt.UTC)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip pre-online activity (all plugin sources) for local today and rebuild aggregates"
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="Parse KEY=value lines for AL_MONGO_URI and AL_MONGO_DATABASE only",
    )
    args = parser.parse_args()

    if args.env_file:
        _apply_env_file(args.env_file, frozenset({"AL_MONGO_URI", "AL_MONGO_DATABASE"}))

    settings = load_settings()
    container = BackendContainer(settings)
    repo = container.services
    now_utc = dt.datetime.now(dt.UTC)
    deleted_raw = 0
    deleted_snapshots = 0
    affected_dates: set[str] = set()
    affected_raw: list[str] = []
    affected_snap: list[str] = []

    try:
        cursor = repo.db.author_profiles.find(
            {"telegramUsername": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "rawAuthor": 1, "telegramUsername": 1, "timeZoneId": 1},
        )

        for profile in cursor:
            raw_author = str(profile.get("rawAuthor") or "").strip()
            if not raw_author:
                continue

            tz_profile = str(profile.get("timeZoneId") or "").strip() or None
            tz = _zone(tz_profile)
            day_str = now_utc.astimezone(tz).date().isoformat()

            first_online = repo.db.break_events.find_one(
                {"rawAuthor": raw_author, "eventType": "online", "date": day_str},
                {"_id": 0, "timestamp": 1, "timeZoneId": 1},
                sort=[("timestamp", 1)],
            )
            if not first_online:
                continue

            ts_raw = first_online.get("timestamp")
            tz_id = str(first_online.get("timeZoneId") or "").strip() or tz_profile
            online_at_utc = _online_instant_utc(ts_raw, tz_id)
            if not online_at_utc:
                continue

            raw_filter = {
                "author": raw_author,
                "date": day_str,
                "$or": [
                    {"occurredAtUtc": {"$lt": online_at_utc}},
                    {"receivedAt": {"$lt": online_at_utc}},
                ],
            }
            r_result = repo.db.raw_activity_events.delete_many(raw_filter)
            r_n = int(r_result.deleted_count)
            if r_n > 0:
                affected_raw.append(raw_author)
                affected_dates.add(day_str)
                deleted_raw += r_n

            snap_filter = {
                "author": raw_author,
                "date": day_str,
                "receivedAt": {"$lt": online_at_utc},
            }
            s_result = repo.db.activity_snapshots.delete_many(snap_filter)
            s_n = int(s_result.deleted_count)
            if s_n > 0:
                affected_snap.append(raw_author)
                affected_dates.add(day_str)
                deleted_snapshots += s_n

        print(
            "strip_activity_before_online: "
            f"deleted_raw_events={deleted_raw} authors_raw={affected_raw} "
            f"deleted_snapshots={deleted_snapshots} authors_snap={affected_snap} "
            f"affected_dates={sorted(affected_dates)}"
        )

        if deleted_raw > 0 or deleted_snapshots > 0:
            print("strip_activity_before_online: starting scoped rebuild for affected dates …")
            for day in sorted(affected_dates):
                repo.rebuild_aggregates_for_dates(day)
            print("strip_activity_before_online: rebuild finished")
        else:
            print("strip_activity_before_online: nothing deleted; skip rebuild")
    finally:
        container.close()


if __name__ == "__main__":
    main()
