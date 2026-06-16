from __future__ import annotations

from typing import Any, Callable

from ..activity_math import *
from ..backend_composable_host import composed


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


class ActivityAggregationRebuildMixin:
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
        self._suppress_rebuild_notification_side_effects = True
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
        self._aggregate_rebuild_target_dates = set(target_dates)
        self._aggregate_rebuild_target_authors = set(target_authors)
        self._suppress_rebuild_notification_side_effects = True
        self._daily_consumed_microseconds_cache = {}

        try:
            self._rebuild_aggregates_from_sources(set(target_dates), target_authors, progress_callback=progress_callback)
        finally:
            self._aggregate_rebuild_target_dates = previous_dates
            self._aggregate_rebuild_target_authors = previous_authors
            self._suppress_rebuild_notification_side_effects = previous_suppression
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

        snapshots = self.db.activity_snapshots.find(snapshot_query).sort("receivedAt", ASCENDING)
        snapshot_total = self.db.activity_snapshots.count_documents(snapshot_query)
        snapshot_count = 0

        for snapshot in snapshots:
            self._apply_snapshot_to_aggregates(snapshot)
            snapshot_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding snapshots", snapshot_count, snapshot_total)

        if snapshot_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding snapshots", 0, 0)

        batch_id_query: dict[str, Any] = {}
        full_rebuild = target_dates is None and target_authors is None
        batch_ids = (
            {
                str(batch.get("batchId") or "")
                for batch in self.db.raw_event_batches.find({}, {"_id": 0, "batchId": 1})
                if batch.get("batchId")
            }
            if full_rebuild
            else set()
        )
        batch_delta_items_by_batch_id: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        orphan_report_rows: list[dict[str, Any]] = []

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

        raw_events = self.db.raw_activity_events.find(raw_event_query).sort("occurredAtUtc", ASCENDING)
        raw_event_total = self.db.raw_activity_events.count_documents(raw_event_query)
        raw_event_count = 0

        for event in raw_events:
            deltas = self._apply_raw_event_to_aggregates(event)
            raw_event_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding raw activity events", raw_event_count, raw_event_total)
            batch_id = str(event.get("batchId") or "")

            if batch_id and ((full_rebuild and batch_id in batch_ids) or not full_rebuild):
                batch_delta_items = batch_delta_items_by_batch_id.setdefault(batch_id, [])
                batch_delta_items.append((event, deltas))
                continue

            add_orphan_report_row(event, deltas)

        if raw_event_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding raw activity events", 0, 0)

        if not full_rebuild:
            batch_ids = set(batch_delta_items_by_batch_id)
            batch_id_query = {"batchId": {"$in": sorted(batch_ids)}} if batch_ids else {"batchId": {"$in": []}}

        last_report_time_by_author: dict[str, dt.datetime] = {}
        batch_total = self.db.raw_event_batches.count_documents(batch_id_query)
        batch_count = 0
        processed_batch_ids: set[str] = set()

        for batch in self.db.raw_event_batches.find(batch_id_query).sort("receivedAt", ASCENDING):
            batch_count += 1
            self._report_rebuild_progress(progress_callback, "Rebuilding event batches", batch_count, batch_total)
            batch_id = str(batch.get("batchId") or "")
            processed_batch_ids.add(batch_id)

            author = str(batch.get("author") or "Unknown User")
            cutoff = last_report_time_by_author.get(author)
            rows = composed(self)._build_event_batch_report_rows(batch, batch_delta_items_by_batch_id.get(batch_id, []), cutoff)
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

        for batch_id, delta_items in batch_delta_items_by_batch_id.items():
            if batch_id in processed_batch_ids:
                continue

            for event, deltas in delta_items:
                add_orphan_report_row(event, deltas)

        _insert_many_if_supported(self.db.report_rows, orphan_report_rows)

        break_total = self.db.break_events.count_documents(raw_author_query)
        break_count = 0

        for event in self.db.break_events.find(raw_author_query).sort("timestamp", ASCENDING):
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
            )

        if break_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding Telegram activity", 0, 0)

        meeting_total = self.db.meeting_events.count_documents(raw_author_query)
        meeting_count = 0

        for event in self.db.meeting_events.find(raw_author_query).sort("timestamp", ASCENDING):
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

        if meeting_total == 0:
            self._report_rebuild_progress(progress_callback, "Rebuilding Discord meetings", 0, 0)

        self._report_rebuild_progress(progress_callback, "Rebuilding status events", 0, 1)
        composed(self)._materialize_status_report_rows()
        self._report_rebuild_progress(progress_callback, "Rebuilding status events", 1, 1)

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

        for state in self.db.aggregate_session_state.find({}):
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
