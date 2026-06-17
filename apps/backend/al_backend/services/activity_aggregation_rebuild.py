from __future__ import annotations

import gc
import logging
import os
import time
import uuid
from typing import Any, Callable

from ..activity_math import *
from ..backend_composable_host import composed
from .raw_event_batching import (
    EDITOR_INPUT_COMPACTION_WINDOW_SECONDS,
    RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE,
    REBUILD_BATCH_DELTA_CHUNK_SIZE,
    REBUILD_CURSOR_BATCH_SIZE,
    REBUILD_DELTA_SPILL_THRESHOLD,
    REBUILD_FAST_ACCOUNTING_ENABLED,
    REBUILD_INTERVAL_ACCUMULATOR_ENABLED,
    REBUILD_MEMORY_GUARD_ENABLED,
    REBUILD_MEMORY_SOFT_LIMIT_MB,
    REBUILD_RAW_FLUSH_BATCHES,
    REBUILD_SLOW_EVENT_THRESHOLD_MS,
)


LOGGER = logging.getLogger("al_backend.rebuild")
REBUILD_EVENT_DELTA_COLLECTION = "aggregate_rebuild_event_deltas"


def _has_count_or_file_delta(deltas: dict[str, Any]) -> bool:
    return bool(
        deltas.get("activityCountDeltas")
        or deltas.get("savedPrefabDeltas")
        or deltas.get("overtimeActivityCountDeltas")
        or deltas.get("overtimeSavedPrefabDeltas")
    )


def _primary_activity_type(deltas: dict[str, Any]) -> str | None:
    for key in ("activityCountDeltas", "overtimeActivityCountDeltas"):
        items = deltas.get(key)

        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("type"):
                    return str(item.get("type"))

    return None


def _cursor_with_batch_size(cursor: Any, batch_size: int = REBUILD_CURSOR_BATCH_SIZE) -> Any:
    batch_size_method = getattr(cursor, "batch_size", None)

    if callable(batch_size_method):
        return batch_size_method(batch_size)

    return cursor


def _minimal_event_for_report_rows(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": event.get("source"),
        "pluginVersion": event.get("pluginVersion"),
        "author": event.get("author"),
        "authorEmail": event.get("authorEmail", ""),
        "projectId": event.get("projectId") or "",
        "sessionId": event.get("sessionId") or "",
        "deviceId": event.get("deviceId") or "",
        "date": event.get("date"),
        "occurredAtLocal": event.get("occurredAtLocal"),
        "occurredAtUtc": event.get("occurredAtUtc"),
        "receivedAt": event.get("receivedAt"),
        "rawReportId": event.get("rawReportId"),
        "batchId": event.get("batchId"),
        "reportType": event.get("reportType", "auto"),
        "timeZoneId": event.get("timeZoneId"),
        "timeZoneDisplayName": event.get("timeZoneDisplayName"),
    }


def _rebuild_cap_ual_time_deltas_to_window(
    deltas: dict[str, Any],
    first_event_time: dt.datetime | None,
    last_event_time: dt.datetime | None,
) -> dict[str, Any]:
    if not first_event_time or not last_event_time or last_event_time <= first_event_time:
        return deltas

    max_microseconds = max(0, int((last_event_time - first_event_time).total_seconds() * MICROSECONDS_PER_SECOND))
    time_keys = (
        ("activeDeltaSeconds", "activeDeltaMicroseconds"),
        ("idleDeltaSeconds", "idleDeltaMicroseconds"),
        ("overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"),
    )
    total_microseconds = sum(_time_microseconds(deltas, seconds_key, microseconds_key) for seconds_key, microseconds_key in time_keys)
    excess_microseconds = total_microseconds - max_microseconds

    if excess_microseconds <= 0:
        return deltas

    capped = dict(deltas)
    for seconds_key, microseconds_key in time_keys:
        current_microseconds = _time_microseconds(capped, seconds_key, microseconds_key)
        reduction = min(current_microseconds, excess_microseconds)

        if reduction <= 0:
            continue

        current_microseconds -= reduction
        excess_microseconds -= reduction
        capped[microseconds_key] = current_microseconds
        capped[seconds_key] = _seconds_from_microseconds(current_microseconds)

        if excess_microseconds <= 0:
            break

    return capped


def _rebuild_current_rss_mb() -> float:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/self/statm", encoding="utf-8") as handle:
            resident_pages = int(handle.read().split()[1])
        return (resident_pages * page_size) / (1024 * 1024)
    except Exception:
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return usage / 1024
        except Exception:
            return 0.0


