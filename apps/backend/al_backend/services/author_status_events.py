from __future__ import annotations

import datetime as dt
from typing import Any

from pymongo import ASCENDING

from ..activity_math import (
    _author_configured_time_zone_id,
    _coerce_datetime,
    _empty_event_deltas,
    _telegram_event_date,
    _valid_time_zone_id,
)
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class AuthorStatusEventsService(MongoComposableMixin):
    """Persists plugin presence transitions (reports stopped / resumed) for aggregates and report_rows."""

    def record_status_event(
        self,
        raw_author: str,
        status_event_type: str,
        transition_at: dt.datetime,
        time_zone_id: str | None = None,
        reason: str = "reports_stopped",
        received_at: dt.datetime | None = None,
    ) -> dict[str, Any]:
        event_type = status_event_type if status_event_type in {"offline", "online"} else ""

        if not raw_author or not event_type:
            return {"ok": False, "error": "Status event requires author and offline/online type"}

        transition_at = _coerce_datetime(transition_at) or dt.datetime.now(dt.UTC)
        received_at = _coerce_datetime(received_at) or transition_at
        normalized_time_zone_id = _valid_time_zone_id(time_zone_id) or _author_configured_time_zone_id(raw_author) or "UTC"
        event_date = _telegram_event_date(transition_at, normalized_time_zone_id)
        composed(self).invalidate_activity_summary_cache([event_date])
        event = {
            "rawAuthor": raw_author,
            "date": event_date,
            "statusEventType": event_type,
            "transitionAt": transition_at,
            "receivedAt": received_at,
            "timeZoneId": normalized_time_zone_id,
            "reason": reason,
            "createdAt": dt.datetime.now(dt.UTC),
        }
        self.db.status_events.update_one(
            {
                "rawAuthor": raw_author,
                "date": event_date,
                "statusEventType": event_type,
                "transitionAt": transition_at,
            },
            {"$setOnInsert": event},
            upsert=True,
        )
        self._insert_status_report_row(event)
        return {"ok": True, "event": event}

    def _record_status_transition_for_author(
        self,
        author: dict[str, Any],
        send_interval_seconds: int,
        now: dt.datetime,
        include_report_stopped_alerts: bool,
    ) -> None:
        raw_author = str(author.get("rawAuthor") or "")

        if not raw_author:
            return

        previous_state = self.db.status_states.find_one({"rawAuthor": raw_author}, {"_id": 0}) or {}
        previous_status = str(previous_state.get("status") or "online")
        stale_presence = str(author.get("stalePresence") or "")
        is_red_offline = (
            include_report_stopped_alerts
            and author.get("status") == "stale"
            and stale_presence in {"reports", "both"}
        )
        time_zone_id = _valid_time_zone_id(author.get("timeZoneId")) or _author_configured_time_zone_id(raw_author) or "UTC"

        if is_red_offline:
            if previous_status == "offline":
                self.db.status_states.update_one(
                    {"rawAuthor": raw_author},
                    {"$set": {"rawAuthor": raw_author, "status": "offline", "updatedAt": now}},
                    upsert=True,
                )
                return

            last_received_at = _coerce_datetime(author.get("lastReceivedAt")) or now
            transition_at = last_received_at + dt.timedelta(seconds=max(0, send_interval_seconds * 2) + 1)
            self.record_status_event(raw_author, "offline", transition_at, time_zone_id, "reports_stopped")
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "offline", "updatedAt": now, "transitionAt": transition_at}},
                upsert=True,
            )
            return

        if (
            not is_red_offline
            and previous_status == "offline"
            and stale_presence not in {"reports", "both"}
        ):
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "online", "updatedAt": now}},
                upsert=True,
            )

        if author.get("status") == "online" and previous_status == "offline" and composed(self).get_plugin_ingest_enabled():
            transition_at = (_coerce_datetime(author.get("lastReceivedAt")) or now) + dt.timedelta(microseconds=1)
            self.record_status_event(raw_author, "online", transition_at, time_zone_id, "reports_resumed")
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "online", "updatedAt": now, "transitionAt": transition_at}},
                upsert=True,
            )

    def _materialize_status_report_rows(self) -> None:
        target_dates = getattr(self, "_aggregate_rebuild_target_dates", None)
        target_authors = getattr(self, "_aggregate_rebuild_target_authors", None)
        query: dict[str, Any] = {}

        if target_dates is not None:
            query["date"] = {"$in": sorted(target_dates)}

        if target_authors:
            query["rawAuthor"] = {"$in": sorted(target_authors)}

        for event in self.db.status_events.find(query, {"_id": 0}).sort("transitionAt", ASCENDING):
            self._insert_status_report_row(event)

    def _insert_status_report_row(self, event: dict[str, Any]) -> None:
        raw_author = str(event.get("rawAuthor") or "Unknown User")
        transition_at = _coerce_datetime(event.get("transitionAt")) or dt.datetime.now(dt.UTC)
        received_at = _coerce_datetime(event.get("receivedAt")) or transition_at
        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or _author_configured_time_zone_id(raw_author) or "UTC"
        event_type = str(event.get("statusEventType") or "")
        event_date = str(event.get("date") or _telegram_event_date(transition_at, time_zone_id))

        if event_type not in {"offline", "online"}:
            return

        materialize_predicate = getattr(composed(self), "_should_materialize_aggregate_date", None)

        if callable(materialize_predicate) and not materialize_predicate(event_date, raw_author):
            return

        self.db.report_rows.delete_many(
            {
                "source": "status",
                "author": raw_author,
                "date": event_date,
                "statusEventType": event_type,
                "recordedAt": transition_at.isoformat(),
            }
        )
        self.db.report_rows.insert_one(
            {
                "source": "status",
                "pluginVersion": "status",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "status",
                "sessionId": raw_author,
                "deviceId": "",
                "date": event_date,
                "recordedAt": transition_at.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": transition_at.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "status",
                "activityType": event_type,
                "statusEventType": event_type,
                "statusReason": str(event.get("reason") or ""),
                "metadata": {"reason": str(event.get("reason") or "")},
                **_empty_event_deltas(),
            }
        )
