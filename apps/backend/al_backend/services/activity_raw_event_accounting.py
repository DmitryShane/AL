from __future__ import annotations

from typing import Any

from ..activity_math import *
from ..hourly_fill_rules import empty_hourly_activity, merge_hourly_activity
from ..backend_composable_host import composed
from ..overtime_rules import (
    is_night_overtime_window,
    is_telegram_overtime_window,
    is_vacation_overtime_window,
)
from .activity_status_intervals import status_interval_context_for_event


MIDNIGHT_OFFLINE_CARRYOVER_SUPPRESSION_SECONDS = 15 * 60


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _windows_overlap(start: dt.datetime, end: dt.datetime, window_start: dt.datetime, window_end: dt.datetime) -> bool:
    return max(start, window_start) < min(end, window_end)


def _hold_duration_seconds_for_state(event: dict[str, Any]) -> float | None:
    if not is_device_source(event.get("source")) or str(event.get("eventType") or "") != "hold":
        return None

    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return _float_or_none(metadata.get("holdDurationSeconds"))


def _hold_started_at_for_state(event: dict[str, Any]) -> str | None:
    if not is_device_source(event.get("source")) or str(event.get("eventType") or "") != "hold":
        return None

    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return str(metadata.get("firstHoldAtUtc") or "") or None


def _has_presence_delta(deltas: dict[str, Any]) -> bool:
    if _has_time_delta(deltas):
        return True

    return bool(
        deltas.get("activityCountDeltas")
        or deltas.get("savedPrefabDeltas")
        or deltas.get("overtimeActivityCountDeltas")
        or deltas.get("overtimeSavedPrefabDeltas")
    )


