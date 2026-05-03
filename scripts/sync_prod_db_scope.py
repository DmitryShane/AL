#!/usr/bin/env python3
"""
Sync selected production AL MongoDB dates into the local database.

The scoped modes replace only documents whose `date` is in the selected range.
Use `--scope full` only when the owner explicitly chose a full database sync.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
from pathlib import Path


REMOTE = "root@activity.mempic.com"
REMOTE_MONGO_URI = "mongodb://127.0.0.1:27017"
REMOTE_DB = "al"
LOCAL_MONGO_URI = "mongodb://127.0.0.1:27017"
LOCAL_DB = "al"
WORK_ROOT = Path("/tmp/al-prod-sync")
DATE_SCOPED_COLLECTIONS = [
    "activity_snapshots",
    "raw_activity_events",
    "report_rows",
    "daily_author_activity",
    "break_events",
    "day_sessions",
    "status_events",
    "calendar_marks",
    "meeting_events",
    "meeting_intervals",
    "aggregate_day_state",
]


def _run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(shlex.quote(part) for part in command))
    subprocess.run(command, check=True, cwd=cwd)


def _run_capture(command: list[str]) -> str:
    print("+", " ".join(shlex.quote(part) for part in command))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


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


def _scope_dates(args: argparse.Namespace) -> list[str]:
    today = dt.date.today()

    if args.scope == "today":
        target = dt.date.fromisoformat(args.date) if args.date else today
        return [target.isoformat()]

    if args.scope == "week":
        if args.start_date and args.end_date:
            return _date_range(args.start_date, args.end_date)

        end = dt.date.fromisoformat(args.date) if args.date else today
        start = end - dt.timedelta(days=6)
        return _date_range(start.isoformat(), end.isoformat())

    return []


def _full_sync(remote: str, work_dir: Path) -> None:
    remote_archive = f"/tmp/al-prod-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.archive.gz"
    local_archive = work_dir / Path(remote_archive).name
    _run(
        [
            "ssh",
            remote,
            f"mongodump --uri={shlex.quote(REMOTE_MONGO_URI)} --db={shlex.quote(REMOTE_DB)} "
            f"--archive={shlex.quote(remote_archive)} --gzip",
        ]
    )
    _run(["scp", f"{remote}:{remote_archive}", str(local_archive)])
    _run(["ssh", remote, f"rm -f {shlex.quote(remote_archive)}"])
    _run(
        [
            "mongorestore",
            f"--uri={LOCAL_MONGO_URI}",
            f"--nsInclude={LOCAL_DB}.*",
            "--drop",
            f"--archive={local_archive}",
            "--gzip",
        ]
    )


def _dump_restore_collection(remote: str, archive_dir: Path, collection: str, query: dict[str, object]) -> None:
    query_json = json.dumps(query, separators=(",", ":"))
    remote_archive = f"/tmp/al-prod-sync-{collection}-{os.getpid()}.archive.gz"
    local_archive = archive_dir / f"{collection}.archive.gz"
    dump_command = (
        f"mongodump --uri={shlex.quote(REMOTE_MONGO_URI)} --db={shlex.quote(REMOTE_DB)} "
        f"--collection={shlex.quote(collection)} --query={shlex.quote(query_json)} "
        f"--archive={shlex.quote(remote_archive)} --gzip"
    )
    _run(["ssh", remote, dump_command])
    _run(["scp", f"{remote}:{remote_archive}", str(local_archive)])
    _run(["ssh", remote, f"rm -f {shlex.quote(remote_archive)}"])

    delete_script = (
        f"db.getSiblingDB({json.dumps(LOCAL_DB)}).getCollection({json.dumps(collection)})"
        f".deleteMany({query_json})"
    )
    _run(["mongosh", LOCAL_MONGO_URI, "--quiet", "--eval", delete_script])
    _run(["mongorestore", f"--uri={LOCAL_MONGO_URI}", f"--archive={local_archive}", "--gzip"])


def _remote_batch_ids(remote: str, dates: list[str]) -> list[str]:
    query = json.dumps({"date": {"$in": dates}}, separators=(",", ":"))
    script = (
        f"JSON.stringify(db.getSiblingDB({json.dumps(REMOTE_DB)}).raw_activity_events"
        f".distinct('batchId', {query}).filter(Boolean))"
    )
    output = _run_capture(["ssh", remote, f"mongosh {shlex.quote(REMOTE_MONGO_URI)} --quiet --eval {shlex.quote(script)}"])

    if not output:
        return []

    return [str(value) for value in json.loads(output)]


def _scoped_sync(remote: str, work_dir: Path, dates: list[str], rebuild: bool) -> None:
    archive_dir = work_dir / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)

    for collection in DATE_SCOPED_COLLECTIONS:
        _dump_restore_collection(remote, archive_dir, collection, {"date": {"$in": dates}})

    batch_ids = _remote_batch_ids(remote, dates)

    if batch_ids:
        _dump_restore_collection(remote, archive_dir, "raw_event_batches", {"batchId": {"$in": batch_ids}})

    if rebuild:
        backend_dir = Path(__file__).resolve().parent.parent / "apps" / "backend"
        script_path = Path(__file__).resolve().parent / "prod_rebuild_activity_scope.py"
        _run(["uv", "run", "python", str(script_path), "--start-date", dates[0], "--end-date", dates[-1]], cwd=backend_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync production AL MongoDB into local MongoDB by scope")
    parser.add_argument("--scope", choices=("today", "week", "full"), required=True)
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Target date for today/week ending date")
    parser.add_argument("--start-date", metavar="YYYY-MM-DD", help="Week/range start date")
    parser.add_argument("--end-date", metavar="YYYY-MM-DD", help="Week/range end date")
    parser.add_argument("--remote", default=REMOTE)
    parser.add_argument("--rebuild", action="store_true", help="Run local scoped aggregate rebuild after restore")
    args = parser.parse_args()

    work_dir = WORK_ROOT / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.scope == "full":
        _full_sync(args.remote, work_dir)
        return

    dates = _scope_dates(args)
    print(f"sync_prod_db_scope: dates={dates}")
    _scoped_sync(args.remote, work_dir, dates, args.rebuild)


if __name__ == "__main__":
    main()
