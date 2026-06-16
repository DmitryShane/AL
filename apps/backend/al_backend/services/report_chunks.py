from __future__ import annotations

from typing import Any

from pymongo import ASCENDING

from ..activity_math import _coerce_datetime, dt
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


CHUNKED_REPORT_SOURCE_ALLOWLIST = {"ual"}


def chunk_metadata(payload: dict[str, Any], source: str) -> dict[str, Any] | None:
    if source not in CHUNKED_REPORT_SOURCE_ALLOWLIST:
        return None

    logical_report_id = str(payload.get("logicalReportId") or "").strip()
    if not logical_report_id:
        return None

    try:
        chunk_index = int(payload.get("chunkIndex"))
        chunk_count = int(payload.get("chunkCount"))
    except (TypeError, ValueError):
        return None

    if chunk_index < 1 or chunk_count < 2 or chunk_index > chunk_count:
        return None

    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    try:
        total_event_count = int(payload.get("totalEventCount") or len(events))
        chunk_event_count = int(payload.get("chunkEventCount") or len(events))
    except (TypeError, ValueError):
        return None

    return {
        "logicalReportId": logical_report_id,
        "chunkIndex": chunk_index,
        "chunkCount": chunk_count,
        "chunkEventCount": chunk_event_count,
        "totalEventCount": total_event_count,
    }


class ReportChunkService(MongoComposableMixin):
    def process_chunked_report(
        self,
        *,
        source: str,
        plugin_version: str,
        payload: dict[str, Any],
        raw_report_id: Any,
        report_type: str,
        received_at: dt.datetime,
        challenge_id: str,
        device_id: str | None,
    ) -> list[str]:
        metadata = chunk_metadata(payload, source)
        if metadata is None:
            return []

        now = dt.datetime.now(dt.UTC)
        chunk_doc = {
            **metadata,
            "rawReportId": raw_report_id,
            "challengeId": challenge_id,
            "source": source,
            "pluginVersion": plugin_version,
            "author": str(payload.get("author") or "Unknown User"),
            "authorEmail": str(payload.get("authorEmail") or ""),
            "projectId": str(payload.get("projectId") or ""),
            "sessionId": str(payload.get("sessionId") or ""),
            "deviceId": str(device_id or payload.get("deviceId") or ""),
            "receivedAt": received_at,
            "queuedAt": None,
            "processingStartedAt": None,
            "processedAt": now,
            "failedAt": None,
            "status": "processed",
            "attempts": 0,
            "eventCount": len(payload.get("events") or []),
            "reportType": report_type,
            "lastError": "",
        }
        raw_report = self.db.raw_reports.find_one({"_id": raw_report_id}) or {}
        chunk_doc["queuedAt"] = raw_report.get("queuedAt")
        chunk_doc["processingStartedAt"] = raw_report.get("processingStartedAt")
        chunk_doc["processedAt"] = raw_report.get("processedAt") or now
        chunk_doc["failedAt"] = raw_report.get("failedAt")
        chunk_doc["attempts"] = int(raw_report.get("attempts") or 1)
        query = {"logicalReportId": metadata["logicalReportId"], "chunkIndex": metadata["chunkIndex"]}
        self.db.raw_report_chunks.update_one(
            query,
            {
                "$set": chunk_doc,
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )

        chunks = list(
            self.db.raw_report_chunks.find({"logicalReportId": metadata["logicalReportId"]}).sort([("chunkIndex", ASCENDING)])
        )
        if len(chunks) < metadata["chunkCount"]:
            return []

        expected_indexes = set(range(1, metadata["chunkCount"] + 1))
        received_indexes = {int(chunk.get("chunkIndex") or 0) for chunk in chunks}
        if received_indexes != expected_indexes:
            return []

        if any(chunk.get("assembledAt") for chunk in chunks):
            return []

        self.db.raw_report_chunks.update_many(
            {"logicalReportId": metadata["logicalReportId"]},
            {"$set": {"status": "assembling", "assemblingStartedAt": now}, "$unset": {"lastError": ""}},
        )

        try:
            affected_dates = self._assemble_complete_chunked_report(
                chunks=chunks,
                source=source,
                plugin_version=plugin_version,
                report_type=report_type,
                received_at=received_at,
                device_id=device_id,
                challenge_id=challenge_id,
            )
        except Exception as exc:
            self.db.raw_report_chunks.update_many(
                {"logicalReportId": metadata["logicalReportId"]},
                {"$set": {"status": "failed", "failedAt": dt.datetime.now(dt.UTC), "lastError": str(exc)}},
            )
            raise

        self.db.raw_report_chunks.update_many(
            {"logicalReportId": metadata["logicalReportId"]},
            {
                "$set": {
                    "status": "processed",
                    "assembledAt": dt.datetime.now(dt.UTC),
                    "affectedDates": sorted({date for date in affected_dates if str(date).strip()}),
                },
                "$unset": {"lastError": ""},
            },
        )
        return affected_dates

    def _assemble_complete_chunked_report(
        self,
        *,
        chunks: list[dict[str, Any]],
        source: str,
        plugin_version: str,
        report_type: str,
        received_at: dt.datetime,
        device_id: str | None,
        challenge_id: str,
    ) -> list[str]:
        ordered_chunks = sorted(chunks, key=lambda item: int(item.get("chunkIndex") or 0))
        raw_report_ids = [chunk.get("rawReportId") for chunk in ordered_chunks]
        raw_reports = {
            report.get("_id"): report
            for report in self.db.raw_reports.find({"_id": {"$in": raw_report_ids}})
        }
        first_payload = dict((raw_reports.get(raw_report_ids[0]) or {}).get("payload") or {})
        events: list[dict[str, Any]] = []

        for chunk in ordered_chunks:
            report = raw_reports.get(chunk.get("rawReportId")) or {}
            payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
            events.extend(payload.get("events") if isinstance(payload.get("events"), list) else [])

        events.sort(key=lambda item: str(item.get("occurredAtUtc") or item.get("occurredAtLocal") or ""))
        assembled_payload = dict(first_payload)
        assembled_payload["events"] = events
        assembled_payload["logicalReportId"] = ordered_chunks[0].get("logicalReportId")
        assembled_payload["chunkCount"] = ordered_chunks[0].get("chunkCount")
        assembled_payload["totalEventCount"] = ordered_chunks[0].get("totalEventCount")
        assembled_payload.pop("chunkIndex", None)
        assembled_payload.pop("chunkEventCount", None)

        return composed(self)._save_event_batch(
            source=source,
            plugin_version=plugin_version,
            payload=assembled_payload,
            raw_report_id=str(ordered_chunks[0].get("logicalReportId") or ""),
            report_type=report_type,
            received_at=received_at,
            challenge_id=challenge_id,
            device_id=device_id,
        )
