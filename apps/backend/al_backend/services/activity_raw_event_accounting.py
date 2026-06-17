from __future__ import annotations

import time
from typing import Any

from pymongo import UpdateOne

from ..activity_math import *
from ..hourly_fill_rules import empty_hourly_activity, merge_hourly_activity
from ..backend_composable_host import composed
from ..overtime_rules import (
    NIGHT_OVERTIME_END_HOUR,
    NIGHT_OVERTIME_START_HOUR,
    is_night_overtime_window,
    is_telegram_overtime_window,
    is_vacation_overtime_window,
)
from .activity_status_intervals import status_interval_context_for_event
from .raw_event_batching import (
    REBUILD_EXECUTION_PLAN_ENABLED,
    REBUILD_FAST_ACCOUNTING_ENABLED,
    REBUILD_INTERVAL_ACCUMULATOR_ENABLED,
)


MIDNIGHT_OFFLINE_CARRYOVER_SUPPRESSION_SECONDS = 15 * 60


def _add_raw_timing(context: dict[str, Any] | None, key: str, elapsed: float) -> None:
    if not context:
        return

    timings = context.setdefault("rawTiming", {})
    timings[key] = float(timings.get(key) or 0.0) + max(0.0, elapsed)


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


def _scene_navigation_duration_seconds_for_state(event: dict[str, Any]) -> float | None:
    if str(event.get("eventType") or "") != "scene_view_navigation":
        return None

    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return _float_or_none(metadata.get("navigationDurationSeconds"))


def _scene_navigation_started_at_for_state(event: dict[str, Any]) -> str | None:
    if str(event.get("eventType") or "") != "scene_view_navigation":
        return None

    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return str(metadata.get("firstNavigationAtUtc") or "") or None


def _has_presence_delta(deltas: dict[str, Any]) -> bool:
    if _has_time_delta(deltas):
        return True

    return bool(
        deltas.get("activityCountDeltas")
        or deltas.get("savedPrefabDeltas")
        or deltas.get("overtimeActivityCountDeltas")
        or deltas.get("overtimeSavedPrefabDeltas")
    )


def _is_unity_saved_file_event(event: dict[str, Any]) -> bool:
    return str(event.get("source") or "") == "ual" and str(event.get("eventType") or "") in {
        "asset_saved",
        "prefab_saved",
        "scene_saved",
        "scene_touched",
    }


def _codex_activity_count_type(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    metadata_activity_type = str(metadata.get("activityType") or "").strip()

    if metadata_activity_type.startswith("codex_"):
        return metadata_activity_type

    codex_event_type = str(metadata.get("codexEventType") or event.get("eventType") or "").strip()
    labels = {
        "session_started": "codex_session_started",
        "task_progress": "codex_task_progress",
        "command_run": "codex_command_run",
        "file_changed": "codex_file_changed",
        "session_finished": "codex_session_finished",
    }
    return labels.get(codex_event_type, "codex_activity")


def _raw_event_activity_count(event: dict[str, Any]) -> int:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}

    try:
        return max(1, int(metadata.get("coalescedEventCount") or 1))
    except (TypeError, ValueError):
        return 1


def _raw_event_fast_cache_key(event: dict[str, Any]) -> str:
    return str(event.get("eventId") or event.get("_id") or id(event))


