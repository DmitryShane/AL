#!/usr/bin/env python3
"""
Delete plugin report_rows that include idle time and were received after a Telegram
offline break_event, until the next Telegram online (same author).

Also removes matching raw_activity_events (by batchId/author/receivedAt/occurredAtUtc)
and activity_snapshots when the report row was snapshot-based.

Then rebuild aggregates.

Run on server:

  cd /opt/al/current/apps/backend && \\
    ./.venv/bin/python ../../scripts/prod_delete_post_offline_idle_plugin_reports.py \\
      --env-file /etc/al/backend.env

  ./.venv/bin/python ../../scripts/prod_delete_post_offline_idle_plugin_reports.py \\
      --env-file /etc/al/backend.env --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import defaultdict
from typing import Any

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "apps", "backend")
if os.path.isdir(_BACKEND):
    sys.path.insert(0, _BACKEND)

from al_backend.activity_math import _coerce_datetime, _time_microseconds
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


def _offline_windows(db: Any) -> list[tuple[str, dt.datetime, dt.datetime | None]]:
    by_author: dict[str, list[tuple[dt.datetime, str]]] = defaultdict(list)

    for doc in db.break_events.find({"eventType": {"$in": ["online", "offline"]}}, {"_id": 0, "rawAuthor": 1, "eventType": 1, "timestamp": 1}):
        raw_author = str(doc.get("rawAuthor") or "").strip()
        event_type = str(doc.get("eventType") or "")
        ts = _coerce_datetime(doc.get("timestamp"))

        if not raw_author or not ts:
            continue

        by_author[raw_author].append((ts, event_type))

    windows: list[tuple[str, dt.datetime, dt.datetime | None]] = []

    for raw_author, seq in by_author.items():
        seq.sort(key=lambda item: item[0])
        i = 0

        while i < len(seq):
            ts, typ = seq[i]

            if typ != "offline":
                i += 1
                continue

            offline_ts = ts
            i += 1

            while i < len(seq) and seq[i][1] == "offline":
                i += 1

            next_online: dt.datetime | None = None

            while i < len(seq):
                ts2, typ2 = seq[i]

                if typ2 == "online":
                    next_online = ts2
                    i += 1
                    break

                i += 1

            windows.append((raw_author, offline_ts, next_online))

    return windows


def _report_row_query(raw_author: str, offline_ts: dt.datetime, next_online: dt.datetime | None) -> dict[str, Any]:
    received: dict[str, Any] = {"$gt": offline_ts}

    if next_online:
        received["$lt"] = next_online

    return {
        "author": raw_author,
        "source": {"$nin": ["telegram", "discord"]},
        "$or": [{"idleDeltaSeconds": {"$gt": 0}}, {"idleDeltaMicroseconds": {"$gt": 0}}],
        "receivedAt": received,
    }


def _delete_raw_for_report_row(db: Any, row: dict[str, Any], dry_run: bool) -> tuple[bool, bool]:
    """Returns (deleted_raw_or_snap_from_raw, deleted_snapshot_document)."""

    author = row.get("author")
    batch_id = row.get("batchId")
    received_at = row.get("receivedAt")
    recorded = _coerce_datetime(row.get("recordedAt") or row.get("lastRecordedAt"))
    raw_report_id = row.get("rawReportId")

    if batch_id and author and received_at:
        query: dict[str, Any] = {"batchId": batch_id, "author": author, "receivedAt": received_at}
        candidates = list(db.raw_activity_events.find(query))

        if len(candidates) == 1:
            if not dry_run:
                db.raw_activity_events.delete_one({"_id": candidates[0]["_id"]})

            return True, False

        if recorded and candidates:

            for doc in candidates:
                occ = _coerce_datetime(doc.get("occurredAtUtc"))

                if occ and abs((occ - recorded).total_seconds()) <= 3:

                    if not dry_run:
                        db.raw_activity_events.delete_one({"_id": doc["_id"]})

                    return True, False

        return False, False

    if row.get("snapshotKey") and author and received_at and raw_report_id is not None:
        snap_query: dict[str, Any] = {"author": author, "receivedAt": received_at, "rawReportId": raw_report_id}
        snap = db.activity_snapshots.find_one(snap_query)

        if snap:
            if not dry_run:
                db.activity_snapshots.delete_one({"_id": snap["_id"]})

            return False, True

    return False, False


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove post-offline idle plugin reports and rebuild aggregates")
    parser.add_argument("--env-file", metavar="PATH", help="Parse AL_MONGO_URI and AL_MONGO_DATABASE")
    parser.add_argument("--dry-run", action="store_true", help="Only print counts, do not delete or rebuild")
    args = parser.parse_args()

    if args.env_file:
        _apply_env_file(args.env_file, frozenset({"AL_MONGO_URI", "AL_MONGO_DATABASE"}))

    settings = load_settings()
    container = BackendContainer(settings)
    repo = container.services

    try:
        windows = _offline_windows(repo.db)
        by_id: dict[Any, dict[str, Any]] = {}

        for raw_author, offline_ts, next_online in windows:
            q = _report_row_query(raw_author, offline_ts, next_online)

            for row in repo.db.report_rows.find(q):
                rid = row.get("_id")

                if rid is None:
                    continue

                if _time_microseconds(row, "idleDeltaSeconds", "idleDeltaMicroseconds") <= 0:
                    continue

                by_id[rid] = row

        total = len(by_id)
        print(f"post_offline_idle_reports: unique_report_rows={total} offline_windows={len(windows)}")

        if total == 0:
            print("post_offline_idle_reports: nothing to delete")

            return

        raw_deleted = 0
        snap_deleted = 0
        report_ids_to_remove: list[Any] = []
        skipped: list[dict[str, Any]] = []

        for row in by_id.values():
            got_raw, got_snap = _delete_raw_for_report_row(repo.db, row, args.dry_run)

            if got_raw:
                raw_deleted += 1
                report_ids_to_remove.append(row["_id"])
            elif got_snap:
                snap_deleted += 1
                report_ids_to_remove.append(row["_id"])
            else:
                skipped.append(row)

        if args.dry_run:
            print(
                f"post_offline_idle_reports: dry-run would remove report_rows={len(report_ids_to_remove)}, "
                f"raw_batch_ops={raw_deleted}, snapshots={snap_deleted}, "
                f"skipped_no_raw_mapping={len(skipped)}"
            )

            return

        if not report_ids_to_remove:
            print(f"post_offline_idle_reports: nothing removed; skipped={len(skipped)}")

            return

        res = repo.db.report_rows.delete_many({"_id": {"$in": report_ids_to_remove}})
        print(
            f"post_offline_idle_reports: raw_or_snap_batches={raw_deleted} snapshots={snap_deleted} "
            f"report_rows_deleted={res.deleted_count} skipped={len(skipped)}"
        )

        if skipped:
            for row in skipped[:15]:
                print(
                    "post_offline_idle_reports: skip",
                    row.get("author"),
                    row.get("batchId"),
                    row.get("receivedAt"),
                    row.get("recordedAt"),
                )

            if len(skipped) > 15:
                print(f"post_offline_idle_reports: ... {len(skipped) - 15} more skips")
        print("post_offline_idle_reports: starting rebuild_aggregates_if_needed(force=True) …")
        repo.rebuild_aggregates_if_needed(force=True)
        print("post_offline_idle_reports: rebuild finished")
    finally:
        container.close()


if __name__ == "__main__":
    main()
