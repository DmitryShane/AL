from __future__ import annotations

from ..activity_math import *


class ActivityAggregationService:
    def rebuild_aggregates_if_needed(self, force: bool = False) -> None:
        metadata = self.db.aggregate_metadata.find_one({"kind": "activity"})

        if not force and metadata and metadata.get("version") == self.aggregates_version:
            return

        self._daily_consumed_microseconds_cache = {}
        self.db.report_rows.delete_many({})
        self.db.daily_author_activity.delete_many({})
        self.db.aggregate_session_state.delete_many({})

        snapshots = self.db.activity_snapshots.find({}).sort("receivedAt", ASCENDING)

        for snapshot in snapshots:
            self._apply_snapshot_to_aggregates(snapshot)

        batch_ids = {
            str(batch.get("batchId") or "")
            for batch in self.db.raw_event_batches.find({}, {"_id": 0, "batchId": 1})
            if batch.get("batchId")
        }
        batch_delta_items_by_batch_id: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        last_event_by_batch_id: dict[str, dict[str, Any]] = {}
        orphan_report_rows: list[dict[str, Any]] = []
        raw_events = self.db.raw_activity_events.find({}).sort("occurredAtUtc", ASCENDING)

        for event in raw_events:
            deltas = self._apply_raw_event_to_aggregates(event)
            batch_id = str(event.get("batchId") or "")

            if batch_id and batch_id in batch_ids:
                batch_delta_items = batch_delta_items_by_batch_id.setdefault(batch_id, [])
                batch_delta_items.append((event, deltas))
                last_event_by_batch_id[batch_id] = event
                continue

            if not _has_time_delta(deltas):
                continue

            resolved_author = self.resolve_author_alias(event.get("author") or "Unknown User")
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
                    **deltas,
                }
            )

        last_report_time_by_author: dict[str, dt.datetime] = {}

        for batch in self.db.raw_event_batches.find({}).sort("receivedAt", ASCENDING):
            batch_id = str(batch.get("batchId") or "")
            author = str(batch.get("author") or "Unknown User")
            cutoff = last_report_time_by_author.get(author)
            rows = self._build_event_batch_report_rows(batch, batch_delta_items_by_batch_id.get(batch_id, []), cutoff)
            _insert_many_if_supported(self.db.report_rows, rows)

            for row in rows:
                row_time = _report_row_time(row)

                if row_time:
                    last_report_time_by_author[author] = row_time

        _insert_many_if_supported(self.db.report_rows, orphan_report_rows)

        for event in self.db.break_events.find({}).sort("timestamp", ASCENDING):
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            self._insert_telegram_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("telegramUsername") or ""),
                str(event.get("eventType") or "telegram"),
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("eventType") or "telegram"),
            )

        for event in self.db.meeting_events.find({}).sort("timestamp", ASCENDING):
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            self._insert_discord_meeting_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("discordUserId") or ""),
                str(event.get("discordUsername") or ""),
                str(event.get("eventType") or "reconcile"),
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("eventType") or "meeting"),
                str(event.get("guildId") or ""),
                str(event.get("channelId") or ""),
            )

        self._materialize_status_report_rows()

        self.db.aggregate_metadata.update_one(
            {"kind": "activity"},
            {"$set": {"kind": "activity", "version": self.aggregates_version, "rebuiltAt": dt.datetime.now(dt.UTC)}},
            upsert=True,
        )
        self._daily_consumed_microseconds_cache = None

    def _apply_snapshot_to_aggregates(self, snapshot: dict[str, Any]) -> None:
        snapshot = dict(snapshot)
        snapshot["author"] = self.resolve_author_alias(snapshot.get("author") or "Unknown User")
        self.update_author_time_zone(snapshot.get("author") or "Unknown User", snapshot.get("timeZoneId"), snapshot.get("timeZoneDisplayName"))
        session_key = _session_key(snapshot)
        previous = self.db.aggregate_session_state.find_one({"_id": session_key}) or {}
        deltas = _build_deltas(snapshot, previous.get("snapshot", {}))
        if self._should_suppress_post_offline_plugin_deltas(snapshot, deltas):
            self.db.aggregate_session_state.update_one(
                {"_id": session_key},
                {"$set": {"snapshot": _state_snapshot(snapshot), "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC))}},
                upsert=True,
            )
            return

        row = dict(snapshot)
        row.update(deltas)
        row["snapshotKey"] = session_key
        self.db.report_rows.insert_one(row)
        self._update_daily_author_activity(snapshot, deltas)
        received_at = _coerce_datetime(snapshot.get("receivedAt")) or dt.datetime.now(dt.UTC)
        report_time = _coerce_datetime(snapshot.get("recordedAt") or snapshot.get("lastRecordedAt") or snapshot.get("receivedAt")) or received_at
        if _has_active_or_overtime_delta(deltas):
            self._schedule_telegram_break_activity_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                report_time,
            )

        if _has_time_delta(deltas):
            self._schedule_telegram_online_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                received_at,
            )
        self.db.aggregate_session_state.update_one(
            {"_id": session_key},
            {"$set": {"snapshot": _state_snapshot(snapshot), "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC))}},
            upsert=True,
        )

    def _should_suppress_post_offline_plugin_deltas(self, item: dict[str, Any], deltas: dict[str, Any]) -> bool:
        if item.get("source") in {"telegram", "discord"} or item.get("reportType") in {"telegram", "meeting"}:
            return False

        if not _has_time_delta(deltas):
            return False

        if _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0:
            return False

        if deltas.get("overtimeActivityCountDeltas") or deltas.get("overtimeSavedPrefabDeltas"):
            return False

        received_at = _coerce_datetime(item.get("receivedAt") or item.get("lastReceivedAt"))

        if not received_at:
            return False

        return self._is_author_offline_after_latest_telegram_state(
            str(item.get("author") or "Unknown User"),
            str(item.get("date") or ""),
            received_at,
        )

    def _is_author_offline_after_latest_telegram_state(self, raw_author: str, day_date: str, at: dt.datetime) -> bool:
        latest_event_type = ""
        latest_timestamp: dt.datetime | None = None

        for event in self.db.break_events.find(
            {
                "rawAuthor": raw_author,
                "date": day_date,
                "eventType": {"$in": ["online", "offline"]},
                "timestamp": {"$lte": at},
            },
            {"_id": 0, "eventType": 1, "timestamp": 1},
        ):
            timestamp = _coerce_datetime(event.get("timestamp"))

            if not timestamp:
                continue

            if not latest_timestamp or timestamp > latest_timestamp:
                latest_timestamp = timestamp
                latest_event_type = str(event.get("eventType") or "")

        return latest_event_type == "offline"

    def _apply_raw_event_to_aggregates(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        event["author"] = self.resolve_author_alias(event.get("author") or "Unknown User")
        self.update_author_time_zone(event.get("author") or "Unknown User", event.get("timeZoneId"), event.get("timeZoneDisplayName"))
        state_key = _raw_event_session_key(event)
        previous = self.db.aggregate_session_state.find_one({"_id": state_key}) or {}
        state = dict(previous.get("state", {}))
        author_state_key = _raw_event_author_day_key(event)
        author_previous = self.db.aggregate_session_state.find_one({"_id": author_state_key}) or {}
        author_state = dict(author_previous.get("state", {}))
        event_type = str(event.get("eventType") or "")
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at
        first_activity_at = _coerce_datetime(state.get("firstActivityAt"))
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        last_activity_local_at = _parse_local_datetime(state.get("lastActivityLocalAt"))
        last_accounting_local_at = _parse_local_datetime(state.get("lastAccountingLocalAt"))
        last_activity_source = str(state.get("lastActivitySource") or "")
        last_accounting_source = str(state.get("lastAccountingSource") or "")
        source_is_focused = state.get("isFocused")
        current_source = str(event.get("source") or "")
        current_scope = _raw_event_activity_scope(event)
        author_first_activity_at = _coerce_datetime(author_state.get("firstActivityAt"))
        author_last_activity_at = _coerce_datetime(author_state.get("lastActivityAt"))
        author_last_accounting_at = _coerce_datetime(author_state.get("lastAccountingAt"))
        author_last_activity_local_at = _parse_local_datetime(author_state.get("lastActivityLocalAt"))
        author_last_accounting_local_at = _parse_local_datetime(author_state.get("lastAccountingLocalAt"))
        author_last_activity_scope = str(author_state.get("lastActivityScope") or "")
        author_last_accounting_scope = str(author_state.get("lastAccountingScope") or "")
        deltas = _empty_event_deltas()
        raw_is_activity = _is_activity_event(event)
        is_activity = raw_is_activity and (source_is_focused is not False or event_type == "focus")
        consumed_normal_microseconds = self._normal_microseconds_consumed_for_event(event)
        overtime_window = self._overtime_window_for_event(event)
        idle_threshold_seconds = self.get_idle_threshold_for_author(str(event.get("author") or "Unknown User"))
        received_at = _coerce_datetime(event.get("receivedAt"))
        status_context = self._status_interval_context_for_event(event, occurred_at, received_at)
        status_offline_at = status_context.get("offlineAt") if status_context else None
        status_online_at = status_context.get("onlineAt") if status_context else None
        is_inside_status_offline = bool(status_context and status_context.get("insideOffline"))
        status_idle_accounted_until = _coerce_datetime(author_state.get("statusIdleAccountedUntil"))

        if event_type == "focus":
            source_is_focused = True
        elif event_type == "blur":
            source_is_focused = False

        if (
            status_offline_at
            and status_online_at
            and received_at
            and received_at >= status_online_at
            and (not status_idle_accounted_until or status_idle_accounted_until < status_online_at)
        ):
            status_time_zone_id = (
                _valid_time_zone_id((status_context or {}).get("timeZoneId"))
                or _valid_time_zone_id(event.get("timeZoneId"))
                or "UTC"
            )
            status_idle_deltas = _interval_deltas(
                status_offline_at,
                status_online_at,
                _to_local_datetime(status_offline_at, status_time_zone_id),
                _to_local_datetime(status_online_at, status_time_zone_id),
                False,
                consumed_normal_microseconds,
                overtime_window,
            )
            _merge_batch_deltas(deltas, status_idle_deltas)
            last_accounting_at = status_online_at
            last_accounting_local_at = _to_local_datetime(status_online_at, status_time_zone_id)
            last_accounting_source = current_source
            author_last_accounting_at = status_online_at
            author_last_accounting_local_at = _to_local_datetime(status_online_at, status_time_zone_id)
            author_last_accounting_scope = current_scope
            status_idle_accounted_until = status_online_at

        if is_inside_status_offline:
            is_activity = False

        if is_activity:
            if not first_activity_at:
                first_activity_at = occurred_at
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source
            elif last_activity_at and last_accounting_at and occurred_at > last_activity_at:
                accounting_start_at = last_accounting_at
                accounting_start_local_at = last_accounting_local_at or last_accounting_at

                if author_last_accounting_at and author_last_accounting_at > accounting_start_at:
                    accounting_start_at = author_last_accounting_at
                    accounting_start_local_at = author_last_accounting_local_at or author_last_accounting_at

                interval_activity_at = last_activity_at

                if author_last_activity_at and author_last_activity_at > interval_activity_at:
                    interval_activity_at = author_last_activity_at

                if accounting_start_at < occurred_at:
                    interval_is_active = (occurred_at - interval_activity_at).total_seconds() < idle_threshold_seconds
                    interval_deltas = _interval_deltas(
                        accounting_start_at,
                        occurred_at,
                        accounting_start_local_at,
                        occurred_local_at,
                        interval_is_active,
                        consumed_normal_microseconds,
                        overtime_window,
                    )
                    _merge_batch_deltas(deltas, interval_deltas)

                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source

            if not last_activity_at or occurred_at > last_activity_at:
                last_activity_at = occurred_at
                last_activity_local_at = occurred_local_at
                last_activity_source = current_source

            if not author_first_activity_at:
                author_first_activity_at = occurred_at

            if not author_last_accounting_at or occurred_at > author_last_accounting_at:
                author_last_accounting_at = occurred_at
                author_last_accounting_local_at = occurred_local_at
                author_last_accounting_scope = current_scope

            if not author_last_activity_at or occurred_at > author_last_activity_at:
                author_last_activity_at = occurred_at
                author_last_activity_local_at = occurred_local_at
                author_last_activity_scope = current_scope
        elif (
            event_type == "heartbeat"
            and first_activity_at
            and last_activity_at
            and last_accounting_at
            and occurred_at > last_accounting_at
            and (not last_activity_source or current_source == last_activity_source)
            and (not author_last_activity_scope or current_scope == author_last_activity_scope)
        ):
            if (occurred_at - last_activity_at).total_seconds() >= idle_threshold_seconds:
                heartbeat_end = occurred_at
                heartbeat_local_end = occurred_local_at

                received_at = _coerce_datetime(event.get("receivedAt"))
                skew_floor = idle_threshold_seconds * STALE_HEARTBEAT_RECEIVE_SKEW_MULTIPLIER

                if skew_floor < STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR:
                    skew_floor = STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR

                if (
                    received_at is not None
                    and received_at > occurred_at
                    and (received_at - occurred_at).total_seconds() >= skew_floor
                ):
                    max_accounting_seconds = idle_threshold_seconds * MAX_STALE_HEARTBEAT_IDLE_MULTIPLIER

                    if max_accounting_seconds > 0:
                        capped_end = last_accounting_at + dt.timedelta(seconds=max_accounting_seconds)

                        if heartbeat_end > capped_end:
                            heartbeat_end = capped_end
                            heartbeat_local_end = (
                                last_accounting_local_at or last_accounting_at
                            ) + dt.timedelta(seconds=max_accounting_seconds)

                interval_seconds = int((heartbeat_end - last_accounting_at).total_seconds())

                if interval_seconds < MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS:
                    return deltas

                interval_deltas = _interval_deltas(
                    last_accounting_at,
                    heartbeat_end,
                    last_accounting_local_at or last_accounting_at,
                    heartbeat_local_end,
                    False,
                    consumed_normal_microseconds,
                    overtime_window,
                )
                _merge_batch_deltas(deltas, interval_deltas)
                last_accounting_at = heartbeat_end
                last_accounting_local_at = heartbeat_local_end
                last_accounting_source = current_source

                if not author_last_accounting_at or heartbeat_end > author_last_accounting_at:
                    author_last_accounting_at = heartbeat_end
                    author_last_accounting_local_at = heartbeat_local_end
                    author_last_accounting_scope = current_scope

        if is_activity:
            activity_type = _activity_count_type(event_type)
            activity_delta_key = "activityCountDeltas"

            if _is_overtime_event_delta(consumed_normal_microseconds, deltas, overtime_window):
                activity_delta_key = "overtimeActivityCountDeltas"

            deltas[activity_delta_key].append({"type": activity_type, "count": 1})

        saved_prefab = None if is_inside_status_offline else _saved_prefab_delta(event)

        if saved_prefab:
            saved_prefab_delta_key = "savedPrefabDeltas"

            if _is_overtime_event_delta(consumed_normal_microseconds, deltas, overtime_window):
                saved_prefab_delta_key = "overtimeSavedPrefabDeltas"

            deltas[saved_prefab_delta_key].append(saved_prefab)

        snapshot = {
            "source": event.get("source"),
            "author": event.get("author") or "Unknown User",
            "authorEmail": event.get("authorEmail", ""),
            "pluginVersion": event.get("pluginVersion"),
            "projectId": event.get("projectId") or "",
            "sessionId": event.get("sessionId") or "",
            "deviceId": event.get("deviceId") or "",
            "date": event.get("date") or occurred_at.date().isoformat(),
            "receivedAt": event.get("receivedAt"),
            "recordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
            "timeZoneId": event.get("timeZoneId"),
            "timeZoneDisplayName": event.get("timeZoneDisplayName"),
            "workWindowSeconds": DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
        }
        suppress_deltas = self._should_suppress_post_offline_plugin_deltas(event, deltas)

        if not suppress_deltas:
            self._update_daily_author_activity(snapshot, deltas)
            cache = getattr(self, "_daily_consumed_microseconds_cache", None)

            if cache is not None:
                cache_key = (str(snapshot.get("author") or "Unknown User"), str(snapshot.get("date") or ""))
                consumed_microseconds = cache.get(cache_key)

                if consumed_microseconds is not None:
                    consumed_microseconds += _time_microseconds(deltas, "activeDeltaSeconds", "activeDeltaMicroseconds")
                    consumed_microseconds += _time_microseconds(deltas, "idleDeltaSeconds", "idleDeltaMicroseconds")
                    cache[cache_key] = min(
                        DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND,
                        max(0, consumed_microseconds),
                    )

        self.db.aggregate_session_state.update_one(
            {"_id": state_key},
            {
                "$set": {
                    "state": {
                        "firstActivityAt": first_activity_at.isoformat() if first_activity_at else None,
                        "lastActivityAt": last_activity_at.isoformat() if last_activity_at else None,
                        "lastAccountingAt": last_accounting_at.isoformat() if last_accounting_at else None,
                        "lastActivityLocalAt": last_activity_local_at.isoformat() if last_activity_local_at else None,
                        "lastAccountingLocalAt": last_accounting_local_at.isoformat() if last_accounting_local_at else None,
                        "lastActivitySource": last_activity_source or None,
                        "lastAccountingSource": last_accounting_source or None,
                        "isFocused": source_is_focused,
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                }
            },
            upsert=True,
        )
        self.db.aggregate_session_state.update_one(
            {"_id": author_state_key},
            {
                "$set": {
                    "state": {
                        "firstActivityAt": author_first_activity_at.isoformat() if author_first_activity_at else None,
                        "lastActivityAt": author_last_activity_at.isoformat() if author_last_activity_at else None,
                        "lastAccountingAt": author_last_accounting_at.isoformat() if author_last_accounting_at else None,
                        "lastActivityLocalAt": author_last_activity_local_at.isoformat() if author_last_activity_local_at else None,
                        "lastAccountingLocalAt": author_last_accounting_local_at.isoformat() if author_last_accounting_local_at else None,
                        "lastActivityScope": author_last_activity_scope or None,
                        "lastAccountingScope": author_last_accounting_scope or None,
                        "statusIdleAccountedUntil": status_idle_accounted_until.isoformat() if status_idle_accounted_until else None,
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                }
            },
            upsert=True,
        )

        if (
            not suppress_deltas
            and overtime_window is None
            and consumed_normal_microseconds >= DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND
        ):
            returned_deltas = dict(deltas)
            returned_deltas["activeDeltaMicroseconds"] = 0
            returned_deltas["activeDeltaSeconds"] = 0
            returned_deltas["idleDeltaMicroseconds"] = 0
            returned_deltas["idleDeltaSeconds"] = 0
            returned_deltas["hourlyActivityDelta"] = _empty_hourly_activity()
            return returned_deltas

        return _empty_event_deltas() if suppress_deltas else deltas

    def _normal_microseconds_consumed_for_event(self, event: dict[str, Any]) -> int:
        work_window_microseconds = DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND

        if self._overtime_window_for_event(event):
            return work_window_microseconds

        cache = getattr(self, "_daily_consumed_microseconds_cache", None)
        cache_key = (str(event.get("author") or "Unknown User"), str(event.get("date") or ""))

        if cache is not None and cache_key in cache:
            return cache[cache_key]

        consumed_microseconds = 0

        for current in self.db.daily_author_activity.find(
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
            },
            {"_id": 0, "activeSeconds": 1, "idleSeconds": 1, "activeMicroseconds": 1, "idleMicroseconds": 1},
        ):
            consumed_microseconds += _time_microseconds(current, "activeSeconds", "activeMicroseconds")
            consumed_microseconds += _time_microseconds(current, "idleSeconds", "idleMicroseconds")

        consumed_microseconds = min(work_window_microseconds, max(0, consumed_microseconds))

        if cache is not None:
            cache[cache_key] = consumed_microseconds

        return consumed_microseconds

    def _overtime_window_for_event(self, event: dict[str, Any]) -> tuple[dt.datetime, dt.datetime] | None:
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")
        event_time = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAt"))

        if not raw_author or not day_date or not event_time:
            return None

        if not self._is_author_offline_after_latest_telegram_state(raw_author, day_date, event_time):
            return None

        day_session = self.db.day_sessions.find_one(
            {"rawAuthor": raw_author, "date": day_date},
            {"_id": 0, "lastOfflineAt": 1, "timeZoneId": 1},
        )
        overtime_started_at = _coerce_datetime((day_session or {}).get("lastOfflineAt"))

        if not overtime_started_at:
            return None

        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or _valid_time_zone_id((day_session or {}).get("timeZoneId")) or "UTC"

        try:
            day = dt.date.fromisoformat(day_date)
            day_end_local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, ZoneInfo(time_zone_id))
        except ValueError:
            return None

        day_end_at = day_end_local.astimezone(dt.UTC)

        if event_time < overtime_started_at or event_time >= day_end_at:
            return None

        return overtime_started_at, day_end_at

    def _status_interval_context_for_event(
        self,
        event: dict[str, Any],
        occurred_at: dt.datetime,
        received_at: dt.datetime | None,
    ) -> dict[str, Any] | None:
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date or not occurred_at:
            return None

        status_events = sorted(
            self.db.status_events.find(
                {"rawAuthor": raw_author, "date": day_date},
                {"_id": 0, "statusEventType": 1, "transitionAt": 1, "timeZoneId": 1},
            ),
            key=lambda item: _coerce_datetime(item.get("transitionAt")) or dt.datetime.min.replace(tzinfo=dt.UTC),
        )
        previous_closed_interval: dict[str, Any] | None = None
        index = 0

        while index < len(status_events):
            status_event = status_events[index]
            transition_at = _coerce_datetime(status_event.get("transitionAt"))

            if not transition_at or str(status_event.get("statusEventType") or "") != "offline":
                index += 1
                continue

            online_event: dict[str, Any] | None = None
            next_index = index + 1

            while next_index < len(status_events):
                next_event = status_events[next_index]
                next_transition_at = _coerce_datetime(next_event.get("transitionAt"))
                next_type = str(next_event.get("statusEventType") or "")

                if next_transition_at and next_transition_at > transition_at and next_type == "online":
                    online_event = next_event
                    break

                if next_transition_at and next_transition_at > transition_at and next_type == "offline":
                    break

                next_index += 1

            online_at = _coerce_datetime((online_event or {}).get("transitionAt"))
            inside_offline = occurred_at > transition_at and (not online_at or occurred_at < online_at)

            if inside_offline:
                return {
                    "offlineAt": transition_at,
                    "onlineAt": online_at,
                    "insideOffline": True,
                    "timeZoneId": online_event.get("timeZoneId") if online_event else status_event.get("timeZoneId"),
                }

            if online_at and occurred_at >= online_at:
                previous_closed_interval = {
                    "offlineAt": transition_at,
                    "onlineAt": online_at,
                    "insideOffline": False,
                    "timeZoneId": online_event.get("timeZoneId") if online_event else status_event.get("timeZoneId"),
                }

            index += 1

        if previous_closed_interval and received_at and received_at >= previous_closed_interval["onlineAt"]:
            return previous_closed_interval

        return None

    def _update_daily_author_activity(self, snapshot: dict[str, Any], deltas: dict[str, Any]) -> None:
        key = {
            "source": snapshot.get("source"),
            "author": snapshot.get("author") or "Unknown User",
            "projectId": snapshot.get("projectId") or "",
            "date": snapshot.get("date") or "",
        }
        self._apply_auto_break_to_deltas(key["author"], key["date"], deltas)
        current = self.db.daily_author_activity.find_one(key, {"_id": 0}) or {}
        hourly_activity = current.get("hourlyActivity") or _empty_hourly_activity()
        _merge_hourly_activity(hourly_activity, deltas.get("hourlyActivityDelta", []))
        activity_counts = _merge_count_list(current.get("activityCounts", []), deltas.get("activityCountDeltas", []), "type", "count")
        saved_prefabs = _merge_count_list(current.get("savedPrefabs", []), deltas.get("savedPrefabDeltas", []), "path", "saveCount")
        overtime_activity_counts = _merge_count_list(
            current.get("overtimeActivityCounts", []), deltas.get("overtimeActivityCountDeltas", []), "type", "count"
        )
        overtime_saved_prefabs = _merge_count_list(
            current.get("overtimeSavedPrefabs", []), deltas.get("overtimeSavedPrefabDeltas", []), "path", "saveCount"
        )
        active_microseconds = _time_microseconds(current, "activeSeconds", "activeMicroseconds") + _time_microseconds(
            deltas, "activeDeltaSeconds", "activeDeltaMicroseconds"
        )
        idle_microseconds = _time_microseconds(current, "idleSeconds", "idleMicroseconds") + _time_microseconds(
            deltas, "idleDeltaSeconds", "idleDeltaMicroseconds"
        )
        break_seconds = int(current.get("breakSeconds", 0)) + int(deltas.get("breakDeltaSeconds", 0))
        auto_break_seconds = int(current.get("autoBreakSeconds", 0)) + int(deltas.get("autoBreakDeltaSeconds", 0))
        overtime_active_microseconds = _time_microseconds(
            current, "overtimeActiveSeconds", "overtimeActiveMicroseconds"
        ) + _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds")

        self.db.daily_author_activity.update_one(
            key,
            {
                "$set": {
                    **key,
                    "authorEmail": snapshot.get("authorEmail", ""),
                    "pluginVersion": snapshot.get("pluginVersion"),
                    "timeZoneId": snapshot.get("timeZoneId"),
                    "timeZoneDisplayName": snapshot.get("timeZoneDisplayName"),
                    "workWindowSeconds": snapshot.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
                    "lastRecordedAt": snapshot.get("recordedAt"),
                    "lastReceivedAt": snapshot.get("receivedAt"),
                    "activityCounts": activity_counts,
                    "savedPrefabs": saved_prefabs,
                    "overtimeActivityCounts": overtime_activity_counts,
                    "overtimeSavedPrefabs": overtime_saved_prefabs,
                    "hourlyActivity": hourly_activity,
                    "activeMicroseconds": active_microseconds,
                    "idleMicroseconds": idle_microseconds,
                    "breakSeconds": break_seconds,
                    "autoBreakSeconds": auto_break_seconds,
                    "overtimeActiveMicroseconds": overtime_active_microseconds,
                    "activeSeconds": _seconds_from_microseconds(active_microseconds),
                    "idleSeconds": _seconds_from_microseconds(idle_microseconds),
                    "overtimeActiveSeconds": _seconds_from_microseconds(overtime_active_microseconds),
                },
            },
            upsert=True,
        )

    def _apply_auto_break_to_deltas(self, raw_author: str, day_date: str, deltas: dict[str, Any]) -> None:
        if not raw_author or not day_date:
            return

        profile = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author}, {"_id": 0, "autoBreakEnabled": 1, "autoBreakEffectiveDate": 1}
        ) or {}

        if not profile.get("autoBreakEnabled"):
            return

        effective_date = str(profile.get("autoBreakEffectiveDate") or "")

        if not effective_date or day_date < effective_date:
            return

        idle_microseconds = _time_microseconds(deltas, "idleDeltaSeconds", "idleDeltaMicroseconds")

        if idle_microseconds <= 0:
            return

        existing_break_seconds = 0

        for item in self.db.daily_author_activity.find({"author": raw_author, "date": day_date}, {"_id": 0, "breakSeconds": 1}):
            existing_break_seconds += int(item.get("breakSeconds", 0))

        remaining_seconds = max(0, AUTO_BREAK_SECONDS - existing_break_seconds)

        if remaining_seconds <= 0:
            return

        transfer_microseconds = min(idle_microseconds, remaining_seconds * MICROSECONDS_PER_SECOND)
        transfer_seconds = _seconds_from_microseconds(transfer_microseconds)

        if transfer_seconds <= 0:
            return

        deltas["idleDeltaMicroseconds"] = max(0, idle_microseconds - transfer_microseconds)
        deltas["idleDeltaSeconds"] = _seconds_from_microseconds(deltas["idleDeltaMicroseconds"])
        deltas["breakDeltaMicroseconds"] = _time_microseconds(
            deltas, "breakDeltaSeconds", "breakDeltaMicroseconds"
        ) + transfer_microseconds
        deltas["breakDeltaSeconds"] = _seconds_from_microseconds(deltas["breakDeltaMicroseconds"])
        deltas["autoBreakDeltaSeconds"] = int(deltas.get("autoBreakDeltaSeconds", 0)) + transfer_seconds
        _move_hourly_idle_to_break(deltas.get("hourlyActivityDelta", []), transfer_seconds)