class _RebuildDeltaStore:
    def __init__(self, service: Any, token: str, threshold: int = REBUILD_DELTA_SPILL_THRESHOLD):
        self.service = service
        self.token = token
        self.threshold = threshold
        self.memory: dict[str, list[dict[str, Any]]] = {}
        self.spilled = False
        self.count = 0

    def add(self, batch_id: str, event: dict[str, Any], deltas: dict[str, Any]) -> None:
        item = {"batchId": batch_id, "event": _minimal_event_for_report_rows(event), "deltas": dict(deltas)}
        self.count += 1

        if self.spilled:
            self._insert_spill_item(item)
            return

        self.memory.setdefault(batch_id, []).append(item)

        if self.count > self.threshold:
            self._spill_memory()

    def batch_ids(self) -> set[str]:
        if self.spilled:
            return {
                str(item.get("batchId") or "")
                for item in _cursor_with_batch_size(
                    self.service.db.aggregate_rebuild_event_deltas.find({"token": self.token}, {"_id": 0, "batchId": 1})
                )
                if item.get("batchId")
            }

        return set(self.memory)

    def get_batch(self, batch_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        return list(self.iter_batch(batch_id))

    def iter_batch(self, batch_id: str) -> Any:
        if self.spilled:
            cursor = self.service.db.aggregate_rebuild_event_deltas.find(
                {"token": self.token, "batchId": batch_id},
                {"_id": 0, "event": 1, "deltas": 1},
            )

            for item in _cursor_with_batch_size(cursor):
                yield dict(item.get("event") or {}), dict(item.get("deltas") or {})

            return

        for item in self.memory.get(batch_id, []):
            yield dict(item.get("event") or {}), dict(item.get("deltas") or {})

    def count_batch(self, batch_id: str) -> int:
        if self.spilled:
            return int(self.service.db.aggregate_rebuild_event_deltas.count_documents({"token": self.token, "batchId": batch_id}))

        return len(self.memory.get(batch_id, []))

    def delete_batch(self, batch_id: str) -> None:
        if self.spilled:
            self.service.db.aggregate_rebuild_event_deltas.delete_many({"token": self.token, "batchId": batch_id})
            return

        self.memory.pop(batch_id, None)

    def iter_unprocessed(self, processed_batch_ids: set[str]) -> Any:
        for batch_id in sorted(self.batch_ids()):
            if batch_id in processed_batch_ids:
                continue

            yield batch_id, self.iter_batch(batch_id)

    def cleanup(self) -> None:
        self.memory.clear()
        self.service.db.aggregate_rebuild_event_deltas.delete_many({"token": self.token})

    def _insert_spill_item(self, item: dict[str, Any]) -> None:
        self.service.db.aggregate_rebuild_event_deltas.insert_one({"token": self.token, **item})

    def _spill_memory(self) -> None:
        docs = [
            {"token": self.token, **item}
            for items in self.memory.values()
            for item in items
        ]
        _insert_many_if_supported(self.service.db.aggregate_rebuild_event_deltas, docs)
        self.memory.clear()
        self.spilled = True


class ActivityAggregationRebuildMixin:
    def _set_rebuild_job_diagnostics(self, values: dict[str, Any]) -> None:
        job_id = getattr(self, "_active_rebuild_job_id", None)

        if not job_id:
            return

        self.db.aggregate_rebuild_jobs.update_one(
            {"jobId": job_id},
            {"$set": {**values, "updatedAt": dt.datetime.now(dt.UTC)}},
        )

    def _record_rebuild_observation(self, metrics: dict[str, Any], phase: str, started_at: float, processed: int = 0) -> None:
        elapsed = max(0.0, time.monotonic() - started_at)
        metrics.setdefault("phaseDurations", {})[phase] = elapsed

        if processed > 0 and elapsed > 0:
            metrics.setdefault("phaseRates", {})[phase] = processed / elapsed

        LOGGER.info(
            "Activity rebuild phase=%s seconds=%.3f processed=%s rate=%.2f/s rss_peak_mb=%.1f guard_pauses=%s",
            phase,
            elapsed,
            processed,
            (processed / elapsed) if processed > 0 and elapsed > 0 else 0,
            float(metrics.get("rssPeakMb") or 0),
            int(metrics.get("memoryGuardPauses") or 0),
        )

    def _maybe_apply_rebuild_memory_guard(self, metrics: dict[str, Any]) -> None:
        current_rss = _rebuild_current_rss_mb()
        metrics["rssCurrentMb"] = round(current_rss, 1)
        metrics["rssPeakMb"] = round(max(float(metrics.get("rssPeakMb") or 0), current_rss), 1)
        metrics["memorySoftLimitMb"] = REBUILD_MEMORY_SOFT_LIMIT_MB

        if not REBUILD_MEMORY_GUARD_ENABLED or current_rss <= REBUILD_MEMORY_SOFT_LIMIT_MB:
            return

        gc.collect()
        time.sleep(0.05)
        metrics["memoryGuardPauses"] = int(metrics.get("memoryGuardPauses") or 0) + 1
        self._set_rebuild_job_diagnostics(
            {
                "rssCurrentMb": metrics["rssCurrentMb"],
                "rssPeakMb": metrics["rssPeakMb"],
                "memorySoftLimitMb": REBUILD_MEMORY_SOFT_LIMIT_MB,
                "memoryGuardPauses": metrics["memoryGuardPauses"],
            }
        )

    def _build_rebuild_event_batch_report_rows(
        self,
        batch: dict[str, Any],
        delta_items: Any,
        cutoff: dt.datetime | None,
        progress_callback: Callable[[str, int, int], None] | None,
        *,
        batch_index: int,
        batch_total: int,
        batch_delta_total: int,
    ) -> list[dict[str, Any]]:
        merged_by_date: dict[str, dict[str, Any]] = {}
        first_time_by_date: dict[str, dt.datetime] = {}
        last_time_by_date: dict[str, dt.datetime] = {}
        last_event_by_date: dict[str, dict[str, Any]] = {}
        processed = 0

        for event, deltas in delta_items:
            processed += 1
            event_time = _raw_event_time(event)

            if cutoff and event_time and event_time <= cutoff:
                continue

            event_date = str(event.get("date") or "")

            if not event_date:
                continue

            batch_deltas = merged_by_date.setdefault(event_date, _empty_batch_deltas())
            _merge_batch_deltas(batch_deltas, deltas)
            last_event_by_date[event_date] = event

            if event_time:
                first_time = first_time_by_date.get(event_date)

                if first_time is None or event_time < first_time:
                    first_time_by_date[event_date] = event_time

                last_time = last_time_by_date.get(event_date)

                if last_time is None or event_time > last_time:
                    last_time_by_date[event_date] = event_time

            if processed % REBUILD_BATCH_DELTA_CHUNK_SIZE == 0:
                self._set_rebuild_job_diagnostics(
                    {
                        "currentBatch": batch_index,
                        "totalBatches": batch_total,
                        "currentBatchEvents": processed,
                        "totalBatchEvents": batch_delta_total,
                    }
                )
                self._report_rebuild_progress(progress_callback, "Rebuilding event batches", batch_index, batch_total)

        self._set_rebuild_job_diagnostics(
            {
                "currentBatch": batch_index,
                "totalBatches": batch_total,
                "currentBatchEvents": processed,
                "totalBatchEvents": batch_delta_total,
            }
        )

        rows: list[dict[str, Any]] = []

        for event_date, batch_deltas in merged_by_date.items():
            last_event = last_event_by_date.get(event_date)

            if str(batch.get("source") or "") == "ual":
                batch_deltas = _rebuild_cap_ual_time_deltas_to_window(
                    batch_deltas,
                    first_time_by_date.get(event_date),
                    last_time_by_date.get(event_date),
                )

            is_codex_presence_row = str(batch.get("source") or "") == "codex" and _has_count_or_file_delta(batch_deltas)

            if not last_event or not (_has_time_delta(batch_deltas) or is_codex_presence_row):
                continue

            rows.append(
                {
                    "source": batch.get("source"),
                    "pluginVersion": batch.get("pluginVersion"),
                    "author": batch.get("author") or "Unknown User",
                    "authorEmail": batch.get("authorEmail", ""),
                    "projectId": batch.get("projectId") or "",
                    "sessionId": batch.get("sessionId") or "",
                    "deviceId": batch.get("deviceId") or "",
                    "date": last_event.get("date"),
                    "recordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
                    "receivedAt": batch.get("receivedAt"),
                    "lastRecordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
                    "lastReceivedAt": batch.get("receivedAt"),
                    "timeZoneId": last_event.get("timeZoneId"),
                    "timeZoneDisplayName": last_event.get("timeZoneDisplayName"),
                    "rawReportId": batch.get("rawReportId"),
                    "batchId": batch.get("batchId"),
                    "challengeId": batch.get("challengeId"),
                    "reportType": batch.get("reportType", "auto"),
                    "activityType": _primary_activity_type(batch_deltas),
                    **batch_deltas,
                }
            )

        return rows

    def compact_editor_input_events_for_rebuild(
        self,
        target_dates: set[str],
        target_authors: set[str],
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not target_dates or not target_authors:
            return {
                "rawEventsBeforeCompaction": 0,
                "rawEventsAfterCompaction": 0,
                "compactedEvents": 0,
                "insertedCompactedEvents": 0,
                "deletedEditorInputEvents": 0,
            }

        raw_event_query = self._aggregate_rebuild_query(target_dates, target_authors, "author")
        raw_event_before = self.db.raw_activity_events.count_documents(raw_event_query)
        query = {
            **raw_event_query,
            "source": "ual",
            "eventType": "editor_input",
            "metadata.coalescedFromRawEvents": {"$ne": True},
        }
        total = self.db.raw_activity_events.count_documents(query)
        processed = 0
        inserted = 0
        deleted = 0
        compacted = 0
        delete_ids: list[str] = []
        insert_docs: list[dict[str, Any]] = []
        current_group_key: tuple[Any, ...] | None = None
        current_window: list[dict[str, Any]] = []
        window_start: dt.datetime | None = None

        def flush_window() -> None:
            nonlocal inserted, deleted, compacted, current_window, window_start

            if len(current_window) <= 1:
                current_window = []
                window_start = None
                return

            first = current_window[0]
            last = current_window[-1]
            first_at = _coerce_datetime(first.get("occurredAtUtc")) or _coerce_datetime(first.get("occurredAtLocal"))
            last_at = _coerce_datetime(last.get("occurredAtUtc")) or _coerce_datetime(last.get("occurredAtLocal")) or first_at
            metadata = dict(last.get("metadata") if isinstance(last.get("metadata"), dict) else {})
            metadata.update(
                {
                    "coalescedFromRawEvents": True,
                    "coalescedEventCount": len(current_window),
                    "coalescedFirstAtUtc": _isoformat_or_none(first_at),
                    "coalescedLastAtUtc": _isoformat_or_none(last_at),
                }
            )
            synthetic = dict(last)
            synthetic.pop("_id", None)
            synthetic.update(
                {
                    "eventId": f"rebuild-coalesced-{uuid.uuid4().hex}",
                    "occurredAtUtc": first_at or last_at or last.get("occurredAtUtc"),
                    "occurredAtLocal": first.get("occurredAtLocal") or last.get("occurredAtLocal"),
                    "receivedAt": last.get("receivedAt"),
                    "metadata": metadata,
                }
            )
            insert_docs.append(synthetic)
            delete_ids.extend(str(item.get("eventId") or "") for item in current_window if item.get("eventId"))
            inserted += 1
            deleted += len(current_window)
            compacted += max(0, len(current_window) - 1)
            current_window = []
            window_start = None

            if len(insert_docs) >= 500:
                _insert_many_if_supported(self.db.raw_activity_events, insert_docs)
                insert_docs.clear()

            if len(delete_ids) >= 1_000:
                self.db.raw_activity_events.delete_many({"eventId": {"$in": list(delete_ids)}})
                delete_ids.clear()

        sort_spec = [
            ("author", ASCENDING),
            ("date", ASCENDING),
            ("source", ASCENDING),
            ("projectId", ASCENDING),
            ("sessionId", ASCENDING),
            ("deviceId", ASCENDING),
            ("batchId", ASCENDING),
            ("timeZoneId", ASCENDING),
            ("occurredAtUtc", ASCENDING),
        ]
        cursor = _cursor_with_batch_size(self.db.raw_activity_events.find(query).sort(sort_spec))

        self._report_rebuild_progress(progress_callback, "Compacting editor input events", 0, total)

        for event in cursor:
            event_at = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAtLocal"))
            group_key = (
                event.get("author"),
                event.get("date"),
                event.get("source"),
                event.get("projectId"),
                event.get("sessionId"),
                event.get("deviceId"),
                event.get("batchId"),
                event.get("timeZoneId"),
            )

            if current_group_key is not None and group_key != current_group_key:
                flush_window()

            if window_start is not None and event_at is not None and (event_at - window_start).total_seconds() >= EDITOR_INPUT_COMPACTION_WINDOW_SECONDS:
                flush_window()

            current_group_key = group_key
            if not current_window:
                window_start = event_at
            current_window.append(event)
            processed += 1
            self._report_rebuild_progress(progress_callback, "Compacting editor input events", processed, total)

        flush_window()

        if insert_docs:
            _insert_many_if_supported(self.db.raw_activity_events, insert_docs)

        if delete_ids:
            self.db.raw_activity_events.delete_many({"eventId": {"$in": list(delete_ids)}})

        raw_event_after = self.db.raw_activity_events.count_documents(raw_event_query)
        result = {
            "rawEventsBeforeCompaction": raw_event_before,
            "rawEventsAfterCompaction": raw_event_after,
            "compactedEvents": compacted,
            "insertedCompactedEvents": inserted,
            "deletedEditorInputEvents": deleted,
        }
        self._set_rebuild_job_diagnostics(result)
        LOGGER.info("Activity rebuild editor input compaction result=%s", result)
        return result

    def compact_events_dropped_for_rebuild(
        self,
        target_dates: set[str],
        target_authors: set[str],
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not target_dates or not target_authors:
            return {
                "compactedEventsDropped": 0,
                "insertedCompactedEventsDropped": 0,
                "deletedEventsDropped": 0,
            }

        raw_event_query = self._aggregate_rebuild_query(target_dates, target_authors, "author")
        query = {
            **raw_event_query,
            "source": "ual",
            "eventType": "events_dropped",
            "metadata.coalescedFromRawEvents": {"$ne": True},
        }
        total = self.db.raw_activity_events.count_documents(query)
        processed = 0
        inserted = 0
        deleted = 0
        compacted = 0
        delete_ids: list[str] = []
        insert_docs: list[dict[str, Any]] = []
        current_group_key: tuple[Any, ...] | None = None
        current_group: list[dict[str, Any]] = []

        def dropped_count(event: dict[str, Any]) -> int:
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            try:
                return max(1, int(metadata.get("clickCount") or metadata.get("coalescedEventCount") or 1))
            except (TypeError, ValueError):
                return 1

        def flush_group() -> None:
            nonlocal inserted, deleted, compacted, current_group

            if len(current_group) <= 1:
                current_group = []
                return

            first = current_group[0]
            last = current_group[-1]
            first_at = _coerce_datetime(first.get("occurredAtUtc")) or _coerce_datetime(first.get("occurredAtLocal"))
            last_at = _coerce_datetime(last.get("occurredAtUtc")) or _coerce_datetime(last.get("occurredAtLocal")) or first_at
            total_dropped = sum(dropped_count(item) for item in current_group)
            metadata = dict(last.get("metadata") if isinstance(last.get("metadata"), dict) else {})
            metadata.update(
                {
                    "state": "pending_events_overflow",
                    "clickCount": total_dropped,
                    "coalescedFromRawEvents": True,
                    "coalescedEventCount": len(current_group),
                    "coalescedDroppedEventCount": total_dropped,
                    "coalescedFirstAtUtc": _isoformat_or_none(first_at),
                    "coalescedLastAtUtc": _isoformat_or_none(last_at),
                }
            )
            synthetic = dict(last)
            synthetic.pop("_id", None)
            synthetic.update(
                {
                    "eventId": f"rebuild-coalesced-events-dropped-{uuid.uuid4().hex}",
                    "occurredAtUtc": first_at or last_at or last.get("occurredAtUtc"),
                    "occurredAtLocal": first.get("occurredAtLocal") or last.get("occurredAtLocal"),
                    "receivedAt": last.get("receivedAt"),
                    "metadata": metadata,
                }
            )
            insert_docs.append(synthetic)
            delete_ids.extend(str(item.get("eventId") or "") for item in current_group if item.get("eventId"))
            inserted += 1
            deleted += len(current_group)
            compacted += max(0, len(current_group) - 1)
            current_group = []

            if len(insert_docs) >= 500:
                _insert_many_if_supported(self.db.raw_activity_events, insert_docs)
                insert_docs.clear()

            if len(delete_ids) >= 1_000:
                self.db.raw_activity_events.delete_many({"eventId": {"$in": list(delete_ids)}})
                delete_ids.clear()

        sort_spec = [
            ("author", ASCENDING),
            ("date", ASCENDING),
            ("source", ASCENDING),
            ("projectId", ASCENDING),
            ("sessionId", ASCENDING),
            ("deviceId", ASCENDING),
            ("batchId", ASCENDING),
            ("timeZoneId", ASCENDING),
            ("occurredAtUtc", ASCENDING),
        ]
        cursor = _cursor_with_batch_size(self.db.raw_activity_events.find(query).sort(sort_spec))
        self._report_rebuild_progress(progress_callback, "Compacting dropped event summaries", 0, total)

        for event in cursor:
            group_key = (
                event.get("author"),
                event.get("date"),
                event.get("source"),
                event.get("projectId"),
                event.get("sessionId"),
                event.get("deviceId"),
                event.get("batchId"),
                event.get("timeZoneId"),
            )

            if current_group_key is not None and group_key != current_group_key:
                flush_group()

            current_group_key = group_key
            current_group.append(event)
            processed += 1
            self._report_rebuild_progress(progress_callback, "Compacting dropped event summaries", processed, total)

        flush_group()

        if insert_docs:
            _insert_many_if_supported(self.db.raw_activity_events, insert_docs)

        if delete_ids:
            self.db.raw_activity_events.delete_many({"eventId": {"$in": list(delete_ids)}})

        result = {
            "compactedEventsDropped": compacted,
            "insertedCompactedEventsDropped": inserted,
            "deletedEventsDropped": deleted,
        }
        self._set_rebuild_job_diagnostics(result)
        LOGGER.info("Activity rebuild events_dropped compaction result=%s", result)
        return result

    def rebuild_aggregates_if_needed(
        self,
        force: bool = False,
        scope: str = "full",
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        metadata = self.db.aggregate_metadata.find_one({"kind": "activity"})

        if not force and metadata and metadata.get("version") == composed(self).aggregates_version:
            return

        if not force and scope == "today":
            target_dates = self._current_author_calendar_dates()
            self.rebuild_aggregates_for_dates(target_dates[0], dates=target_dates, progress_callback=progress_callback)
            self.db.aggregate_metadata.update_one(
                {"kind": "activity"},
                {
                    "$set": {
                        "kind": "activity",
                        "version": composed(self).aggregates_version,
                        "rebuiltAt": dt.datetime.now(dt.UTC),
                        "rebuildScope": "today",
                        "rebuildDates": target_dates,
                    }
                },
                upsert=True,
            )
            composed(self).invalidate_activity_summary_cache(target_dates)
            return

        previous_suppression = getattr(self, "_suppress_rebuild_notification_side_effects", False)
        previous_rebuild_in_progress = getattr(self, "_rebuild_in_progress", False)
        self._suppress_rebuild_notification_side_effects = True
        self._rebuild_in_progress = True
        self._daily_consumed_microseconds_cache = {}
        try:
            self._report_rebuild_progress(progress_callback, "Clearing derived data", 0, 1)
            self.db.report_rows.delete_many({})
            self.db.daily_author_activity.delete_many({})
            self.db.activity_author_day_summary_snapshots.delete_many({})
            self.db.activity_day_summary_snapshots.delete_many({})
            self.db.aggregate_session_state.delete_many({})
            self.db.aggregate_day_state.delete_many({})
            self._report_rebuild_progress(progress_callback, "Clearing derived data", 1, 1)
            self._rebuild_aggregates_from_sources(progress_callback=progress_callback)
            self._report_rebuild_progress(progress_callback, "Capturing day state", 0, 1)
            self._persist_aggregate_day_state(sorted(self.db.daily_author_activity.distinct("date")), set())
            self._report_rebuild_progress(progress_callback, "Capturing day state", 1, 1)

            self.db.aggregate_metadata.update_one(
                {"kind": "activity"},
                {"$set": {"kind": "activity", "version": composed(self).aggregates_version, "rebuiltAt": dt.datetime.now(dt.UTC)}},
                upsert=True,
            )
            composed(self).invalidate_activity_summary_cache()
        finally:
            self._daily_consumed_microseconds_cache = None
            self._suppress_rebuild_notification_side_effects = previous_suppression
            self._rebuild_in_progress = previous_rebuild_in_progress

    def _current_author_calendar_dates(self) -> list[str]:
        now = dt.datetime.now(dt.UTC)
        dates: set[str] = {now.date().isoformat()}

        for profile in self.db.author_profiles.find({}, {"_id": 0, "timeZoneId": 1}):
            time_zone_id = str(profile.get("timeZoneId") or "")

            try:
                zone = ZoneInfo(time_zone_id) if time_zone_id else dt.UTC
            except ZoneInfoNotFoundError:
                zone = dt.UTC

            dates.add(now.astimezone(zone).date().isoformat())

        return sorted(dates)

    def rebuild_aggregates_for_dates(
        self,
        start_date: str,
        end_date: str | None = None,
        authors: list[str] | tuple[str, ...] | set[str] | None = None,
        dates: list[str] | tuple[str, ...] | set[str] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        target_dates = self._aggregate_rebuild_dates(start_date, end_date, dates)
        target_authors: set[str] = set()
        for author in authors or []:
            normalized_author = str(author or "").strip()

            if not normalized_author:
                continue

            target_authors.update(composed(self).author_alias_keys(normalized_author))
        scoped_query = self._aggregate_rebuild_query(target_dates, target_authors, "author")
        raw_author_query = self._aggregate_rebuild_query(target_dates, target_authors, "rawAuthor")
        compaction_result = self.compact_editor_input_events_for_rebuild(
            set(target_dates),
            target_authors,
            progress_callback=progress_callback,
        )
        dropped_compaction_result = self.compact_events_dropped_for_rebuild(
            set(target_dates),
            target_authors,
            progress_callback=progress_callback,
        )
        compaction_result.update(dropped_compaction_result)
        compaction_result["rawEventsAfterCompaction"] = self.db.raw_activity_events.count_documents(scoped_query)
        compaction_result["compactedEventsByType"] = {
            "editor_input": int(compaction_result.get("compactedEvents") or 0),
            "events_dropped": int(compaction_result.get("compactedEventsDropped") or 0),
        }
        compaction_result["compactedEventsTotal"] = sum(compaction_result["compactedEventsByType"].values())
        self._set_rebuild_job_diagnostics(compaction_result)
        self._report_rebuild_progress(progress_callback, "Clearing scoped derived data", 0, 1)
        backfilled_live_meeting_events = self._backfill_live_meeting_events_from_report_rows(scoped_query)
        deleted_report_rows = self.db.report_rows.delete_many(scoped_query).deleted_count
        deleted_daily = self.db.daily_author_activity.delete_many(scoped_query).deleted_count
        deleted_state = self.db.aggregate_session_state.delete_many(scoped_query).deleted_count
        deleted_day_state = self.db.aggregate_day_state.delete_many(scoped_query).deleted_count

        for day in target_dates:
            if target_authors:
                for author in target_authors:
                    deleted_state += self.db.aggregate_session_state.delete_many(
                        {"_id": {"$regex": f"(^|\\|){re.escape(author)}\\|.*{re.escape(day)}(\\||$)"}}
                    ).deleted_count
            else:
                deleted_state += self.db.aggregate_session_state.delete_many({"_id": {"$regex": f"(^|\\|){re.escape(day)}(\\||$)"}}).deleted_count

        self._report_rebuild_progress(progress_callback, "Clearing scoped derived data", 1, 1)
        previous_dates = getattr(self, "_aggregate_rebuild_target_dates", None)
        previous_authors = getattr(self, "_aggregate_rebuild_target_authors", None)
        previous_suppression = getattr(self, "_suppress_rebuild_notification_side_effects", False)
        previous_rebuild_in_progress = getattr(self, "_rebuild_in_progress", False)
        self._aggregate_rebuild_target_dates = set(target_dates)
        self._aggregate_rebuild_target_authors = set(target_authors)
        self._suppress_rebuild_notification_side_effects = True
        self._rebuild_in_progress = True
        self._daily_consumed_microseconds_cache = {}

        try:
            self._rebuild_aggregates_from_sources(set(target_dates), target_authors, progress_callback=progress_callback)
        finally:
            self._aggregate_rebuild_target_dates = previous_dates
            self._aggregate_rebuild_target_authors = previous_authors
            self._suppress_rebuild_notification_side_effects = previous_suppression
            self._rebuild_in_progress = previous_rebuild_in_progress
            self._daily_consumed_microseconds_cache = None

        self._report_rebuild_progress(progress_callback, "Capturing day state", 0, 1)
        state_count = self._persist_aggregate_day_state(target_dates, target_authors)
        self._report_rebuild_progress(progress_callback, "Capturing day state", 1, 1)
        composed(self).invalidate_activity_summary_cache(target_dates)
        rebuilt_snapshots = composed(self).rebuild_activity_day_summary_snapshots_for_dates(target_dates, sorted(target_authors))
        return {
            "ok": True,
            "dates": target_dates,
            "authors": sorted(target_authors),
            "deletedReportRows": deleted_report_rows,
            "deletedDailyAuthorActivity": deleted_daily,
            "deletedAggregateSessionState": deleted_state,
            "deletedAggregateDayState": deleted_day_state,
            "capturedAggregateDayState": state_count,
            "backfilledLiveMeetingEvents": backfilled_live_meeting_events,
            "rawAuthorQuery": raw_author_query,
            "rebuiltActivitySnapshots": rebuilt_snapshots,
            **(getattr(self, "_last_rebuild_metrics", {}) or {}),
            **compaction_result,
        }

    def _backfill_live_meeting_events_from_report_rows(self, scoped_query: dict[str, Any]) -> int:
        return 0

    def rebuild_aggregates_for_author_dates(self, authors: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
        raw_authors = {str(author or "Unknown User") for author in authors if str(author or "").strip()}
        dates: set[str] = set()

        for collection, author_field in (
            (self.db.activity_snapshots, "author"),
            (self.db.raw_activity_events, "author"),
            (self.db.break_events, "rawAuthor"),
            (self.db.meeting_events, "rawAuthor"),
            (self.db.status_events, "rawAuthor"),
            (self.db.daily_author_activity, "author"),
            (self.db.report_rows, "author"),
        ):
            for day in collection.distinct("date", {author_field: {"$in": sorted(raw_authors)}}):
                if day:
                    dates.add(str(day))

        if not dates:
            return {"ok": True, "dates": [], "authors": sorted(raw_authors)}

        # Alias changes can move raw authors between display authors, so rebuild whole affected dates.
        return self.rebuild_aggregates_for_dates(sorted(dates)[0], dates=sorted(dates))

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

    def _rebuild_aggregates_from_sources(
        self,
        target_dates: set[str] | None = None,
        target_authors: set[str] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        snapshot_query = self._aggregate_rebuild_query(target_dates, target_authors, "author")
        raw_author_query = self._aggregate_rebuild_query(target_dates, target_authors, "rawAuthor")
        raw_event_query = self._aggregate_rebuild_query(target_dates, target_authors, "author")

        metrics: dict[str, Any] = {"memoryGuardPauses": 0, "rssPeakMb": 0}
        snapshots = _cursor_with_batch_size(self.db.activity_snapshots.find(snapshot_query).sort("receivedAt", ASCENDING))
        snapshot_total = self.db.activity_snapshots.count_documents(snapshot_query)
        snapshot_count = 0
        phase_started_at = time.monotonic()

        for snapshot in snapshots:
            self._apply_snapshot_to_aggregates(snapshot)
            snapshot_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding snapshots", snapshot_count, snapshot_total)
            self._maybe_apply_rebuild_memory_guard(metrics)

        self._record_rebuild_observation(metrics, "Rebuilding snapshots", phase_started_at, snapshot_count)

        if snapshot_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding snapshots", 0, 0)

        batch_id_query: dict[str, Any] = {}
        full_rebuild = target_dates is None and target_authors is None
        delta_store = _RebuildDeltaStore(self, uuid.uuid4().hex)
        orphan_report_rows: list[dict[str, Any]] = []

        def flush_orphan_report_rows() -> None:
            if not orphan_report_rows:
                return

            _insert_many_if_supported(self.db.report_rows, list(orphan_report_rows))
            orphan_report_rows.clear()

        def add_orphan_report_row(event: dict[str, Any], deltas: dict[str, Any]) -> None:
            is_codex_presence_row = str(event.get("source") or "") == "codex" and _has_count_or_file_delta(deltas)

            if not (_has_time_delta(deltas) or is_codex_presence_row):
                return

            resolved_author = composed(self).resolve_author_alias(event.get("author") or "Unknown User")
            orphan_report_rows.append(
                {
                    "source": event.get("source"),
                    "pluginVersion": event.get("pluginVersion"),
                    "author": resolved_author,
                    "authorEmail": event.get("authorEmail", ""),
                    "projectId": event.get("projectId") or "",
                    "sessionId": event.get("sessionId") or "",
                    "deviceId": event.get("deviceId") or "",
                    "date": event.get("date"),
                    "recordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
                    "receivedAt": event.get("receivedAt"),
                    "lastRecordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
                    "lastReceivedAt": event.get("receivedAt"),
                    "timeZoneId": event.get("timeZoneId"),
                    "timeZoneDisplayName": event.get("timeZoneDisplayName"),
                    "rawReportId": event.get("rawReportId"),
                    "batchId": event.get("batchId"),
                    "reportType": event.get("reportType", "auto"),
                    "activityType": _primary_activity_type(deltas),
                    **deltas,
                }
            )

        try:
            raw_events = _cursor_with_batch_size(self.db.raw_activity_events.find(raw_event_query).sort("occurredAtUtc", ASCENDING))
            raw_event_total = self.db.raw_activity_events.count_documents(raw_event_query)
            raw_event_count = 0
            raw_event_batch: list[dict[str, Any]] = []
            raw_flush_size = RAW_EVENT_ACCOUNTING_SUB_BATCH_SIZE * REBUILD_RAW_FLUSH_BATCHES
            phase_started_at = time.monotonic()
            slow_event_threshold_seconds = max(0.0, REBUILD_SLOW_EVENT_THRESHOLD_MS / 1000.0)
            metrics["rawFastAccountingEnabled"] = REBUILD_FAST_ACCOUNTING_ENABLED
            metrics["rawIntervalAccumulatorEnabled"] = REBUILD_INTERVAL_ACCUMULATOR_ENABLED
            metrics.setdefault("rawSlowEvents", [])
            metrics.setdefault("rawSlowEventsByType", {})
            metrics.setdefault("rawAccountingSecondsByEventType", {})
            metrics.setdefault("rawAccumulatorEventsByType", {})
            metrics.setdefault("rawLegacyFallbackEventsByType", {})
            metrics["rawAccumulatorEvents"] = 0
            metrics["rawAccumulatorSeconds"] = 0.0
            metrics["rawAccumulatorEventsPerSecond"] = 0.0
            metrics["rawPhaseWallSeconds"] = 0.0
            self._set_rebuild_job_diagnostics(
                {
                    "rawFastAccountingEnabled": REBUILD_FAST_ACCOUNTING_ENABLED,
                    "rawIntervalAccumulatorEnabled": REBUILD_INTERVAL_ACCUMULATOR_ENABLED,
                }
            )

            def process_raw_event_batch(events: list[dict[str, Any]]) -> None:
                nonlocal raw_event_count
                if not events:
                    return

                composed(self)._begin_raw_event_batch_accounting(events)
                try:
                    for event in events:
                        accounting_started_at = time.perf_counter()
                        event_type_key = f"{str(event.get('source') or '')}:{str(event.get('eventType') or '')}"
                        accumulator_handled, deltas = self._try_apply_rebuild_interval_accumulator_event(event)
                        if accumulator_handled:
                            accumulator_elapsed = max(0.0, time.perf_counter() - accounting_started_at)
                            metrics["rawAccumulatorEvents"] = int(metrics.get("rawAccumulatorEvents") or 0) + 1
                            metrics["rawAccumulatorSeconds"] = float(metrics.get("rawAccumulatorSeconds") or 0.0) + accumulator_elapsed
                            if metrics["rawAccumulatorSeconds"] > 0:
                                metrics["rawAccumulatorEventsPerSecond"] = (
                                    int(metrics.get("rawAccumulatorEvents") or 0)
                                    / float(metrics.get("rawAccumulatorSeconds") or 1.0)
                                )
                            accumulator_by_type = metrics.setdefault("rawAccumulatorEventsByType", {})
                            accumulator_by_type[event_type_key] = int(accumulator_by_type.get(event_type_key) or 0) + 1
                        else:
                            fallback_by_type = metrics.setdefault("rawLegacyFallbackEventsByType", {})
                            fallback_by_type[event_type_key] = int(fallback_by_type.get(event_type_key) or 0) + 1
                            deltas = self._apply_raw_event_to_aggregates(event)
                        accounting_elapsed = max(0.0, time.perf_counter() - accounting_started_at)
                        accounting_by_type = metrics.setdefault("rawAccountingSecondsByEventType", {})
                        accounting_by_type[event_type_key] = float(accounting_by_type.get(event_type_key) or 0.0) + accounting_elapsed
                        context = getattr(self, "_raw_event_batch_accounting", None)
                        if context:
                            timing = context.setdefault("rawTiming", {})
                            timing["rawEventAccounting"] = float(timing.get("rawEventAccounting") or 0.0) + accounting_elapsed
                        if slow_event_threshold_seconds and accounting_elapsed >= slow_event_threshold_seconds:
                            slow_events = metrics.setdefault("rawSlowEvents", [])
                            slow_by_type = metrics.setdefault("rawSlowEventsByType", {})
                            slow_by_type[event_type_key] = int(slow_by_type.get(event_type_key) or 0) + 1
                            slow_events.append(
                                {
                                    "eventType": str(event.get("eventType") or ""),
                                    "source": str(event.get("source") or ""),
                                    "date": str(event.get("date") or ""),
                                    "occurredAtUtc": str(event.get("occurredAtUtc") or ""),
                                    "batchId": str(event.get("batchId") or ""),
                                    "durationMs": round(accounting_elapsed * 1000, 1),
                                }
                            )
                            del slow_events[:-20]
                        raw_event_count += 1
                        raw_elapsed = max(0.001, time.monotonic() - phase_started_at)
                        metrics["rawPhaseWallSeconds"] = raw_elapsed
                        if raw_event_count == raw_event_total or raw_event_count % 100 == 0:
                            self._set_rebuild_job_diagnostics(
                                {
                                    "rawEventsPerSecond": raw_event_count / raw_elapsed,
                                    "rawFlushSize": raw_flush_size,
                                    "rawSlowEvents": metrics.get("rawSlowEvents", []),
                                    "rawSlowEventsByType": metrics.get("rawSlowEventsByType", {}),
                                    "rawPhaseWallSeconds": metrics.get("rawPhaseWallSeconds", 0.0),
                                    "rawAccumulatorEvents": metrics.get("rawAccumulatorEvents", 0),
                                    "rawAccumulatorEventsByType": metrics.get("rawAccumulatorEventsByType", {}),
                                    "rawAccumulatorSeconds": metrics.get("rawAccumulatorSeconds", 0.0),
                                    "rawAccumulatorEventsPerSecond": metrics.get("rawAccumulatorEventsPerSecond", 0.0),
                                    "rawLegacyFallbackEventsByType": metrics.get("rawLegacyFallbackEventsByType", {}),
                                }
                            )
                        self._report_rebuild_progress(progress_callback, "Rebuilding raw activity events", raw_event_count, raw_event_total)
                        batch_id = str(event.get("batchId") or "")

                        if batch_id:
                            delta_store.add(batch_id, event, deltas)
                            continue

                        add_orphan_report_row(event, deltas)

                        if len(orphan_report_rows) >= 1_000:
                            flush_orphan_report_rows()
                finally:
                    composed(self)._finish_raw_event_batch_accounting()
                    stats = getattr(self, "_last_raw_event_batch_accounting_stats", {}) or {}
                    raw_timing = metrics.setdefault("rawTiming", {})
                    for key, value in (stats.get("rawTiming") or {}).items():
                        raw_timing[key] = float(raw_timing.get(key) or 0.0) + float(value or 0.0)
                    metrics["rawFlushCount"] = int(metrics.get("rawFlushCount") or 0) + int(stats.get("rawFlushCount") or 0)
                    metrics["rawStateWrites"] = int(metrics.get("rawStateWrites") or 0) + int(stats.get("rawStateWrites") or 0)
                    metrics["rawDailyWrites"] = int(metrics.get("rawDailyWrites") or 0) + int(stats.get("rawDailyWrites") or 0)
                    metrics["rawDailyMergeFlushSeconds"] = float(metrics.get("rawDailyMergeFlushSeconds") or 0.0) + float(
                        stats.get("rawDailyMergeFlushSeconds") or 0.0
                    )
                    metrics["rawFastContextBuildSeconds"] = float(metrics.get("rawFastContextBuildSeconds") or 0.0) + float(
                        stats.get("rawFastContextBuildSeconds") or 0.0
                    )
                    self._set_rebuild_job_diagnostics(
                        {
                            "rawTiming": raw_timing,
                            "rawFlushCount": metrics.get("rawFlushCount", 0),
                            "rawStateWrites": metrics.get("rawStateWrites", 0),
                            "rawDailyWrites": metrics.get("rawDailyWrites", 0),
                            "rawFastAccountingEnabled": stats.get("rawFastAccountingEnabled", REBUILD_FAST_ACCOUNTING_ENABLED),
                            "rawIntervalAccumulatorEnabled": stats.get(
                                "rawIntervalAccumulatorEnabled",
                                REBUILD_INTERVAL_ACCUMULATOR_ENABLED,
                            ),
                            "rawFastContextBuildSeconds": metrics.get("rawFastContextBuildSeconds", 0.0),
                            "rawDailyMergeFlushSeconds": metrics.get("rawDailyMergeFlushSeconds", 0.0),
                            "rawSlowEvents": metrics.get("rawSlowEvents", []),
                            "rawSlowEventsByType": metrics.get("rawSlowEventsByType", {}),
                            "rawPhaseWallSeconds": metrics.get("rawPhaseWallSeconds", 0.0),
                            "rawAccountingSecondsByEventType": metrics.get("rawAccountingSecondsByEventType", {}),
                            "rawAccumulatorEvents": metrics.get("rawAccumulatorEvents", 0),
                            "rawAccumulatorEventsByType": metrics.get("rawAccumulatorEventsByType", {}),
                            "rawAccumulatorSeconds": metrics.get("rawAccumulatorSeconds", 0.0),
                            "rawAccumulatorEventsPerSecond": metrics.get("rawAccumulatorEventsPerSecond", 0.0),
                            "rawLegacyFallbackEventsByType": metrics.get("rawLegacyFallbackEventsByType", {}),
                        }
                    )
                    self._maybe_apply_rebuild_memory_guard(metrics)

            for event in raw_events:
                raw_event_batch.append(event)
                if len(raw_event_batch) >= raw_flush_size:
                    process_raw_event_batch(raw_event_batch)
                    raw_event_batch = []

            process_raw_event_batch(raw_event_batch)
            self._record_rebuild_observation(metrics, "Rebuilding raw activity events", phase_started_at, raw_event_count)

            if raw_event_total == 0:
                self._report_rebuild_progress(progress_callback, "Rebuilding raw activity events", 0, 0)

            if not full_rebuild:
                batch_ids = delta_store.batch_ids()
                batch_id_query = {"batchId": {"$in": sorted(batch_ids)}} if batch_ids else {"batchId": {"$in": []}}

            last_report_time_by_author: dict[str, dt.datetime] = {}
            batch_total = self.db.raw_event_batches.count_documents(batch_id_query)
            batch_count = 0
            processed_batch_ids: set[str] = set()
            phase_started_at = time.monotonic()

            for batch in _cursor_with_batch_size(self.db.raw_event_batches.find(batch_id_query).sort("receivedAt", ASCENDING)):
                batch_count += 1
                self._report_rebuild_progress(progress_callback, "Rebuilding event batches", batch_count, batch_total)
                batch_id = str(batch.get("batchId") or "")
                processed_batch_ids.add(batch_id)

                author = str(batch.get("author") or "Unknown User")
                cutoff = last_report_time_by_author.get(author)
                batch_delta_total = delta_store.count_batch(batch_id)
                self._set_rebuild_job_diagnostics(
                    {
                        "currentBatch": batch_count,
                        "totalBatches": batch_total,
                        "currentBatchEvents": 0,
                        "totalBatchEvents": batch_delta_total,
                    }
                )
                rows = self._build_rebuild_event_batch_report_rows(
                    batch,
                    delta_store.iter_batch(batch_id),
                    cutoff,
                    progress_callback,
                    batch_index=batch_count,
                    batch_total=batch_total,
                    batch_delta_total=batch_delta_total,
                )
                materialized_rows = [
                    row
                    for row in rows
                    if self._should_materialize_aggregate_date(str(row.get("date") or ""), str(row.get("author") or "Unknown User"))
                ]
                _insert_many_if_supported(self.db.report_rows, materialized_rows)

                for row in rows:
                    row_time = _report_row_time(row)

                    if row_time:
                        last_report_time_by_author[author] = row_time

                delta_store.delete_batch(batch_id)
                rows.clear()
                materialized_rows.clear()
                self._maybe_apply_rebuild_memory_guard(metrics)

            self._record_rebuild_observation(metrics, "Rebuilding event batches", phase_started_at, batch_count)

            for _batch_id, delta_items in delta_store.iter_unprocessed(processed_batch_ids):
                for event, deltas in delta_items:
                    add_orphan_report_row(event, deltas)

                    if len(orphan_report_rows) >= 1_000:
                        flush_orphan_report_rows()

                delta_store.delete_batch(_batch_id)

            flush_orphan_report_rows()
        finally:
            delta_store.cleanup()
            self._set_rebuild_job_diagnostics(
                {
                    "rssPeakMb": metrics.get("rssPeakMb"),
                    "memorySoftLimitMb": REBUILD_MEMORY_SOFT_LIMIT_MB,
                    "memoryGuardPauses": metrics.get("memoryGuardPauses", 0),
                }
            )

        break_total = self.db.break_events.count_documents(raw_author_query)
        break_count = 0

        phase_started_at = time.monotonic()
        for event in _cursor_with_batch_size(self.db.break_events.find(raw_author_query).sort("timestamp", ASCENDING)):
            break_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding Telegram activity", break_count, break_total)
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else None
            composed(self)._insert_telegram_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("telegramUsername") or ""),
                str(event.get("eventType") or "telegram"),
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("telegramStatus") or event.get("eventType") or "telegram"),
                metadata,
                respect_rebuild_scope=True,
            )
            self._maybe_apply_rebuild_memory_guard(metrics)

        self._record_rebuild_observation(metrics, "Rebuilding Telegram activity", phase_started_at, break_count)

        if break_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding Telegram activity", 0, 0)

        meeting_total = self.db.meeting_events.count_documents(raw_author_query)
        meeting_count = 0

        phase_started_at = time.monotonic()
        for event in _cursor_with_batch_size(self.db.meeting_events.find(raw_author_query).sort("timestamp", ASCENDING)):
            meeting_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding Discord meetings", meeting_count, meeting_total)
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            event_type = str(event.get("eventType") or "reconcile")
            if event_type == "live":
                continue
            metadata = {}

            composed(self)._insert_discord_meeting_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("discordUserId") or ""),
                str(event.get("discordUsername") or ""),
                event_type,
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("eventType") or "meeting"),
                str(event.get("guildId") or ""),
                str(event.get("channelId") or ""),
                metadata,
            )
            self._maybe_apply_rebuild_memory_guard(metrics)

        self._record_rebuild_observation(metrics, "Rebuilding Discord meetings", phase_started_at, meeting_count)

        if meeting_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding Discord meetings", 0, 0)

        self._report_rebuild_progress(progress_callback, "Rebuilding status events", 0, 1)
        composed(self)._materialize_status_report_rows()
        self._report_rebuild_progress(progress_callback, "Rebuilding status events", 1, 1)
        self._last_rebuild_metrics = {
            "rssPeakMb": metrics.get("rssPeakMb"),
            "memorySoftLimitMb": REBUILD_MEMORY_SOFT_LIMIT_MB,
            "memoryGuardPauses": metrics.get("memoryGuardPauses", 0),
            "phaseDurations": metrics.get("phaseDurations", {}),
            "phaseRates": metrics.get("phaseRates", {}),
            "rawTiming": metrics.get("rawTiming", {}),
            "rawFlushCount": metrics.get("rawFlushCount", 0),
            "rawStateWrites": metrics.get("rawStateWrites", 0),
            "rawDailyWrites": metrics.get("rawDailyWrites", 0),
            "rawFastAccountingEnabled": metrics.get("rawFastAccountingEnabled", REBUILD_FAST_ACCOUNTING_ENABLED),
            "rawIntervalAccumulatorEnabled": metrics.get("rawIntervalAccumulatorEnabled", REBUILD_INTERVAL_ACCUMULATOR_ENABLED),
            "rawFastContextBuildSeconds": metrics.get("rawFastContextBuildSeconds", 0.0),
            "rawDailyMergeFlushSeconds": metrics.get("rawDailyMergeFlushSeconds", 0.0),
            "rawSlowEvents": metrics.get("rawSlowEvents", []),
            "rawSlowEventsByType": metrics.get("rawSlowEventsByType", {}),
            "rawPhaseWallSeconds": metrics.get("rawPhaseWallSeconds", 0.0),
            "rawAccountingSecondsByEventType": metrics.get("rawAccountingSecondsByEventType", {}),
            "rawAccumulatorEvents": metrics.get("rawAccumulatorEvents", 0),
            "rawAccumulatorEventsByType": metrics.get("rawAccumulatorEventsByType", {}),
            "rawAccumulatorSeconds": metrics.get("rawAccumulatorSeconds", 0.0),
            "rawAccumulatorEventsPerSecond": metrics.get("rawAccumulatorEventsPerSecond", 0.0),
            "rawLegacyFallbackEventsByType": metrics.get("rawLegacyFallbackEventsByType", {}),
        }
        self._set_rebuild_job_diagnostics(self._last_rebuild_metrics)

    def _report_rebuild_progress(
        self,
        progress_callback: Callable[[str, int, int], None] | None,
        phase: str,
        current: int,
        total: int,
    ) -> None:
        if progress_callback is None:
            return

        if total > 0 and current not in {0, total} and current % 100 != 0:
            return

        progress_callback(phase, current, total)

    def _aggregate_rebuild_dates(
        self,
        start_date: str,
        end_date: str | None,
        dates: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        if dates:
            values = sorted({str(value) for value in dates if str(value or "").strip()})

            for value in values:
                dt.date.fromisoformat(value)

            return values

        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date or start_date)

        if end < start:
            raise ValueError("end_date must be greater than or equal to start_date")

        days: list[str] = []
        current = start

        while current <= end:
            days.append(current.isoformat())
            current += dt.timedelta(days=1)

        return days

    def _aggregate_rebuild_query(
        self,
        target_dates: set[str] | list[str] | tuple[str, ...] | None,
        target_authors: set[str] | None,
        author_field: str,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {}

        if target_dates is not None:
            query["date"] = {"$in": sorted(target_dates)}

        if target_authors:
            query[author_field] = {"$in": sorted(target_authors)}

        return query

    def _should_materialize_aggregate_date(self, day_date: str, author: str | None = None) -> bool:
        target_dates = getattr(self, "_aggregate_rebuild_target_dates", None)

        if target_dates is not None and day_date not in target_dates:
            return False

        target_authors = getattr(self, "_aggregate_rebuild_target_authors", None)

        if target_authors and composed(self).resolve_author_alias(author or "Unknown User") not in target_authors:
            return False

        return True

    def _persist_aggregate_day_state(self, target_dates: list[str], target_authors: set[str]) -> int:
        captured = 0
        now = dt.datetime.now(dt.UTC)

        for state in _cursor_with_batch_size(self.db.aggregate_session_state.find({})):
            state_date = str(state.get("date") or "")

            if state_date not in target_dates:
                continue

            state_author = composed(self).resolve_author_alias(str(state.get("author") or "Unknown User"))

            if target_authors and state_author not in target_authors:
                continue

            state_id = str(state.get("_id") or "")
            self.db.aggregate_day_state.update_one(
                {"date": state_date, "author": state_author, "stateId": state_id},
                {
                    "$set": {
                        "date": state_date,
                        "author": state_author,
                        "stateId": state_id,
                        "state": dict(state),
                        "rebuiltAt": now,
                    },
                },
                upsert=True,
            )
            captured += 1

        return captured
