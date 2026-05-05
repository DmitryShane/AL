from __future__ import annotations

from typing import Any

from ..activity_math import DESCENDING, dt, _coerce_datetime
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class EditorActivityPurgeService(MongoComposableMixin):
    _EDITOR_PLUGIN_PURGE_SOURCE_DENYLIST = ("telegram", "discord")

    def _status_purge_author_keys(self, canonical_author: str, reminder_raw_author: str) -> list[str]:
        keys: set[str] = set()
        canonical = str(canonical_author or "").strip()

        if canonical and canonical != "Unknown User":
            keys.add(canonical)

        reminder = str(reminder_raw_author or "").strip()

        if reminder and reminder != "Unknown User":
            keys.add(reminder)

        for doc in self.db.author_aliases.find({"targetRawAuthor": canonical}, {"_id": 0, "sourceRawAuthor": 1}):
            src = str(doc.get("sourceRawAuthor") or "").strip()

            if src:
                keys.add(src)

        keys.discard("")
        return sorted(keys)

    def purge_editor_plugin_activity_for_author_day(self, raw_author: str, day_date: str) -> dict[str, Any]:
        reminder_raw_author = str(raw_author or "").strip()
        author = composed(self).resolve_author_alias(reminder_raw_author or "Unknown User")
        day_date = str(day_date or "").strip()

        if not author or author == "Unknown User" or not day_date:
            return {"ok": False, "error": "missing_author_or_date"}

        status_author_keys = self._status_purge_author_keys(author, reminder_raw_author)
        deleted_status_report_rows = 0

        if status_author_keys:
            deleted_status_report_rows = self.db.report_rows.delete_many(
                {"author": {"$in": status_author_keys}, "date": day_date, "source": "status"}
            ).deleted_count

        query_events = {"author": author, "date": day_date, "source": {"$nin": list(self._EDITOR_PLUGIN_PURGE_SOURCE_DENYLIST)}}
        batch_ids: set[str] = set()

        for event in self.db.raw_activity_events.find(query_events, {"_id": 0, "batchId": 1}):
            batch_id = str(event.get("batchId") or "").strip()

            if batch_id:
                batch_ids.add(batch_id)

        deleted_events = self.db.raw_activity_events.delete_many(query_events).deleted_count
        deleted_batches = 0
        deleted_raw_reports = 0

        for batch_id in sorted(batch_ids):
            if self.db.raw_activity_events.count_documents({"batchId": batch_id}) > 0:
                continue

            batch = self.db.raw_event_batches.find_one({"batchId": batch_id}, {"_id": 0})

            if not batch:
                continue

            self.db.raw_event_batches.delete_many({"batchId": batch_id})
            deleted_batches += 1
            raw_report_id = batch.get("rawReportId")

            if raw_report_id is not None:
                self.db.raw_reports.delete_many({"_id": raw_report_id})
                deleted_raw_reports += 1

        query_snapshots = {"author": author, "date": day_date, "source": {"$nin": list(self._EDITOR_PLUGIN_PURGE_SOURCE_DENYLIST)}}
        deleted_snapshots = self.db.activity_snapshots.delete_many(query_snapshots).deleted_count
        deleted_status_events = 0

        if status_author_keys:
            deleted_status_events = self.db.status_events.delete_many(
                {"rawAuthor": {"$in": status_author_keys}, "date": day_date}
            ).deleted_count

        self._resync_status_state_from_events(author)
        rebuild_result = self.rebuild_aggregates_for_dates(start_date=day_date, dates=[day_date], authors=[author])

        return {
            "ok": True,
            "deletedRawActivityEvents": deleted_events,
            "deletedRawEventBatches": deleted_batches,
            "deletedRawReports": deleted_raw_reports,
            "deletedActivitySnapshots": deleted_snapshots,
            "deletedStatusReportRows": deleted_status_report_rows,
            "deletedStatusEvents": deleted_status_events,
            "purgeRebuildDates": rebuild_result.get("dates"),
            "purgeRebuildAuthors": rebuild_result.get("authors"),
        }

    def _resync_status_state_from_events(self, raw_author: str) -> None:
        author = str(raw_author or "").strip()

        if not author or author == "Unknown User":
            return

        now = dt.datetime.now(dt.UTC)
        cursor = self.db.status_events.find({"rawAuthor": author})
        last_items = list(cursor.sort([("transitionAt", DESCENDING)]).limit(1))

        if not last_items:
            self.db.status_states.update_one(
                {"rawAuthor": author},
                {"$set": {"rawAuthor": author, "status": "online", "updatedAt": now}},
                upsert=True,
            )
            return

        last = last_items[0]
        event_type = str(last.get("statusEventType") or "")
        new_status = "offline" if event_type == "offline" else "online"
        transition_at = _coerce_datetime(last.get("transitionAt")) or now

        self.db.status_states.update_one(
            {"rawAuthor": author},
            {
                "$set": {
                    "rawAuthor": author,
                    "status": new_status,
                    "updatedAt": now,
                    "transitionAt": transition_at,
                }
            },
            upsert=True,
        )
