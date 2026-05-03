#!/usr/bin/env python3
"""
Production-safe aggregate rebuild for selected calendar dates.

Run on the server:

  cd /opt/al/current/apps/backend && \
    ./.venv/bin/python ../../scripts/prod_rebuild_activity_scope.py \
      --env-file /etc/al/backend.env --today
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)

    if end < start:
        raise ValueError("end date must be greater than or equal to start date")

    dates: list[str] = []
    current = start

    while current <= end:
        dates.append(current.isoformat())
        current += dt.timedelta(days=1)

    return dates


def _today_dates_for_authors(repo) -> list[str]:
    now = dt.datetime.now(dt.UTC)
    dates: set[str] = {now.date().isoformat()}

    for profile in repo.db.author_profiles.find({}, {"_id": 0, "timeZoneId": 1}):
        tz = _zone(str(profile.get("timeZoneId") or ""))
        dates.add(now.astimezone(tz).date().isoformat())

    return sorted(dates)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild AL aggregates for selected production calendar dates")
    parser.add_argument("--env-file", metavar="PATH", help="Parse AL_MONGO_URI and AL_MONGO_DATABASE")
    parser.add_argument("--today", action="store_true", help="Rebuild current local date for all configured author time zones")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Rebuild one calendar date")
    parser.add_argument("--start-date", metavar="YYYY-MM-DD", help="Rebuild date range start")
    parser.add_argument("--end-date", metavar="YYYY-MM-DD", help="Rebuild date range end")
    args = parser.parse_args()

    if args.env_file:
        _apply_env_file(args.env_file, frozenset({"AL_MONGO_URI", "AL_MONGO_DATABASE"}))

    settings = load_settings()
    container = BackendContainer(settings)
    repo = container.services

    try:
        if args.today:
            dates = _today_dates_for_authors(repo)
        elif args.date:
            dates = [dt.date.fromisoformat(args.date).isoformat()]
        elif args.start_date and args.end_date:
            dates = _date_range(args.start_date, args.end_date)
        else:
            raise SystemExit("Choose --today, --date, or --start-date with --end-date")

        print(f"prod_rebuild_activity_scope: rebuilding dates={dates}")
        result = repo.rebuild_aggregates_for_dates(dates[0], dates=dates)
        print(f"prod_rebuild_activity_scope: result={result}")
    finally:
        container.close()


if __name__ == "__main__":
    main()