class ActivityRawEventAccountingMixin:
    def _apply_snapshot_to_aggregates(self, snapshot: dict[str, Any]) -> None:
        snapshot = dict(snapshot)
        snapshot["author"] = composed(self).resolve_author_alias(snapshot.get("author") or "Unknown User")
        composed(self).update_author_time_zone(snapshot.get("author") or "Unknown User", snapshot.get("timeZoneId"), snapshot.get("timeZoneDisplayName"))
        session_key = _session_key(snapshot)
        previous = self.db.aggregate_session_state.find_one({"_id": session_key}) or {}
        deltas = _build_deltas(snapshot, previous.get("snapshot", {}))
        if composed(self).is_vacation_day(str(snapshot.get("author") or "Unknown User"), str(snapshot.get("date") or "")):
            deltas = composed(self).convert_deltas_to_vacation_overtime(deltas)
        materialize = self._should_materialize_aggregate_date(str(snapshot.get("date") or ""), str(snapshot.get("author") or "Unknown User"))
        if self._should_suppress_post_offline_plugin_deltas(snapshot, deltas):
            self.db.aggregate_session_state.update_one(
                {"_id": session_key},
                {
                    "$set": {
                        "author": snapshot.get("author") or "Unknown User",
                        "date": snapshot.get("date") or "",
                        "snapshot": _state_snapshot(snapshot),
                        "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC)),
                    }
                },
                upsert=True,
            )
            return

        if materialize:
            row = dict(snapshot)
            row.update(deltas)
            row["snapshotKey"] = session_key
            self.db.report_rows.insert_one(row)
            self._update_daily_author_activity(snapshot, deltas)

        received_at = _coerce_datetime(snapshot.get("receivedAt")) or dt.datetime.now(dt.UTC)
        report_time = _coerce_datetime(snapshot.get("recordedAt") or snapshot.get("lastRecordedAt") or snapshot.get("receivedAt")) or received_at
        suppress_vacation_prompt = composed(self).should_suppress_vacation_prompt(
            str(snapshot.get("author") or "Unknown User"),
            str(snapshot.get("date") or ""),
        )
        suppress_rebuild_notifications = self._notifications_suppressed_for_rebuild()
        if materialize and not suppress_rebuild_notifications and _has_active_or_overtime_delta(deltas) and not suppress_vacation_prompt:
            composed(self)._schedule_telegram_break_activity_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                report_time,
            )

        if materialize and not suppress_rebuild_notifications and _has_time_delta(deltas) and not suppress_vacation_prompt:
            composed(self)._schedule_telegram_online_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                received_at,
            )
        self.db.aggregate_session_state.update_one(
            {"_id": session_key},
            {
                "$set": {
                    "author": snapshot.get("author") or "Unknown User",
                    "date": snapshot.get("date") or "",
                    "snapshot": _state_snapshot(snapshot),
                    "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC)),
                }
            },
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
        event["author"] = composed(self).resolve_author_alias(event.get("author") or "Unknown User")
        composed(self).update_author_time_zone(event.get("author") or "Unknown User", event.get("timeZoneId"), event.get("timeZoneDisplayName"))
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
        overtime_window_kind = self._overtime_window_kind_for_event(event)
        count_idle_as_overtime = overtime_window_kind == "vacation" or (
            overtime_window_kind == "telegram" and current_source == "ual"
        )
        idle_threshold_seconds = composed(self).get_idle_threshold_for_author(
            str(event.get("author") or "Unknown User"),
            current_source,
        )
        received_at = _coerce_datetime(event.get("receivedAt"))
        status_context = self._status_interval_context_for_event(event, occurred_at, received_at)
        status_offline_at = status_context.get("offlineAt") if status_context else None
        status_online_at = status_context.get("onlineAt") if status_context else None
        is_inside_status_offline = bool(status_context and status_context.get("insideOffline"))
        status_idle_accounted_until = _coerce_datetime(author_state.get("statusIdleAccountedUntil"))
        skip_activity_interval_accounting = False

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
            interval_overtime_window = self._overtime_window_for_interval(event, status_offline_at, status_online_at)
            status_idle_deltas = _interval_deltas(
                status_offline_at,
                status_online_at,
                _to_local_datetime(status_offline_at, status_time_zone_id),
                _to_local_datetime(status_online_at, status_time_zone_id),
                False,
                consumed_normal_microseconds,
                interval_overtime_window,
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

        if is_activity and event_type == "hold":
            hold_deltas = self._device_hold_duration_deltas(
                event,
                author_state,
                consumed_normal_microseconds,
                overtime_window,
            )

            if _has_time_delta(hold_deltas):
                _merge_batch_deltas(deltas, hold_deltas)
                skip_activity_interval_accounting = True

        if is_activity:
            if not first_activity_at:
                first_activity_at = occurred_at
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source
            elif (
                not skip_activity_interval_accounting
                and last_activity_at
                and last_accounting_at
                and occurred_at > last_activity_at
            ):
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
                    interval_end_at = occurred_at
                    interval_end_local_at = occurred_local_at
                    workday_started_at = self._workday_started_at_for_event_interval(event, accounting_start_at, interval_end_at)

                    if not interval_is_active and workday_started_at and accounting_start_at < workday_started_at < interval_end_at:
                        accounting_start_at = workday_started_at
                        accounting_start_local_at = _to_local_datetime(
                            workday_started_at,
                            _valid_time_zone_id(event.get("timeZoneId")) or "UTC",
                        )

                    if not interval_is_active and count_idle_as_overtime:
                        interval_end_at = min(
                            occurred_at,
                            interval_activity_at + dt.timedelta(seconds=idle_threshold_seconds),
                        )
                        interval_end_local_at = accounting_start_local_at + (interval_end_at - accounting_start_at)
                        interval_is_active = interval_end_at > accounting_start_at

                    interval_overtime_window = self._overtime_window_for_interval(event, accounting_start_at, interval_end_at)
                    if self._has_reports_stopped_gap_overlap(event, accounting_start_at, interval_end_at):
                        interval_overtime_window = None
                    interval_deltas = _interval_deltas(
                        accounting_start_at,
                        interval_end_at,
                        accounting_start_local_at,
                        interval_end_local_at,
                        interval_is_active,
                        consumed_normal_microseconds,
                        interval_overtime_window,
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
            and not is_inside_status_offline
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
                stale_heartbeat_capped = False

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
                            stale_heartbeat_capped = True

                interval_seconds = int((heartbeat_end - last_accounting_at).total_seconds())

                if interval_seconds < MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS:
                    return deltas

                interval_end_at = heartbeat_end
                interval_end_local_at = heartbeat_local_end
                interval_is_active = False

                if count_idle_as_overtime:
                    interval_end_at = min(
                        heartbeat_end,
                        last_activity_at + dt.timedelta(seconds=idle_threshold_seconds),
                    )
                    interval_end_local_at = (last_accounting_local_at or last_accounting_at) + (
                        interval_end_at - last_accounting_at
                    )
                    interval_is_active = interval_end_at > last_accounting_at

                interval_overtime_window = self._overtime_window_for_interval(event, last_accounting_at, interval_end_at)
                if (stale_heartbeat_capped and overtime_window_kind == "night") or self._has_reports_stopped_gap_overlap(
                    event,
                    last_accounting_at,
                    interval_end_at,
                ):
                    interval_overtime_window = None
                interval_deltas = _interval_deltas(
                    last_accounting_at,
                    interval_end_at,
                    last_accounting_local_at or last_accounting_at,
                    interval_end_local_at,
                    interval_is_active,
                    consumed_normal_microseconds,
                    interval_overtime_window,
                )
                _merge_batch_deltas(deltas, interval_deltas)
                last_accounting_at = heartbeat_end
                last_accounting_local_at = heartbeat_local_end
                last_accounting_source = current_source

                if not author_last_accounting_at or heartbeat_end > author_last_accounting_at:
                    author_last_accounting_at = heartbeat_end
                    author_last_accounting_local_at = heartbeat_local_end
                    author_last_accounting_scope = current_scope

        event_has_overtime_time_delta = _time_microseconds(
            deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"
        ) > 0
        event_counts_as_overtime_breakdown = event_has_overtime_time_delta or overtime_window_kind == "night" or (
            overtime_window_kind in {"telegram", "vacation"}
            and consumed_normal_microseconds >= DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND
        )

        if is_activity:
            activity_type = _activity_count_type(event_type)
            activity_delta_key = "activityCountDeltas"

            if event_counts_as_overtime_breakdown:
                activity_delta_key = "overtimeActivityCountDeltas"

            deltas[activity_delta_key].append({"type": activity_type, "count": 1})

        saved_prefab = None if is_inside_status_offline else _saved_prefab_delta(event)

        if saved_prefab:
            saved_prefab_delta_key = "savedPrefabDeltas"

            if event_counts_as_overtime_breakdown:
                saved_prefab_delta_key = "overtimeSavedPrefabDeltas"

            deltas[saved_prefab_delta_key].append(saved_prefab)

        worked_file = None if is_inside_status_offline else _worked_file_delta(event)

        if worked_file:
            worked_file_delta_key = "savedPrefabDeltas"

            if event_counts_as_overtime_breakdown:
                worked_file_delta_key = "overtimeSavedPrefabDeltas"

            deltas[worked_file_delta_key].append(worked_file)

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
        materialize = self._should_materialize_aggregate_date(str(snapshot.get("date") or ""), str(snapshot.get("author") or "Unknown User"))

        if not suppress_deltas and materialize:
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
                    "author": event.get("author") or "Unknown User",
                    "date": event.get("date") or "",
                    "state": {
                        "firstActivityAt": first_activity_at.isoformat() if first_activity_at else None,
                        "lastActivityAt": last_activity_at.isoformat() if last_activity_at else None,
                        "lastAccountingAt": last_accounting_at.isoformat() if last_accounting_at else None,
                        "lastActivityLocalAt": last_activity_local_at.isoformat() if last_activity_local_at else None,
                        "lastAccountingLocalAt": last_accounting_local_at.isoformat() if last_accounting_local_at else None,
                        "lastActivitySource": last_activity_source or None,
                        "lastAccountingSource": last_accounting_source or None,
                        "lastHoldDurationSeconds": _hold_duration_seconds_for_state(event),
                        "lastHoldStartedAt": _hold_started_at_for_state(event),
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
                    "author": event.get("author") or "Unknown User",
                    "date": event.get("date") or "",
                    "state": {
                        "firstActivityAt": author_first_activity_at.isoformat() if author_first_activity_at else None,
                        "lastActivityAt": author_last_activity_at.isoformat() if author_last_activity_at else None,
                        "lastAccountingAt": author_last_accounting_at.isoformat() if author_last_accounting_at else None,
                        "lastActivityLocalAt": author_last_activity_local_at.isoformat() if author_last_activity_local_at else None,
                        "lastAccountingLocalAt": author_last_accounting_local_at.isoformat() if author_last_accounting_local_at else None,
                        "lastActivityScope": author_last_activity_scope or None,
                        "lastAccountingScope": author_last_accounting_scope or None,
                        "lastHoldDurationSeconds": _hold_duration_seconds_for_state(event),
                        "lastHoldStartedAt": _hold_started_at_for_state(event),
                        "statusIdleAccountedUntil": status_idle_accounted_until.isoformat() if status_idle_accounted_until else None,
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                }
            },
            upsert=True,
        )

        if (
            not suppress_deltas
            and materialize
            and overtime_window is None
            and consumed_normal_microseconds >= DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND
        ):
            returned_deltas = dict(deltas)
            returned_deltas["activeDeltaMicroseconds"] = 0
            returned_deltas["activeDeltaSeconds"] = 0
            returned_deltas["idleDeltaMicroseconds"] = 0
            returned_deltas["idleDeltaSeconds"] = 0
            returned_deltas["hourlyActivityDelta"] = empty_hourly_activity()
            return returned_deltas

        return _empty_event_deltas() if suppress_deltas else deltas

    def _device_hold_duration_deltas(
        self,
        event: dict[str, Any],
        author_state: dict[str, Any],
        consumed_normal_microseconds: int,
        overtime_window: tuple[dt.datetime, dt.datetime] | None,
    ) -> dict[str, Any]:
        deltas = _empty_event_deltas()

        if not is_device_source(event.get("source")):
            return deltas

        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        hold_duration_seconds = _float_or_none(metadata.get("holdDurationSeconds"))

        if hold_duration_seconds is None or hold_duration_seconds <= 0:
            return deltas

        previous_duration_seconds = _float_or_none(author_state.get("lastHoldDurationSeconds"))
        previous_hold_started_at = str(author_state.get("lastHoldStartedAt") or "")
        current_hold_started_at = str(metadata.get("firstHoldAtUtc") or "")

        if current_hold_started_at and current_hold_started_at != previous_hold_started_at:
            previous_duration_seconds = 0.0

        duration_delta_seconds = hold_duration_seconds - max(0.0, previous_duration_seconds or 0.0)

        if duration_delta_seconds <= 0:
            return deltas

        occurred_at = _coerce_datetime(event.get("occurredAtUtc") or event.get("occurredAt"))
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal"))

        if not occurred_at:
            return deltas

        duration_delta = dt.timedelta(seconds=duration_delta_seconds)
        interval_start_at = occurred_at - duration_delta
        interval_start_local_at = (occurred_local_at or occurred_at) - duration_delta
        interval_end_local_at = occurred_local_at or occurred_at
        hold_overtime_window = overtime_window or self._overtime_window_for_interval(event, interval_start_at, occurred_at)
        hold_deltas = _interval_deltas(
            interval_start_at,
            occurred_at,
            interval_start_local_at,
            interval_end_local_at,
            True,
            consumed_normal_microseconds,
            hold_overtime_window,
        )
        _merge_batch_deltas(deltas, hold_deltas)
        return deltas

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
        context = self._overtime_rule_context()

        vacation_window = is_vacation_overtime_window(event, context)

        if vacation_window:
            return vacation_window

        night_window = None if self._suppress_night_overtime_for_midnight_offline_carryover(event) else is_night_overtime_window(event)

        if night_window:
            return night_window

        return is_telegram_overtime_window(event, context)

    def _overtime_window_for_interval(
        self,
        event: dict[str, Any],
        start: dt.datetime,
        end: dt.datetime,
    ) -> tuple[dt.datetime, dt.datetime] | None:
        if end <= start:
            return None

        event_window = self._overtime_window_for_event(event)

        if event_window and _windows_overlap(start, end, event_window[0], event_window[1]):
            return event_window

        if self._suppress_night_overtime_for_midnight_offline_carryover(event):
            return None

        probe = dict(event)
        probe["occurredAtUtc"] = start
        interval_start_window = is_night_overtime_window(probe)

        if interval_start_window and _windows_overlap(start, end, interval_start_window[0], interval_start_window[1]):
            return interval_start_window

        return None

    def _overtime_window_kind_for_event(self, event: dict[str, Any]) -> str | None:
        context = self._overtime_rule_context()

        if is_vacation_overtime_window(event, context):
            return "vacation"
        if not self._suppress_night_overtime_for_midnight_offline_carryover(event) and is_night_overtime_window(event):
            return "night"
        if is_telegram_overtime_window(event, context):
            return "telegram"

        return None

    def _workday_started_at_for_event_interval(
        self,
        event: dict[str, Any],
        start: dt.datetime,
        end: dt.datetime,
    ) -> dt.datetime | None:
        if end <= start:
            return None

        start_probe = dict(event)
        start_probe["occurredAtUtc"] = start
        if not is_night_overtime_window(start_probe):
            return None

        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date:
            return None

        session = self.db.day_sessions.find_one(
            {"rawAuthor": raw_author, "date": day_date},
            {"_id": 0, "startedAt": 1},
        )
        started_at = _coerce_datetime((session or {}).get("startedAt"))

        if started_at and start < started_at < end:
            return started_at

        return None

    def _suppress_night_overtime_for_midnight_offline_carryover(self, event: dict[str, Any]) -> bool:
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAt"))
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not occurred_at or not raw_author or not day_date:
            return False

        latest_event: dict[str, Any] | None = None
        latest_timestamp: dt.datetime | None = None

        for current in self.db.break_events.find(
            {
                "rawAuthor": raw_author,
                "eventType": {"$in": ["online", "offline"]},
                "timestamp": {"$lte": occurred_at},
            },
            {"_id": 0, "eventType": 1, "timestamp": 1, "date": 1},
        ):
            timestamp = _coerce_datetime(current.get("timestamp"))

            if not timestamp:
                continue

            if latest_timestamp is None or timestamp > latest_timestamp:
                latest_event = current
                latest_timestamp = timestamp

        if not latest_event or not latest_timestamp:
            return False

        if str(latest_event.get("eventType") or "") != "offline" or str(latest_event.get("date") or "") == day_date:
            return False

        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
        zone = ZoneInfo(time_zone_id)
        occurred_local = occurred_at.astimezone(zone)
        latest_local = latest_timestamp.astimezone(zone)

        try:
            event_day = dt.date.fromisoformat(day_date)
        except ValueError:
            return False

        midnight_local = dt.datetime.combine(event_day, dt.time.min, zone)

        return (
            latest_local.date() == event_day - dt.timedelta(days=1)
            and latest_local <= midnight_local
            and (midnight_local - latest_local).total_seconds() <= MIDNIGHT_OFFLINE_CARRYOVER_SUPPRESSION_SECONDS
            and occurred_local.date() == event_day
        )

    def _has_reports_stopped_gap_overlap(self, event: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
        if end <= start:
            return False

        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date:
            return False

        offline_at: dt.datetime | None = None

        for status_event in sorted(
            self.db.status_events.find(
                {
                    "rawAuthor": raw_author,
                    "date": day_date,
                    "reason": {"$in": ["reports_stopped", "reports_resumed"]},
                    "transitionAt": {"$lte": end},
                },
                {"_id": 0, "statusEventType": 1, "transitionAt": 1, "reason": 1},
            ),
            key=lambda item: _coerce_datetime(item.get("transitionAt")) or dt.datetime.min.replace(tzinfo=dt.UTC),
        ):
            transition_at = _coerce_datetime(status_event.get("transitionAt"))

            if not transition_at:
                continue

            status_type = str(status_event.get("statusEventType") or "")
            reason = str(status_event.get("reason") or "")

            if status_type == "offline" and reason == "reports_stopped":
                offline_at = transition_at
                continue

            if status_type != "online" or reason != "reports_resumed" or not offline_at:
                continue

            if _windows_overlap(start, end, offline_at, transition_at):
                return True

            offline_at = None

        return bool(offline_at and end > offline_at)

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

        status_events = list(
            self.db.status_events.find(
                {"rawAuthor": raw_author, "date": day_date},
                {"_id": 0, "statusEventType": 1, "transitionAt": 1, "timeZoneId": 1, "reason": 1},
            )
        )
        return status_interval_context_for_event(status_events, occurred_at, received_at)

    def _update_daily_author_activity(self, snapshot: dict[str, Any], deltas: dict[str, Any]) -> None:
        key = {
            "source": snapshot.get("source"),
            "author": snapshot.get("author") or "Unknown User",
            "projectId": snapshot.get("projectId") or "",
            "date": snapshot.get("date") or "",
        }
        current = self.db.daily_author_activity.find_one(key, {"_id": 0}) or {}
        hourly_activity = current.get("hourlyActivity") or empty_hourly_activity()
        merge_hourly_activity(hourly_activity, deltas.get("hourlyActivityDelta", []))
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
        overtime_active_microseconds = _time_microseconds(
            current, "overtimeActiveSeconds", "overtimeActiveMicroseconds"
        ) + _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds")
        set_fields = {
            **key,
            "authorEmail": snapshot.get("authorEmail", ""),
            "pluginVersion": snapshot.get("pluginVersion"),
            "timeZoneId": snapshot.get("timeZoneId"),
            "timeZoneDisplayName": snapshot.get("timeZoneDisplayName"),
            "workWindowSeconds": snapshot.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
            "activityCounts": activity_counts,
            "savedPrefabs": saved_prefabs,
            "overtimeActivityCounts": overtime_activity_counts,
            "overtimeSavedPrefabs": overtime_saved_prefabs,
            "hourlyActivity": hourly_activity,
            "activeMicroseconds": active_microseconds,
            "idleMicroseconds": idle_microseconds,
            "breakSeconds": break_seconds,
            "overtimeActiveMicroseconds": overtime_active_microseconds,
            "activeSeconds": _seconds_from_microseconds(active_microseconds),
            "idleSeconds": _seconds_from_microseconds(idle_microseconds),
            "overtimeActiveSeconds": _seconds_from_microseconds(overtime_active_microseconds),
        }

        if _has_presence_delta(deltas):
            set_fields["lastRecordedAt"] = snapshot.get("recordedAt")
            set_fields["lastReceivedAt"] = snapshot.get("receivedAt")

        self.db.daily_author_activity.update_one(
            key,
            {"$set": set_fields},
            upsert=True,
        )