class ActivityRawEventAccountingMixin:
    def _begin_raw_event_batch_accounting(self, events: list[dict[str, Any]]) -> None:
        started_at = time.perf_counter()
        state_keys = sorted(
            {
                key
                for event in events
                for key in (_raw_event_session_key(event), _raw_event_author_day_key(event))
                if str(key or "").strip()
            }
        )
        states = {
            str(item.get("_id")): item
            for item in self.db.aggregate_session_state.find({"_id": {"$in": state_keys}})
        }
        self._raw_event_batch_accounting = {
            "states": states,
            "dirtyStates": set(),
            "daily": {},
            "dirtyDaily": set(),
            "daySessions": {},
            "statusEvents": {},
            "reportsStoppedEvents": {},
            "breakOnlineOfflineEvents": {},
            "dailyOvertimeRows": {},
            "vacationMarks": {},
            "overtimeDaySessions": {},
            "authorTimeZones": set(),
            "fastAccountingEnabled": REBUILD_FAST_ACCOUNTING_ENABLED,
            "intervalAccumulatorEnabled": REBUILD_INTERVAL_ACCUMULATOR_ENABLED,
            "executionPlanEnabled": REBUILD_EXECUTION_PLAN_ENABLED,
            "executionPlans": {},
            "authorAliases": {},
            "eventDerived": {},
            "pendingDailyDeltas": {},
            "pendingDailyOrder": [],
            "rawTiming": {},
            "rawFlushCount": 0,
            "rawStateWrites": 0,
            "rawDailyWrites": 0,
            "rawDailyMergeFlushSeconds": 0.0,
            "rawAccumulatorFlushSeconds": 0.0,
            "rawAccumulatorDailyWrites": 0,
            "rawAccumulatorStateWrites": 0,
            "executionPlanBuildSeconds": 0.0,
            "hotLoopHelperCallsByName": {},
            "precomputedIntervalChecksByType": {},
        }
        _add_raw_timing(self._raw_event_batch_accounting, "stateLoad", time.perf_counter() - started_at)

    def _finish_raw_event_batch_accounting(self) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return

        try:
            self._flush_pending_daily_author_activity_deltas(context)
            state_started_at = time.perf_counter()
            state_ops = []
            for state_key in sorted(context.get("dirtyStates") or []):
                doc = dict((context.get("states") or {}).get(state_key) or {})
                if not doc:
                    continue
                state_ops.append(
                    UpdateOne(
                        {"_id": state_key},
                        {"$set": {key: value for key, value in doc.items() if key != "_id"}},
                        upsert=True,
                    )
                )

            if state_ops:
                self.db.aggregate_session_state.bulk_write(state_ops, ordered=False)

            context["rawStateWrites"] = int(context.get("rawStateWrites") or 0) + len(state_ops)
            if context.get("intervalAccumulatorEnabled"):
                context["rawAccumulatorStateWrites"] = int(context.get("rawAccumulatorStateWrites") or 0) + len(state_ops)
            _add_raw_timing(context, "stateFlush", time.perf_counter() - state_started_at)
            daily_started_at = time.perf_counter()
            daily_ops = []
            for daily_key in sorted(context.get("dirtyDaily") or []):
                doc = dict((context.get("daily") or {}).get(daily_key) or {})
                if not doc:
                    continue
                key = {
                    "source": doc.get("source"),
                    "author": doc.get("author") or "Unknown User",
                    "projectId": doc.get("projectId") or "",
                    "date": doc.get("date") or "",
                }
                daily_ops.append(UpdateOne(key, {"$set": doc}, upsert=True))

            if daily_ops:
                self.db.daily_author_activity.bulk_write(daily_ops, ordered=False)

            context["rawDailyWrites"] = int(context.get("rawDailyWrites") or 0) + len(daily_ops)
            context["rawFlushCount"] = int(context.get("rawFlushCount") or 0) + 1
            _add_raw_timing(context, "dailyFlush", time.perf_counter() - daily_started_at)
            self._last_raw_event_batch_accounting_stats = {
                "rawTiming": dict(context.get("rawTiming") or {}),
                "rawFlushCount": int(context.get("rawFlushCount") or 0),
                "rawStateWrites": int(context.get("rawStateWrites") or 0),
                "rawDailyWrites": int(context.get("rawDailyWrites") or 0),
                "rawAccumulatorDailyWrites": int(context.get("rawAccumulatorDailyWrites") or 0),
                "rawAccumulatorStateWrites": int(context.get("rawAccumulatorStateWrites") or 0),
                "rawFastAccountingEnabled": bool(context.get("fastAccountingEnabled")),
                "rawIntervalAccumulatorEnabled": bool(context.get("intervalAccumulatorEnabled")),
                "rawExecutionPlanEnabled": bool(context.get("executionPlanEnabled")),
                "rawFastContextBuildSeconds": float((context.get("rawTiming") or {}).get("stateLoad") or 0.0),
                "rawDailyMergeFlushSeconds": float(context.get("rawDailyMergeFlushSeconds") or 0.0),
                "rawAccumulatorFlushSeconds": float(context.get("rawAccumulatorFlushSeconds") or 0.0),
                "executionPlanBuildSeconds": float(context.get("executionPlanBuildSeconds") or 0.0),
                "hotLoopHelperCallsByName": dict(context.get("hotLoopHelperCallsByName") or {}),
                "precomputedIntervalChecksByType": dict(context.get("precomputedIntervalChecksByType") or {}),
            }
        finally:
            self._raw_event_batch_accounting = None

    def _flush_pending_daily_author_activity_deltas(self, context: dict[str, Any]) -> None:
        pending = context.get("pendingDailyDeltas") or {}
        order = context.get("pendingDailyOrder") or []

        if not pending:
            return

        started_at = time.perf_counter()
        pending_count = len(order)

        for daily_key in order:
            item = pending.get(daily_key)

            if not item:
                continue

            snapshot = dict(item.get("snapshot") or {})
            merged = dict(item.get("mergedDeltas") or _empty_event_deltas())

            self._merge_daily_author_activity_now(snapshot, merged)

        pending.clear()
        order.clear()
        elapsed = time.perf_counter() - started_at
        context["rawDailyMergeFlushSeconds"] = float(context.get("rawDailyMergeFlushSeconds") or 0.0) + elapsed
        context["rawAccumulatorFlushSeconds"] = float(context.get("rawAccumulatorFlushSeconds") or 0.0) + elapsed
        context["rawAccumulatorDailyWrites"] = int(context.get("rawAccumulatorDailyWrites") or 0) + pending_count
        _add_raw_timing(context, "dailyMergeFlush", elapsed)

    def _batch_state_doc(self, state_key: str) -> dict[str, Any] | None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return None

        states = context.setdefault("states", {})
        if state_key not in states:
            states[state_key] = self.db.aggregate_session_state.find_one({"_id": state_key}) or {}
        return dict(states.get(state_key) or {})

    def _set_batch_state_doc(self, state_key: str, doc: dict[str, Any]) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            self.db.aggregate_session_state.update_one(
                {"_id": state_key},
                {"$set": {key: value for key, value in doc.items() if key != "_id"}},
                upsert=True,
            )
            return

        doc = dict(doc)
        doc["_id"] = state_key
        context.setdefault("states", {})[state_key] = doc
        context.setdefault("dirtyStates", set()).add(state_key)

    def _batch_daily_doc(self, key: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        daily_key = "|".join(
            [
                str(key.get("source") or ""),
                str(key.get("author") or "Unknown User"),
                str(key.get("projectId") or ""),
                str(key.get("date") or ""),
            ]
        )
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return daily_key, self.db.daily_author_activity.find_one(key, {"_id": 0}) or {}

        daily = context.setdefault("daily", {})
        if daily_key not in daily:
            daily[daily_key] = self.db.daily_author_activity.find_one(key, {"_id": 0}) or {}
        return daily_key, dict(daily.get(daily_key) or {})

    def _set_batch_daily_doc(self, daily_key: str, doc: dict[str, Any]) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            key = {
                "source": doc.get("source"),
                "author": doc.get("author") or "Unknown User",
                "projectId": doc.get("projectId") or "",
                "date": doc.get("date") or "",
            }
            self.db.daily_author_activity.update_one(key, {"$set": doc}, upsert=True)
            return

        context.setdefault("daily", {})[daily_key] = dict(doc)
        context.setdefault("dirtyDaily", set()).add(daily_key)

    def _batch_day_session_doc(self, raw_author: str, day_date: str) -> dict[str, Any]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        query = {"rawAuthor": raw_author, "date": day_date}
        projection = {"_id": 0, "startedAt": 1}

        if not context:
            return self.db.day_sessions.find_one(query, projection) or {}

        cache = context.setdefault("daySessions", {})
        cache_key = (raw_author, day_date)

        if cache_key not in cache:
            cache[cache_key] = self.db.day_sessions.find_one(query, projection) or {}

        return dict(cache.get(cache_key) or {})

    def _batch_status_events(self, raw_author: str, day_date: str) -> list[dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        query = {"rawAuthor": raw_author, "date": day_date}
        projection = {"_id": 0, "statusEventType": 1, "transitionAt": 1, "timeZoneId": 1, "reason": 1}

        if not context:
            return list(self.db.status_events.find(query, projection))

        cache = context.setdefault("statusEvents", {})
        cache_key = (raw_author, day_date)

        if cache_key not in cache:
            cache[cache_key] = list(self.db.status_events.find(query, projection))

        return [dict(item) for item in cache.get(cache_key) or []]

    def _batch_reports_stopped_events(self, raw_author: str, day_date: str) -> list[dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        query = {
            "rawAuthor": raw_author,
            "date": day_date,
            "reason": {"$in": ["reports_stopped", "reports_resumed"]},
        }
        projection = {"_id": 0, "statusEventType": 1, "transitionAt": 1, "reason": 1}

        if not context:
            return list(self.db.status_events.find(query, projection))

        cache = context.setdefault("reportsStoppedEvents", {})
        cache_key = (raw_author, day_date)

        if cache_key not in cache:
            status_events = self._batch_status_events(raw_author, day_date)
            cache[cache_key] = [
                {
                    "statusEventType": item.get("statusEventType"),
                    "transitionAt": item.get("transitionAt"),
                    "reason": item.get("reason"),
                }
                for item in status_events
                if item.get("reason") in {"reports_stopped", "reports_resumed"}
            ]

        return [dict(item) for item in cache.get(cache_key) or []]

    def _batch_break_online_offline_events(self, raw_author: str, occurred_at: dt.datetime) -> list[dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        projection = {"_id": 0, "eventType": 1, "timestamp": 1, "date": 1}

        if not context:
            query = {
                "rawAuthor": raw_author,
                "eventType": {"$in": ["online", "offline"]},
                "timestamp": {"$lte": occurred_at},
            }
            return list(self.db.break_events.find(query, projection))

        query = {"rawAuthor": raw_author, "eventType": {"$in": ["online", "offline"]}}
        cache = context.setdefault("breakOnlineOfflineEvents", {})

        if raw_author not in cache:
            cache[raw_author] = list(self.db.break_events.find(query, projection))

        return [dict(item) for item in cache.get(raw_author) or []]

    def _batch_daily_overtime_rows(self, raw_author: str, day_date: str) -> list[dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        query = {"author": raw_author, "date": day_date, "overtimeActiveSeconds": {"$gt": 0}}
        projection = {"_id": 0, "source": 1, "hourlyActivity": 1, "overtimeActiveSeconds": 1}

        if not context:
            return list(self.db.daily_author_activity.find(query, projection))

        cache = context.setdefault("dailyOvertimeRows", {})
        cache_key = (raw_author, day_date)

        if cache_key not in cache:
            cache[cache_key] = list(self.db.daily_author_activity.find(query, projection))

        return [dict(item) for item in cache.get(cache_key) or []]

    def _batch_overtime_day_session_doc(self, raw_author: str, day_date: str) -> dict[str, Any]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        query = {"rawAuthor": raw_author, "date": day_date}
        projection = {"_id": 0, "lastOfflineAt": 1, "timeZoneId": 1, "reminderAction": 1}

        if not context:
            return self.db.day_sessions.find_one(query, projection) or {}

        cache = context.setdefault("overtimeDaySessions", {})
        cache_key = (raw_author, day_date)

        if cache_key not in cache:
            cache[cache_key] = self.db.day_sessions.find_one(query, projection) or {}

        return dict(cache.get(cache_key) or {})

    def _fast_event_derived_cache(self, event: dict[str, Any]) -> dict[str, Any] | None:
        context = getattr(self, "_raw_event_batch_accounting", None)

        if not context or not context.get("fastAccountingEnabled"):
            return None

        event_key = _raw_event_fast_cache_key(event)
        return context.setdefault("eventDerived", {}).setdefault(event_key, {})

    def _raw_accounting_alias(self, raw_author: str) -> str:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return composed(self).resolve_author_alias(raw_author or "Unknown User")

        aliases = context.setdefault("authorAliases", {})
        key = str(raw_author or "Unknown User")
        if key not in aliases:
            aliases[key] = composed(self).resolve_author_alias(key)
        return str(aliases.get(key) or "Unknown User")

    def _execution_plan_for_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context or not context.get("executionPlanEnabled"):
            return None

        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")
        source = str(event.get("source") or "")
        if not raw_author or not day_date:
            return None

        cache_key = (raw_author, day_date, source)
        plans = context.setdefault("executionPlans", {})
        if cache_key in plans:
            return plans[cache_key]

        started_at = time.perf_counter()
        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
        try:
            day = dt.date.fromisoformat(day_date)
            zone = ZoneInfo(time_zone_id)
        except ValueError:
            zone = ZoneInfo("UTC")
            day = (_coerce_datetime(event.get("occurredAtUtc")) or dt.datetime.now(dt.UTC)).date()

        night_start_at = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), zone).astimezone(dt.UTC)
        night_end_at = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), zone).astimezone(dt.UTC)
        if night_end_at <= night_start_at:
            night_end_at += dt.timedelta(days=1)

        day_session = self._batch_day_session_doc(raw_author, day_date)
        started_at_utc = _coerce_datetime((day_session or {}).get("startedAt"))
        status_events = list(self._batch_status_events(raw_author, day_date))
        reports_stopped_events = list(self._batch_reports_stopped_events(raw_author, day_date))
        break_events = list(self._batch_break_online_offline_events(raw_author, dt.datetime.combine(day, dt.time.max, dt.UTC)))
        consumed_microseconds = self._normal_microseconds_consumed_for_event_fast_baseline(raw_author, day_date)
        target_dates = getattr(self, "_aggregate_rebuild_target_dates", None)
        target_authors = getattr(self, "_aggregate_rebuild_target_authors", None)
        materialize = (target_dates is None or day_date in target_dates) and (
            not target_authors or self._raw_accounting_alias(raw_author) in target_authors
        )
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
        if is_device_source(source) and global_setting.get("deviceIdleThresholdSeconds"):
            idle_threshold_seconds = int(global_setting["deviceIdleThresholdSeconds"])
        elif global_setting.get("idleThresholdSeconds"):
            idle_threshold_seconds = int(global_setting["idleThresholdSeconds"])
        else:
            idle_threshold_seconds = DEFAULT_IDLE_THRESHOLD_SECONDS

        plan = {
            "author": raw_author,
            "date": day_date,
            "source": source,
            "timeZoneId": time_zone_id,
            "idleThresholdSeconds": idle_threshold_seconds,
            "materialize": materialize,
            "startedAt": started_at_utc,
            "statusEvents": status_events,
            "reportsStoppedEvents": reports_stopped_events,
            "breakEvents": break_events,
            "nightWindow": (night_start_at, night_end_at),
            "consumedNormalMicroseconds": consumed_microseconds,
        }
        plans[cache_key] = plan
        elapsed = time.perf_counter() - started_at
        context["executionPlanBuildSeconds"] = float(context.get("executionPlanBuildSeconds") or 0.0) + elapsed
        _add_raw_timing(context, "executionPlanBuild", elapsed)
        return plan

    def _normal_microseconds_consumed_for_event_fast_baseline(self, raw_author: str, day_date: str) -> int:
        cache = getattr(self, "_daily_consumed_microseconds_cache", None)
        cache_key = (str(raw_author or "Unknown User"), str(day_date or ""))
        if cache is not None and cache_key in cache:
            return int(cache.get(cache_key) or 0)

        consumed_microseconds = 0
        for current in self.db.daily_author_activity.find(
            {"author": raw_author or "Unknown User", "date": day_date or ""},
            {"_id": 0, "activeSeconds": 1, "idleSeconds": 1, "activeMicroseconds": 1, "idleMicroseconds": 1},
        ):
            consumed_microseconds += _time_microseconds(current, "activeSeconds", "activeMicroseconds")
            consumed_microseconds += _time_microseconds(current, "idleSeconds", "idleMicroseconds")

        consumed_microseconds = min(
            DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND,
            max(0, consumed_microseconds),
        )
        if cache is not None:
            cache[cache_key] = consumed_microseconds
        return consumed_microseconds

    def _increment_hot_loop_helper_call(self, name: str) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return
        calls = context.setdefault("hotLoopHelperCallsByName", {})
        calls[name] = int(calls.get(name) or 0) + 1

    def _increment_precomputed_interval_check(self, name: str) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context:
            return
        checks = context.setdefault("precomputedIntervalChecksByType", {})
        checks[name] = int(checks.get(name) or 0) + 1

    def _plan_status_interval_context(
        self,
        plan: dict[str, Any],
        occurred_at: dt.datetime,
        received_at: dt.datetime | None,
    ) -> dict[str, Any] | None:
        self._increment_precomputed_interval_check("statusInterval")
        return status_interval_context_for_event(plan.get("statusEvents") or [], occurred_at, received_at)

    def _plan_has_reports_stopped_overlap(self, plan: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
        self._increment_precomputed_interval_check("reportsStoppedOverlap")
        if end <= start:
            return False

        offline_at: dt.datetime | None = None
        for status_event in sorted(
            [
                status_event
                for status_event in plan.get("reportsStoppedEvents") or []
                if (_coerce_datetime(status_event.get("transitionAt")) or dt.datetime.max.replace(tzinfo=dt.UTC)) <= end
            ],
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

    def _plan_overtime_window_for_interval(
        self,
        plan: dict[str, Any],
        start: dt.datetime,
        end: dt.datetime,
    ) -> tuple[dt.datetime, dt.datetime] | None:
        self._increment_precomputed_interval_check("overtimeWindow")
        if end <= start:
            return None
        night_window = plan.get("nightWindow")
        if night_window and _windows_overlap(start, end, night_window[0], night_window[1]):
            return night_window
        return None

    def _plan_workday_started_at_for_interval(
        self,
        plan: dict[str, Any],
        start: dt.datetime,
        end: dt.datetime,
    ) -> dt.datetime | None:
        self._increment_precomputed_interval_check("workdayStart")
        started_at = _coerce_datetime(plan.get("startedAt"))
        if started_at and start < started_at < end:
            return started_at
        return None

    def _plan_is_waiting_for_first_workday_activity(
        self,
        plan: dict[str, Any],
        author_last_activity_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        self._increment_precomputed_interval_check("waitingForFirstActivity")
        started_at = _coerce_datetime(plan.get("startedAt"))
        if started_at:
            if at <= started_at:
                return False
            return not author_last_activity_at or author_last_activity_at < started_at
        return False

    def _plan_author_offline_after_latest_telegram_state(self, plan: dict[str, Any], at: dt.datetime) -> bool:
        self._increment_precomputed_interval_check("telegramOffline")
        latest_event_type = ""
        latest_timestamp: dt.datetime | None = None
        day_date = str(plan.get("date") or "")

        for event in plan.get("breakEvents") or []:
            if str(event.get("date") or "") != day_date:
                continue
            timestamp = _coerce_datetime(event.get("timestamp"))
            if not timestamp or timestamp > at:
                continue
            if not latest_timestamp or timestamp > latest_timestamp:
                latest_timestamp = timestamp
                latest_event_type = str(event.get("eventType") or "")

        return latest_event_type == "offline"

    def _try_apply_rebuild_interval_accumulator_event(self, event: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if not context or not context.get("intervalAccumulatorEnabled"):
            return False, _empty_event_deltas()

        context["lastAccumulatorFallbackReason"] = None
        event = dict(event)
        event["author"] = self._raw_accounting_alias(event.get("author") or "Unknown User")
        composed(self)._update_author_time_zone_for_raw_event_accounting(
            event.get("author") or "Unknown User",
            event.get("timeZoneId"),
            event.get("timeZoneDisplayName"),
        )
        event_type = str(event.get("eventType") or "")
        source = str(event.get("source") or "")

        if source == "ual" and event_type == "events_dropped":
            return True, self._apply_events_dropped_fast(event)

        fast_activity_types = {
            "click",
            "editor_input",
            "hold",
            "play_mode",
            "scene_object_changed",
            "selection",
            "undo_redo",
        }
        if source == "ual" and event_type in fast_activity_types:
            return self._try_apply_standard_activity_fast(event)

        if event_type != "heartbeat" or source == "codex":
            context["lastAccumulatorFallbackReason"] = "unsupported_event_type"
            return False, _empty_event_deltas()

        plan = self._execution_plan_for_event(event)
        if not plan:
            context["lastAccumulatorFallbackReason"] = "missing_execution_plan"
            return False, _empty_event_deltas()

        if plan.get("statusEvents"):
            context["lastAccumulatorFallbackReason"] = "status_events"
            return False, _empty_event_deltas()

        state_key = _raw_event_session_key(event)
        state_doc = self._batch_state_doc(state_key) or {}
        state = dict(state_doc.get("state", {}))
        author_state_key = _raw_event_author_day_key(event)
        author_state_doc = self._batch_state_doc(author_state_key) or {}
        author_state = dict(author_state_doc.get("state", {}))
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else None
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        last_activity_source = str(state.get("lastActivitySource") or "")
        author_last_activity_scope = str(author_state.get("lastActivityScope") or "")
        current_scope = _raw_event_activity_scope(event)

        if not occurred_at or not last_activity_at or not last_accounting_at:
            context["lastAccumulatorFallbackReason"] = "missing_heartbeat_state"
            return False, _empty_event_deltas()

        if occurred_at <= last_accounting_at:
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        if last_activity_source and source != last_activity_source:
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        if author_last_activity_scope and current_scope != author_last_activity_scope:
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        idle_threshold_seconds = int(plan.get("idleThresholdSeconds") or DEFAULT_IDLE_THRESHOLD_SECONDS)

        if (occurred_at - last_activity_at).total_seconds() < idle_threshold_seconds:
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        return self._try_apply_heartbeat_idle_fast(event, state, author_state, idle_threshold_seconds)

    def _try_apply_standard_activity_fast(self, event: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        plan = self._execution_plan_for_event(event)
        state_key = _raw_event_session_key(event)
        state_doc = self._batch_state_doc(state_key) or {}
        state = dict(state_doc.get("state", {}))
        author_state_key = _raw_event_author_day_key(event)
        author_state_doc = self._batch_state_doc(author_state_key) or {}
        author_state = dict(author_state_doc.get("state", {}))
        event_type = str(event.get("eventType") or "")
        current_source = str(event.get("source") or "")
        current_scope = _raw_event_activity_scope(event)
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else None
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at

        if not occurred_at or not occurred_local_at:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "missing_timestamp"
            return False, _empty_event_deltas()

        if not plan:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "missing_execution_plan"
            return False, _empty_event_deltas()

        if plan.get("statusEvents"):
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "status_events"
            return False, _empty_event_deltas()

        if self._plan_overtime_window_for_interval(plan, occurred_at, occurred_at + dt.timedelta(microseconds=1)) is not None:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "overtime_window"
            return False, _empty_event_deltas()

        if self._plan_status_interval_context(plan, occurred_at, _coerce_datetime(event.get("receivedAt"))):
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "status_interval"
            return False, _empty_event_deltas()

        source_is_focused = state.get("isFocused")
        raw_is_activity = _is_activity_event(event)
        is_activity = raw_is_activity and (source_is_focused is not False or event_type == "focus")
        is_time_accounting_activity = is_activity and not _is_unity_saved_file_event(event)

        if not is_time_accounting_activity:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "not_time_accounting_activity"
            return False, _empty_event_deltas()

        first_activity_at = _coerce_datetime(state.get("firstActivityAt"))
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        last_activity_local_at = _parse_local_datetime(state.get("lastActivityLocalAt"))
        last_accounting_local_at = _parse_local_datetime(state.get("lastAccountingLocalAt"))
        last_activity_source = str(state.get("lastActivitySource") or "")
        last_accounting_source = str(state.get("lastAccountingSource") or "")
        author_first_activity_at = _coerce_datetime(author_state.get("firstActivityAt"))
        author_last_activity_at = _coerce_datetime(author_state.get("lastActivityAt"))
        author_last_accounting_at = _coerce_datetime(author_state.get("lastAccountingAt"))
        author_last_activity_local_at = _parse_local_datetime(author_state.get("lastActivityLocalAt"))
        author_last_accounting_local_at = _parse_local_datetime(author_state.get("lastAccountingLocalAt"))
        author_last_activity_scope = str(author_state.get("lastActivityScope") or "")
        author_last_accounting_scope = str(author_state.get("lastAccountingScope") or "")
        idle_threshold_seconds = int(plan.get("idleThresholdSeconds") or DEFAULT_IDLE_THRESHOLD_SECONDS)
        consumed_normal_microseconds = int(plan.get("consumedNormalMicroseconds") or 0)
        deltas = _empty_event_deltas()
        waiting_for_first_workday_activity = self._plan_is_waiting_for_first_workday_activity(plan, author_last_activity_at, occurred_at)

        if not first_activity_at:
            first_activity_at = occurred_at
            last_accounting_at = occurred_at
            last_accounting_local_at = occurred_local_at
            last_accounting_source = current_source
        elif not waiting_for_first_workday_activity and last_activity_at and last_accounting_at and occurred_at > last_activity_at:
            accounting_start_at = last_accounting_at
            accounting_start_local_at = last_accounting_local_at or last_accounting_at

            if author_last_accounting_at and author_last_accounting_at > accounting_start_at:
                accounting_start_at = author_last_accounting_at
                accounting_start_local_at = author_last_accounting_local_at or author_last_accounting_at

            interval_activity_at = last_activity_at
            if author_last_activity_at and author_last_activity_at > interval_activity_at:
                interval_activity_at = author_last_activity_at

            if accounting_start_at < occurred_at:
                workday_started_at = self._plan_workday_started_at_for_interval(plan, accounting_start_at, occurred_at)
                if workday_started_at and accounting_start_at < workday_started_at < occurred_at:
                    accounting_start_at = workday_started_at
                    accounting_start_local_at = _to_local_datetime(
                        workday_started_at,
                        _valid_time_zone_id(event.get("timeZoneId")) or "UTC",
                    )

                interval_end_at = occurred_at
                interval_end_local_at = occurred_local_at
                interval_is_active = (occurred_at - interval_activity_at).total_seconds() < idle_threshold_seconds
                interval_overtime_window = self._plan_overtime_window_for_interval(plan, accounting_start_at, interval_end_at)
                if interval_overtime_window is not None:
                    if context is not None:
                        context["lastAccumulatorFallbackReason"] = "interval_overtime_window"
                    return False, _empty_event_deltas()
                if self._plan_has_reports_stopped_overlap(plan, accounting_start_at, interval_end_at):
                    if context is not None:
                        context["lastAccumulatorFallbackReason"] = "reports_stopped_overlap"
                    return False, _empty_event_deltas()
                interval_deltas = _interval_deltas(
                    accounting_start_at,
                    interval_end_at,
                    accounting_start_local_at,
                    interval_end_local_at,
                    interval_is_active,
                    consumed_normal_microseconds,
                    None,
                )
                _merge_batch_deltas(deltas, interval_deltas)

            last_accounting_at = occurred_at
            last_accounting_local_at = occurred_local_at
            last_accounting_source = current_source
        elif waiting_for_first_workday_activity:
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

        activity_type = _activity_count_type(event_type)
        deltas["activityCountDeltas"].append({"type": activity_type, "count": _raw_event_activity_count(event)})
        return True, self._materialize_fast_event(
            event,
            deltas,
            state_key,
            author_state_key,
            first_activity_at,
            last_activity_at,
            last_accounting_at,
            last_activity_local_at,
            last_accounting_local_at,
            last_activity_source,
            last_accounting_source,
            source_is_focused,
            author_first_activity_at,
            author_last_activity_at,
            author_last_accounting_at,
            author_last_activity_local_at,
            author_last_accounting_local_at,
            author_last_activity_scope,
            author_last_accounting_scope,
            author_state.get("statusIdleAccountedUntil"),
            waiting_for_first_workday_activity,
            plan,
        )

    def _try_apply_heartbeat_idle_fast(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
        author_state: dict[str, Any],
        idle_threshold_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        context = getattr(self, "_raw_event_batch_accounting", None)
        plan = self._execution_plan_for_event(event)
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else None
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        last_accounting_local_at = _parse_local_datetime(state.get("lastAccountingLocalAt"))
        author_last_activity_at = _coerce_datetime(author_state.get("lastActivityAt"))

        if not plan:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "missing_execution_plan"
            return False, _empty_event_deltas()

        if not occurred_at or not occurred_local_at or not last_activity_at or not last_accounting_at:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "missing_heartbeat_state"
            return False, _empty_event_deltas()

        if self._plan_overtime_window_for_interval(plan, occurred_at, occurred_at + dt.timedelta(microseconds=1)) is not None:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "overtime_window"
            return False, _empty_event_deltas()

        heartbeat_end = occurred_at
        heartbeat_local_end = occurred_local_at
        stale_heartbeat_capped = False
        received_at = _coerce_datetime(event.get("receivedAt"))
        skew_floor = idle_threshold_seconds * STALE_HEARTBEAT_RECEIVE_SKEW_MULTIPLIER
        if skew_floor < STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR:
            skew_floor = STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR

        if received_at is not None and received_at > occurred_at and (received_at - occurred_at).total_seconds() >= skew_floor:
            max_accounting_seconds = idle_threshold_seconds * MAX_STALE_HEARTBEAT_IDLE_MULTIPLIER
            if max_accounting_seconds > 0:
                capped_end = last_accounting_at + dt.timedelta(seconds=max_accounting_seconds)
                if heartbeat_end > capped_end:
                    heartbeat_end = capped_end
                    heartbeat_local_end = (last_accounting_local_at or last_accounting_at) + dt.timedelta(seconds=max_accounting_seconds)
                    stale_heartbeat_capped = True

        interval_seconds = int((heartbeat_end - last_accounting_at).total_seconds())
        if interval_seconds < MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS:
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        interval_start_at = last_accounting_at
        interval_start_local_at = last_accounting_local_at or last_accounting_at
        interval_end_at = heartbeat_end
        interval_end_local_at = heartbeat_local_end
        workday_started_at = None
        if self._plan_overtime_window_for_interval(plan, last_activity_at, last_activity_at + dt.timedelta(microseconds=1)):
            workday_started_at = self._plan_workday_started_at_for_interval(plan, interval_start_at, interval_end_at)
        if workday_started_at:
            interval_start_at = workday_started_at
            interval_start_local_at = _to_local_datetime(
                workday_started_at,
                _valid_time_zone_id(event.get("timeZoneId")) or "UTC",
            )

        if self._plan_is_waiting_for_first_workday_activity(plan, author_last_activity_at, interval_end_at):
            return self._apply_heartbeat_noop_fast(event, state, author_state, plan)

        interval_overtime_window = self._plan_overtime_window_for_interval(plan, interval_start_at, interval_end_at)
        if interval_overtime_window is not None:
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "interval_overtime_window"
            return False, _empty_event_deltas()
        if self._plan_has_reports_stopped_overlap(plan, interval_start_at, interval_end_at):
            if context is not None:
                context["lastAccumulatorFallbackReason"] = "reports_stopped_overlap"
            return False, _empty_event_deltas()

        consumed_normal_microseconds = int(plan.get("consumedNormalMicroseconds") or 0)
        deltas = _interval_deltas(
            interval_start_at,
            interval_end_at,
            interval_start_local_at,
            interval_end_local_at,
            False,
            consumed_normal_microseconds,
            None,
        )
        state_key = _raw_event_session_key(event)
        author_state_key = _raw_event_author_day_key(event)
        first_activity_at = _coerce_datetime(state.get("firstActivityAt"))
        last_activity_local_at = _parse_local_datetime(state.get("lastActivityLocalAt"))
        author_first_activity_at = _coerce_datetime(author_state.get("firstActivityAt"))
        author_last_accounting_at = _coerce_datetime(author_state.get("lastAccountingAt"))
        author_last_accounting_local_at = _parse_local_datetime(author_state.get("lastAccountingLocalAt"))
        current_scope = _raw_event_activity_scope(event)

        if not author_last_accounting_at or heartbeat_end > author_last_accounting_at:
            author_last_accounting_at = heartbeat_end
            author_last_accounting_local_at = heartbeat_local_end

        return True, self._materialize_fast_event(
            event,
            deltas,
            state_key,
            author_state_key,
            first_activity_at,
            last_activity_at,
            heartbeat_end,
            last_activity_local_at,
            heartbeat_local_end,
            str(state.get("lastActivitySource") or ""),
            str(event.get("source") or ""),
            state.get("isFocused"),
            author_first_activity_at,
            author_last_activity_at,
            author_last_accounting_at,
            _parse_local_datetime(author_state.get("lastActivityLocalAt")),
            author_last_accounting_local_at,
            str(author_state.get("lastActivityScope") or ""),
            current_scope,
            author_state.get("statusIdleAccountedUntil"),
            False,
            plan,
        )

    def _materialize_fast_event(
        self,
        event: dict[str, Any],
        deltas: dict[str, Any],
        state_key: str,
        author_state_key: str,
        first_activity_at: dt.datetime | None,
        last_activity_at: dt.datetime | None,
        last_accounting_at: dt.datetime | None,
        last_activity_local_at: dt.datetime | None,
        last_accounting_local_at: dt.datetime | None,
        last_activity_source: str,
        last_accounting_source: str,
        source_is_focused: Any,
        author_first_activity_at: dt.datetime | None,
        author_last_activity_at: dt.datetime | None,
        author_last_accounting_at: dt.datetime | None,
        author_last_activity_local_at: dt.datetime | None,
        author_last_accounting_local_at: dt.datetime | None,
        author_last_activity_scope: str,
        author_last_accounting_scope: str,
        status_idle_accounted_until: Any,
        waiting_for_first_workday_activity: bool,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
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
        suppress_deltas = self._should_suppress_post_offline_plugin_deltas_fast(event, deltas, plan)
        materialize = bool(plan.get("materialize")) if plan else self._should_materialize_aggregate_date(
            str(snapshot.get("date") or ""),
            str(snapshot.get("author") or "Unknown User"),
        )

        if not suppress_deltas and materialize and (not waiting_for_first_workday_activity or _has_presence_delta(deltas)):
            self._update_daily_author_activity(snapshot, deltas)

        current_state = dict((self._batch_state_doc(state_key) or {}).get("state", {}))
        current_author_state = dict((self._batch_state_doc(author_state_key) or {}).get("state", {}))
        self._set_batch_state_doc(
            state_key,
            {
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
                    "lastSceneNavigationDurationSeconds": current_state.get("lastSceneNavigationDurationSeconds"),
                    "lastSceneNavigationStartedAt": current_state.get("lastSceneNavigationStartedAt"),
                    "isFocused": source_is_focused,
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        self._set_batch_state_doc(
            author_state_key,
            {
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
                    "lastSceneNavigationDurationSeconds": current_author_state.get("lastSceneNavigationDurationSeconds"),
                    "lastSceneNavigationStartedAt": current_author_state.get("lastSceneNavigationStartedAt"),
                    "statusIdleAccountedUntil": status_idle_accounted_until,
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        return _empty_event_deltas() if suppress_deltas else deltas

    def _apply_heartbeat_noop_fast(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
        author_state: dict[str, Any],
        plan: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
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
        deltas = _empty_event_deltas()
        author_last_activity_at = _coerce_datetime(author_state.get("lastActivityAt"))
        waiting_for_first_workday_activity = (
            self._plan_is_waiting_for_first_workday_activity(plan, author_last_activity_at, occurred_at)
            if plan
            else self._is_waiting_for_first_workday_activity(event, author_last_activity_at, occurred_at)
        )
        materialize = bool(plan.get("materialize")) if plan else self._should_materialize_aggregate_date(
            str(snapshot.get("date") or ""),
            str(snapshot.get("author") or "Unknown User"),
        )
        if materialize and not waiting_for_first_workday_activity:
            self._update_daily_author_activity(snapshot, deltas)

        state_key = _raw_event_session_key(event)
        self._set_batch_state_doc(
            state_key,
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
                "state": dict(state),
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        author_state_key = _raw_event_author_day_key(event)
        self._set_batch_state_doc(
            author_state_key,
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
                "state": dict(author_state),
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        return True, deltas

    def _apply_events_dropped_fast(self, event: dict[str, Any]) -> dict[str, Any]:
        state_key = _raw_event_session_key(event)
        previous = self._batch_state_doc(state_key)
        if previous is None:
            previous = self.db.aggregate_session_state.find_one({"_id": state_key}) or {}
        state = dict(previous.get("state", {}))
        author_state_key = _raw_event_author_day_key(event)
        author_previous = self._batch_state_doc(author_state_key)
        if author_previous is None:
            author_previous = self.db.aggregate_session_state.find_one({"_id": author_state_key}) or {}
        author_state = dict(author_previous.get("state", {}))
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at
        first_activity_at = _coerce_datetime(state.get("firstActivityAt")) or occurred_at
        author_first_activity_at = _coerce_datetime(author_state.get("firstActivityAt")) or occurred_at
        current_source = str(event.get("source") or "")
        current_scope = _raw_event_activity_scope(event)

        self._set_batch_state_doc(
            state_key,
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
                "state": {
                    "firstActivityAt": first_activity_at.isoformat() if first_activity_at else None,
                    "lastActivityAt": state.get("lastActivityAt"),
                    "lastAccountingAt": occurred_at.isoformat(),
                    "lastActivityLocalAt": state.get("lastActivityLocalAt"),
                    "lastAccountingLocalAt": occurred_local_at.isoformat(),
                    "lastActivitySource": state.get("lastActivitySource"),
                    "lastAccountingSource": current_source or None,
                    "lastHoldDurationSeconds": _hold_duration_seconds_for_state(event),
                    "lastHoldStartedAt": _hold_started_at_for_state(event),
                    "lastSceneNavigationDurationSeconds": state.get("lastSceneNavigationDurationSeconds"),
                    "lastSceneNavigationStartedAt": state.get("lastSceneNavigationStartedAt"),
                    "isFocused": state.get("isFocused"),
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        self._set_batch_state_doc(
            author_state_key,
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
                "state": {
                    "firstActivityAt": author_first_activity_at.isoformat() if author_first_activity_at else None,
                    "lastActivityAt": author_state.get("lastActivityAt"),
                    "lastAccountingAt": occurred_at.isoformat(),
                    "lastActivityLocalAt": author_state.get("lastActivityLocalAt"),
                    "lastAccountingLocalAt": occurred_local_at.isoformat(),
                    "lastActivityScope": author_state.get("lastActivityScope"),
                    "lastAccountingScope": current_scope or None,
                    "lastHoldDurationSeconds": _hold_duration_seconds_for_state(event),
                    "lastHoldStartedAt": _hold_started_at_for_state(event),
                    "lastSceneNavigationDurationSeconds": author_state.get("lastSceneNavigationDurationSeconds"),
                    "lastSceneNavigationStartedAt": author_state.get("lastSceneNavigationStartedAt"),
                    "statusIdleAccountedUntil": author_state.get("statusIdleAccountedUntil"),
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        return _empty_event_deltas()

    def _update_author_time_zone_for_raw_event_accounting(
        self,
        raw_author: str,
        time_zone_id: Any,
        time_zone_display_name: Any | None = None,
    ) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)

        if not context:
            composed(self).update_author_time_zone(raw_author, time_zone_id, time_zone_display_name)
            return

        cache = context.setdefault("authorTimeZones", set())
        cache_key = (str(raw_author or ""), str(time_zone_id or ""), str(time_zone_display_name or ""))

        if cache_key in cache:
            return

        cache.add(cache_key)
        composed(self).update_author_time_zone(raw_author, time_zone_id, time_zone_display_name)

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
            composed(self)._schedule_telegram_post_offline_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                report_time,
            )

        if materialize and not suppress_rebuild_notifications and _has_active_or_overtime_delta(deltas) and not suppress_vacation_prompt:
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

    def _should_suppress_post_offline_plugin_deltas_fast(
        self,
        item: dict[str, Any],
        deltas: dict[str, Any],
        plan: dict[str, Any] | None,
    ) -> bool:
        if not plan:
            self._increment_hot_loop_helper_call("_should_suppress_post_offline_plugin_deltas")
            return self._should_suppress_post_offline_plugin_deltas(item, deltas)

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

        return self._plan_author_offline_after_latest_telegram_state(plan, received_at)

    def _is_author_offline_after_latest_telegram_state(self, raw_author: str, day_date: str, at: dt.datetime) -> bool:
        latest_event_type = ""
        latest_timestamp: dt.datetime | None = None

        context = getattr(self, "_raw_event_batch_accounting", None)
        if context:
            events = self._batch_break_online_offline_events(raw_author, at)
        else:
            events = self.db.break_events.find(
                {
                    "rawAuthor": raw_author,
                    "date": day_date,
                    "eventType": {"$in": ["online", "offline"]},
                    "timestamp": {"$lte": at},
                },
                {"_id": 0, "eventType": 1, "timestamp": 1},
            )

        for event in events:
            if context and str(event.get("date") or "") != day_date:
                continue

            timestamp = _coerce_datetime(event.get("timestamp"))

            if not timestamp:
                continue

            if timestamp > at:
                continue

            if not latest_timestamp or timestamp > latest_timestamp:
                latest_timestamp = timestamp
                latest_event_type = str(event.get("eventType") or "")

        return latest_event_type == "offline"

    def _apply_raw_event_to_aggregates(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        event["author"] = composed(self).resolve_author_alias(event.get("author") or "Unknown User")
        composed(self)._update_author_time_zone_for_raw_event_accounting(
            event.get("author") or "Unknown User",
            event.get("timeZoneId"),
            event.get("timeZoneDisplayName"),
        )
        state_key = _raw_event_session_key(event)
        previous = self._batch_state_doc(state_key)
        if previous is None:
            previous = self.db.aggregate_session_state.find_one({"_id": state_key}) or {}
        state = dict(previous.get("state", {}))
        author_state_key = _raw_event_author_day_key(event)
        author_previous = self._batch_state_doc(author_state_key)
        if author_previous is None:
            author_previous = self.db.aggregate_session_state.find_one({"_id": author_state_key}) or {}
        author_state = dict(author_previous.get("state", {}))
        event_type = str(event.get("eventType") or "")
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at
        first_activity_at = _coerce_datetime(state.get("firstActivityAt"))
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        previous_last_activity_at = last_activity_at
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

        if (
            getattr(self, "_raw_event_batch_accounting", None)
            and getattr(self, "_raw_event_batch_accounting", {}).get("fastAccountingEnabled")
            and current_source == "ual"
            and event_type == "events_dropped"
        ):
            if not first_activity_at:
                first_activity_at = occurred_at
            if not author_first_activity_at:
                author_first_activity_at = occurred_at
            last_accounting_at = occurred_at
            last_accounting_local_at = occurred_local_at
            last_accounting_source = current_source
            author_last_accounting_at = occurred_at
            author_last_accounting_local_at = occurred_local_at
            author_last_accounting_scope = current_scope
            self._set_batch_state_doc(
                state_key,
                {
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
                        "lastSceneNavigationDurationSeconds": state.get("lastSceneNavigationDurationSeconds"),
                        "lastSceneNavigationStartedAt": state.get("lastSceneNavigationStartedAt"),
                        "isFocused": source_is_focused,
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                },
            )
            self._set_batch_state_doc(
                author_state_key,
                {
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
                        "lastSceneNavigationDurationSeconds": author_state.get("lastSceneNavigationDurationSeconds"),
                        "lastSceneNavigationStartedAt": author_state.get("lastSceneNavigationStartedAt"),
                        "statusIdleAccountedUntil": author_state.get("statusIdleAccountedUntil"),
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                },
            )
            return deltas

        raw_is_activity = _is_activity_event(event)
        is_activity = raw_is_activity and (source_is_focused is not False or event_type == "focus")
        is_time_accounting_activity = is_activity and not _is_unity_saved_file_event(event)
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
        if self._is_stale_unity_night_event_after_workday_start(event, occurred_at, received_at):
            return deltas

        status_context = self._status_interval_context_for_event(event, occurred_at, received_at)
        status_offline_at = status_context.get("offlineAt") if status_context else None
        status_online_at = status_context.get("onlineAt") if status_context else None
        is_inside_status_offline = bool(status_context and status_context.get("insideOffline"))
        status_idle_accounted_until = _coerce_datetime(author_state.get("statusIdleAccountedUntil"))
        skip_activity_interval_accounting = False
        waiting_for_first_workday_activity = False

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
            is_time_accounting_activity = False

        if event_type == "events_dropped":
            if not first_activity_at:
                first_activity_at = occurred_at
            if not author_first_activity_at:
                author_first_activity_at = occurred_at
            last_accounting_at = occurred_at
            last_accounting_local_at = occurred_local_at
            last_accounting_source = current_source
            author_last_accounting_at = occurred_at
            author_last_accounting_local_at = occurred_local_at
            author_last_accounting_scope = current_scope

        if is_time_accounting_activity and event_type == "hold":
            hold_deltas = self._device_hold_duration_deltas(
                event,
                author_state,
                consumed_normal_microseconds,
                overtime_window,
            )

            if _has_time_delta(hold_deltas):
                _merge_batch_deltas(deltas, hold_deltas)
                skip_activity_interval_accounting = True

        if is_time_accounting_activity and event_type == "scene_view_navigation":
            navigation_context = self._scene_navigation_duration_context(event, author_state)
            navigation_start_at = navigation_context[1] if navigation_context else None
            navigation_start_local_at = navigation_context[2] if navigation_context else None
            navigation_deltas = _empty_event_deltas()

            if navigation_context:
                navigation_accounted_start_at = last_accounting_at
                navigation_accounted_start_local_at = last_accounting_local_at
                if author_last_accounting_at and (
                    not navigation_accounted_start_at or author_last_accounting_at > navigation_accounted_start_at
                ):
                    navigation_accounted_start_at = author_last_accounting_at
                    navigation_accounted_start_local_at = author_last_accounting_local_at
                navigation_deltas = self._scene_navigation_duration_deltas(
                    event,
                    author_state,
                    consumed_normal_microseconds,
                    overtime_window,
                    navigation_accounted_start_at,
                    navigation_accounted_start_local_at,
                )

                if (
                    navigation_start_at
                    and first_activity_at
                    and last_activity_at
                    and last_accounting_at
                    and navigation_start_at > last_accounting_at
                ):
                    accounting_start_at = last_accounting_at
                    accounting_start_local_at = last_accounting_local_at or last_accounting_at

                    if author_last_accounting_at and author_last_accounting_at > accounting_start_at:
                        accounting_start_at = author_last_accounting_at
                        accounting_start_local_at = author_last_accounting_local_at or author_last_accounting_at

                    interval_activity_at = last_activity_at

                    if author_last_activity_at and author_last_activity_at > interval_activity_at:
                        interval_activity_at = author_last_activity_at

                    idle_gap_seconds = (navigation_start_at - interval_activity_at).total_seconds()
                    idle_interval_seconds = int((navigation_start_at - accounting_start_at).total_seconds())

                    if (
                        not self._is_waiting_for_first_workday_activity(
                            event,
                            author_last_activity_at,
                            navigation_start_at,
                        )
                        and idle_gap_seconds >= idle_threshold_seconds
                        and idle_interval_seconds >= MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS
                    ):
                        interval_overtime_window = self._overtime_window_for_interval(
                            event,
                            accounting_start_at,
                            navigation_start_at,
                        )
                        if self._has_reports_stopped_gap_overlap(event, accounting_start_at, navigation_start_at):
                            interval_overtime_window = None
                        interval_deltas = _interval_deltas(
                            accounting_start_at,
                            navigation_start_at,
                            accounting_start_local_at,
                            navigation_start_local_at or navigation_start_at,
                            False,
                            consumed_normal_microseconds,
                            interval_overtime_window,
                        )
                        _merge_batch_deltas(deltas, interval_deltas)

                if _has_time_delta(navigation_deltas):
                    _merge_batch_deltas(deltas, navigation_deltas)

                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source
                if not author_last_accounting_at or occurred_at > author_last_accounting_at:
                    author_last_accounting_at = occurred_at
                    author_last_accounting_local_at = occurred_local_at
                    author_last_accounting_scope = current_scope
            else:
                is_time_accounting_activity = False
            skip_activity_interval_accounting = True

        if is_time_accounting_activity:
            waiting_for_first_workday_activity = self._is_waiting_for_first_workday_activity(
                event,
                author_last_activity_at,
                occurred_at,
            )
            waiting_blocks_interval_accounting = waiting_for_first_workday_activity and current_source != "codex"
            if not first_activity_at:
                first_activity_at = occurred_at
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source
            elif (
                not skip_activity_interval_accounting
                and not waiting_blocks_interval_accounting
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
                    workday_started_at = self._workday_started_at_for_event_interval(event, accounting_start_at, occurred_at)

                    if workday_started_at and accounting_start_at < workday_started_at < occurred_at:
                        accounting_start_at = workday_started_at
                        accounting_start_local_at = _to_local_datetime(
                            workday_started_at,
                            _valid_time_zone_id(event.get("timeZoneId")) or "UTC",
                        )

                    if current_source == "codex":
                        interval_end_at = min(
                            occurred_at,
                            accounting_start_at + dt.timedelta(seconds=idle_threshold_seconds),
                        )
                        interval_end_local_at = accounting_start_local_at + (interval_end_at - accounting_start_at)
                        interval_is_active = interval_end_at > accounting_start_at
                    else:
                        interval_end_at = occurred_at
                        interval_end_local_at = occurred_local_at
                        interval_is_active = (occurred_at - interval_activity_at).total_seconds() < idle_threshold_seconds

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
            elif waiting_blocks_interval_accounting:
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
            and current_source != "codex"
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
                interval_start_at = last_accounting_at
                interval_start_local_at = last_accounting_local_at or last_accounting_at

                workday_started_at = self._workday_started_at_after_night_activity(
                    event,
                    last_activity_at,
                    interval_start_at,
                    interval_end_at,
                )

                if workday_started_at:
                    interval_start_at = workday_started_at
                    interval_start_local_at = _to_local_datetime(
                        workday_started_at,
                        _valid_time_zone_id(event.get("timeZoneId")) or "UTC",
                    )

                if count_idle_as_overtime:
                    interval_end_at = min(
                        heartbeat_end,
                        last_activity_at + dt.timedelta(seconds=idle_threshold_seconds),
                    )
                    interval_end_local_at = interval_start_local_at + (interval_end_at - interval_start_at)
                    interval_is_active = interval_end_at > interval_start_at

                if not self._is_waiting_for_first_workday_activity(event, author_last_activity_at, interval_end_at):
                    interval_overtime_window = self._overtime_window_for_interval(event, interval_start_at, interval_end_at)
                    if (stale_heartbeat_capped and overtime_window_kind == "night") or self._has_reports_stopped_gap_overlap(
                        event,
                        interval_start_at,
                        interval_end_at,
                    ):
                        interval_overtime_window = None
                    interval_deltas = _interval_deltas(
                        interval_start_at,
                        interval_end_at,
                        interval_start_local_at,
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

        suppress_rebuild_notifications = self._notifications_suppressed_for_rebuild()
        saved_like_event = event_type in {"asset_saved", "prefab_saved", "scene_saved", "scene_touched", "file_saved"}
        late_overtime_breakdown_event = bool(
            overtime_window_kind is not None
            and not saved_like_event
            and previous_last_activity_at
            and occurred_at <= previous_last_activity_at
        )
        event_has_overtime_time_delta = _time_microseconds(
            deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"
        ) > 0
        event_counts_as_overtime_breakdown = event_has_overtime_time_delta or late_overtime_breakdown_event
        suppress_overtime_window_breakdown = (
            overtime_window_kind is not None
            and not event_has_overtime_time_delta
            and not late_overtime_breakdown_event
        )

        saved_prefab = None if is_inside_status_offline else _saved_prefab_delta(event)
        suppress_activity_count = (
            current_source == "ual"
            and event_type in {"asset_saved", "prefab_saved", "scene_saved", "scene_touched"}
            and saved_prefab is None
            and not suppress_rebuild_notifications
        )

        if is_activity and not suppress_activity_count and not suppress_overtime_window_breakdown:
            activity_type = _codex_activity_count_type(event) if current_source == "codex" else _activity_count_type(event_type)
            activity_delta_key = "activityCountDeltas"
            activity_count = _raw_event_activity_count(event)

            if event_counts_as_overtime_breakdown:
                activity_delta_key = "overtimeActivityCountDeltas"

            deltas[activity_delta_key].append({"type": activity_type, "count": activity_count})

        if saved_prefab and not suppress_overtime_window_breakdown:
            saved_prefab_delta_key = "savedPrefabDeltas"

            if event_counts_as_overtime_breakdown:
                saved_prefab_delta_key = "overtimeSavedPrefabDeltas"

            deltas[saved_prefab_delta_key].append(saved_prefab)

        worked_file = None if is_inside_status_offline else _worked_file_delta(event)

        if worked_file and not suppress_overtime_window_breakdown:
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
        scene_navigation_duration_for_state = _scene_navigation_duration_seconds_for_state(event)
        scene_navigation_started_for_state = _scene_navigation_started_at_for_state(event)
        state_scene_navigation_duration = (
            scene_navigation_duration_for_state
            if scene_navigation_duration_for_state is not None
            else state.get("lastSceneNavigationDurationSeconds")
        )
        state_scene_navigation_started = (
            scene_navigation_started_for_state
            if scene_navigation_started_for_state is not None
            else state.get("lastSceneNavigationStartedAt")
        )
        author_state_scene_navigation_duration = (
            scene_navigation_duration_for_state
            if scene_navigation_duration_for_state is not None
            else author_state.get("lastSceneNavigationDurationSeconds")
        )
        author_state_scene_navigation_started = (
            scene_navigation_started_for_state
            if scene_navigation_started_for_state is not None
            else author_state.get("lastSceneNavigationStartedAt")
        )

        if not suppress_deltas and materialize and (not waiting_for_first_workday_activity or _has_presence_delta(deltas)):
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

        if not suppress_deltas and materialize and not suppress_rebuild_notifications and _has_presence_delta(deltas):
            composed(self)._schedule_telegram_post_offline_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                str(snapshot.get("source") or ""),
                occurred_at,
            )

        self._set_batch_state_doc(
            state_key,
            {
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
                    "lastSceneNavigationDurationSeconds": state_scene_navigation_duration,
                    "lastSceneNavigationStartedAt": state_scene_navigation_started,
                    "isFocused": source_is_focused,
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
        )
        self._set_batch_state_doc(
            author_state_key,
            {
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
                    "lastSceneNavigationDurationSeconds": author_state_scene_navigation_duration,
                    "lastSceneNavigationStartedAt": author_state_scene_navigation_started,
                    "statusIdleAccountedUntil": status_idle_accounted_until.isoformat() if status_idle_accounted_until else None,
                },
                "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
            },
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

    def _scene_navigation_duration_deltas(
        self,
        event: dict[str, Any],
        author_state: dict[str, Any],
        consumed_normal_microseconds: int,
        overtime_window: tuple[dt.datetime, dt.datetime] | None,
        accounted_start_at: dt.datetime | None = None,
        accounted_start_local_at: dt.datetime | None = None,
    ) -> dict[str, Any]:
        deltas = _empty_event_deltas()
        context = self._scene_navigation_duration_context(event, author_state)

        if not context:
            return deltas

        _duration_delta_seconds, interval_start_at, interval_start_local_at, occurred_at, interval_end_local_at = context
        if accounted_start_at and accounted_start_at > interval_start_at:
            if accounted_start_at >= occurred_at:
                return deltas
            interval_start_at = accounted_start_at
            interval_start_local_at = accounted_start_local_at or interval_start_at
        navigation_overtime_window = overtime_window or self._overtime_window_for_interval(event, interval_start_at, occurred_at)
        navigation_deltas = _interval_deltas(
            interval_start_at,
            occurred_at,
            interval_start_local_at,
            interval_end_local_at,
            True,
            consumed_normal_microseconds,
            navigation_overtime_window,
        )
        _merge_batch_deltas(deltas, navigation_deltas)
        return deltas

    def _scene_navigation_duration_context(
        self,
        event: dict[str, Any],
        author_state: dict[str, Any],
    ) -> tuple[float, dt.datetime, dt.datetime, dt.datetime, dt.datetime] | None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        navigation_duration_seconds = _float_or_none(metadata.get("navigationDurationSeconds"))

        if navigation_duration_seconds is None or navigation_duration_seconds <= 0:
            return None

        previous_duration_seconds = _float_or_none(author_state.get("lastSceneNavigationDurationSeconds"))
        previous_navigation_started_at = str(author_state.get("lastSceneNavigationStartedAt") or "")
        current_navigation_started_at = str(metadata.get("firstNavigationAtUtc") or "")

        if current_navigation_started_at and current_navigation_started_at != previous_navigation_started_at:
            previous_duration_seconds = 0.0

        duration_delta_seconds = navigation_duration_seconds - max(0.0, previous_duration_seconds or 0.0)

        if duration_delta_seconds <= 0:
            return None

        occurred_at = _coerce_datetime(event.get("occurredAtUtc") or event.get("occurredAt"))
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal"))

        if not occurred_at:
            return None

        duration_delta = dt.timedelta(seconds=duration_delta_seconds)
        interval_start_at = occurred_at - duration_delta
        interval_start_local_at = (occurred_local_at or occurred_at) - duration_delta
        interval_end_local_at = occurred_local_at or occurred_at
        return duration_delta_seconds, interval_start_at, interval_start_local_at, occurred_at, interval_end_local_at

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
        event_cache = self._fast_event_derived_cache(event)
        if event_cache is not None and "overtimeWindow" in event_cache:
            return event_cache.get("overtimeWindow")

        started_at = time.perf_counter()
        context = self._overtime_rule_context()

        try:
            vacation_window = is_vacation_overtime_window(event, context)

            if vacation_window:
                if event_cache is not None:
                    event_cache["overtimeWindow"] = vacation_window
                    event_cache["overtimeWindowKind"] = "vacation"
                return vacation_window

            night_window = None if self._suppress_night_overtime_for_midnight_offline_carryover(event) else is_night_overtime_window(event)

            if night_window:
                if event_cache is not None:
                    event_cache["overtimeWindow"] = night_window
                    event_cache["overtimeWindowKind"] = "night"
                return night_window

            telegram_window = is_telegram_overtime_window(event, context)
            if event_cache is not None:
                event_cache["overtimeWindow"] = telegram_window
                event_cache["overtimeWindowKind"] = "telegram" if telegram_window else None
            return telegram_window
        finally:
            _add_raw_timing(getattr(self, "_raw_event_batch_accounting", None), "overtimeWindow", time.perf_counter() - started_at)

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
        event_cache = self._fast_event_derived_cache(event)
        if event_cache is not None and "overtimeWindowKind" in event_cache:
            return event_cache.get("overtimeWindowKind")

        context = self._overtime_rule_context()

        if is_vacation_overtime_window(event, context):
            if event_cache is not None:
                event_cache["overtimeWindowKind"] = "vacation"
            return "vacation"
        if not self._suppress_night_overtime_for_midnight_offline_carryover(event) and is_night_overtime_window(event):
            if event_cache is not None:
                event_cache["overtimeWindowKind"] = "night"
            return "night"
        if is_telegram_overtime_window(event, context):
            if event_cache is not None:
                event_cache["overtimeWindowKind"] = "telegram"
            return "telegram"

        if event_cache is not None:
            event_cache["overtimeWindowKind"] = None
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

        session = self._batch_day_session_doc(raw_author, day_date)
        started_at = _coerce_datetime((session or {}).get("startedAt"))

        if started_at and start < started_at < end:
            return started_at

        return None

    def _workday_started_at_after_night_activity(
        self,
        event: dict[str, Any],
        activity_at: dt.datetime | None,
        start: dt.datetime,
        end: dt.datetime,
    ) -> dt.datetime | None:
        if not activity_at or end <= start:
            return None

        activity_probe = dict(event)
        activity_probe["occurredAtUtc"] = activity_at
        if not is_night_overtime_window(activity_probe):
            return None

        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date:
            return None

        session = self._batch_day_session_doc(raw_author, day_date)
        started_at = _coerce_datetime((session or {}).get("startedAt"))

        if started_at:
            if started_at >= end:
                return end
            if start < started_at < end:
                return started_at

        return None

    def _is_waiting_for_first_workday_activity(
        self,
        event: dict[str, Any],
        author_last_activity_at: dt.datetime | None,
        at: dt.datetime,
    ) -> bool:
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date:
            return False

        session = self._batch_day_session_doc(raw_author, day_date)
        started_at = _coerce_datetime((session or {}).get("startedAt"))

        if started_at:
            if at <= started_at:
                return False

            return not author_last_activity_at or author_last_activity_at < started_at

        if self._is_waiting_for_telegram_online_after_night_overtime(event, raw_author, day_date, at):
            return True

        return False

    def _is_waiting_for_telegram_online_after_night_overtime(
        self,
        event: dict[str, Any],
        raw_author: str,
        day_date: str,
        at: dt.datetime,
    ) -> bool:
        if str(event.get("source") or "") == "telegram":
            return False

        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"

        try:
            day = dt.date.fromisoformat(day_date)
            zone = ZoneInfo(time_zone_id)
        except ValueError:
            return False

        night_start_at = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_START_HOUR), zone).astimezone(dt.UTC)
        night_end_at = dt.datetime.combine(day, dt.time(hour=NIGHT_OVERTIME_END_HOUR), zone).astimezone(dt.UTC)

        if at < night_end_at:
            return False

        for daily in self._batch_daily_overtime_rows(raw_author, day_date):
            for hour in daily.get("hourlyActivity") or []:
                hour_start_at = dt.datetime.combine(day, dt.time(hour=int(hour.get("hour") or 0)), zone).astimezone(dt.UTC)
                hour_end_at = hour_start_at + dt.timedelta(hours=1)
                if (
                    int(hour.get("overtimeActiveSeconds") or 0) > 0
                    and _windows_overlap(hour_start_at, hour_end_at, night_start_at, night_end_at)
                ):
                    return True

        return False

    def _is_stale_unity_night_event_after_workday_start(
        self,
        event: dict[str, Any],
        occurred_at: dt.datetime,
        received_at: dt.datetime | None,
    ) -> bool:
        if str(event.get("source") or "") != "ual":
            return False

        night_window = is_night_overtime_window(event)

        if not received_at or not night_window:
            return False

        if received_at >= night_window[1]:
            return True

        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not raw_author or not day_date:
            return False

        session = self._batch_day_session_doc(raw_author, day_date)
        started_at = _coerce_datetime((session or {}).get("startedAt"))

        return bool(started_at and occurred_at < started_at <= received_at)

    def _suppress_night_overtime_for_midnight_offline_carryover(self, event: dict[str, Any]) -> bool:
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or _coerce_datetime(event.get("occurredAt"))
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        if not occurred_at or not raw_author or not day_date:
            return False

        latest_event: dict[str, Any] | None = None
        latest_timestamp: dt.datetime | None = None

        for current in self._batch_break_online_offline_events(raw_author, occurred_at):
            timestamp = _coerce_datetime(current.get("timestamp"))

            if not timestamp:
                continue

            if timestamp > occurred_at:
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
        started_at = time.perf_counter()
        if end <= start:
            return False

        event_cache = self._fast_event_derived_cache(event)
        cache_key = ("reportsStoppedOverlap", start.isoformat(), end.isoformat())
        if event_cache is not None and cache_key in event_cache:
            return bool(event_cache.get(cache_key))

        try:
            raw_author = str(event.get("author") or "Unknown User")
            day_date = str(event.get("date") or "")

            if not raw_author or not day_date:
                return False

            offline_at: dt.datetime | None = None

            for status_event in sorted(
                [
                    status_event
                    for status_event in self._batch_reports_stopped_events(raw_author, day_date)
                    if (_coerce_datetime(status_event.get("transitionAt")) or dt.datetime.max.replace(tzinfo=dt.UTC)) <= end
                ],
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
                    if event_cache is not None:
                        event_cache[cache_key] = True
                    return True

                offline_at = None

            result = bool(offline_at and end > offline_at)
            if event_cache is not None:
                event_cache[cache_key] = result
            return result
        finally:
            _add_raw_timing(getattr(self, "_raw_event_batch_accounting", None), "reportsStoppedOverlap", time.perf_counter() - started_at)

    def _status_interval_context_for_event(
        self,
        event: dict[str, Any],
        occurred_at: dt.datetime,
        received_at: dt.datetime | None,
    ) -> dict[str, Any] | None:
        event_cache = self._fast_event_derived_cache(event)
        if event_cache is not None and "statusIntervalContext" in event_cache:
            cached = event_cache.get("statusIntervalContext")
            return dict(cached) if isinstance(cached, dict) else None

        started_at = time.perf_counter()
        raw_author = str(event.get("author") or "Unknown User")
        day_date = str(event.get("date") or "")

        try:
            if not raw_author or not day_date or not occurred_at:
                return None

            status_events = self._batch_status_events(raw_author, day_date)
            result = status_interval_context_for_event(status_events, occurred_at, received_at)
            if event_cache is not None:
                event_cache["statusIntervalContext"] = dict(result) if result else None
            return result
        finally:
            _add_raw_timing(getattr(self, "_raw_event_batch_accounting", None), "statusInterval", time.perf_counter() - started_at)

    def _update_daily_author_activity(self, snapshot: dict[str, Any], deltas: dict[str, Any]) -> None:
        context = getattr(self, "_raw_event_batch_accounting", None)
        if context and context.get("fastAccountingEnabled"):
            key = {
                "source": snapshot.get("source"),
                "author": snapshot.get("author") or "Unknown User",
                "projectId": snapshot.get("projectId") or "",
                "date": snapshot.get("date") or "",
            }
            daily_key = "|".join(
                [
                    str(key.get("source") or ""),
                    str(key.get("author") or "Unknown User"),
                    str(key.get("projectId") or ""),
                    str(key.get("date") or ""),
                ]
            )
            pending = context.setdefault("pendingDailyDeltas", {})
            if daily_key not in pending:
                pending[daily_key] = {"snapshot": dict(snapshot), "mergedDeltas": _empty_event_deltas()}
                context.setdefault("pendingDailyOrder", []).append(daily_key)
            else:
                pending[daily_key]["snapshot"] = dict(snapshot)
            _merge_batch_deltas(pending[daily_key].setdefault("mergedDeltas", _empty_event_deltas()), deltas)
            return

        self._merge_daily_author_activity_now(snapshot, deltas)

    def _merge_daily_author_activity_now(self, snapshot: dict[str, Any], deltas: dict[str, Any]) -> None:
        started_at = time.perf_counter()
        key = {
            "source": snapshot.get("source"),
            "author": snapshot.get("author") or "Unknown User",
            "projectId": snapshot.get("projectId") or "",
            "date": snapshot.get("date") or "",
        }
        daily_key, current = self._batch_daily_doc(key)
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

        self._set_batch_daily_doc(daily_key, set_fields)
        _add_raw_timing(getattr(self, "_raw_event_batch_accounting", None), "dailyActivityMerge", time.perf_counter() - started_at)
