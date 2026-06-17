from __future__ import annotations

import threading

from ..activity_math import *
from ..backend_composable_host import composed
from ..hourly_fill_rules import empty_hourly_activity
from .activity_summary_helpers import _is_device_profile_raw_author


class ActivityDaySummarySnapshotsMixin:
    ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW = "activity-day"
    ACTIVITY_AUTHOR_DAY_SUMMARY_MAINTENANCE_LIMIT = 3
    ACTIVITY_DAY_SUMMARY_SNAPSHOT_VERSION_OFFSET = 7
    ACTIVITY_SNAPSHOT_STALE_LOCK_SECONDS = 15 * 60
    ACTIVITY_SNAPSHOT_BACKGROUND_DRAIN_LIMIT = 250
    ACTIVITY_SNAPSHOT_REPORTS_LIMIT = 50
    ACTIVITY_SNAPSHOT_LOCAL_MIDNIGHT_DELAY_SECONDS = 5 * 60

    def activity_day_summary_snapshot_version(self) -> int:
        return int(getattr(composed(self), "aggregates_version", 0)) + self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VERSION_OFFSET

    def activity_day_summary_snapshot_for_request(
        self,
        *,
        view: str,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        include_profiles: bool,
        include_hourly: bool,
        include_breakdowns: bool,
        now: dt.datetime,
    ) -> tuple[str | None, dict[str, Any] | None]:
        snapshot_date = self._activity_day_summary_snapshot_date_for_request(
            view=view,
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
            include_profiles=include_profiles,
            include_hourly=include_hourly,
            include_breakdowns=include_breakdowns,
            now=now,
        )

        if not snapshot_date:
            return None, None

        author_payload = self.activity_author_day_summary_snapshot_payload_for_request(
            snapshot_date,
            view=view,
            now=now,
        )

        if author_payload:
            return snapshot_date, {"payload": author_payload}

        doc = self.db.activity_day_summary_snapshots.find_one(
            {
                "date": snapshot_date,
                "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                "snapshotVersion": self.activity_day_summary_snapshot_version(),
            },
            {"_id": 0, "payload": 1},
        )

        if doc and not self._is_completed_day_snapshot_ready(snapshot_date, now):
            self.db.activity_day_summary_snapshots.delete_many(
                {
                    "date": snapshot_date,
                    "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                    "snapshotVersion": self.activity_day_summary_snapshot_version(),
                }
            )
            return snapshot_date, None

        return snapshot_date, doc

    def activity_author_day_summary_snapshot_payload_for_request(
        self,
        day_date: str,
        *,
        view: str,
        now: dt.datetime | None = None,
    ) -> dict[str, Any] | None:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            return None

        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        required_authors = self._snapshot_authors_for_date(snapshot_date)
        input_authors = set(self._snapshot_input_authors_for_date(snapshot_date))

        if not required_authors:
            return None

        docs = list(
            self.db.activity_author_day_summary_snapshots.find(
                {"date": snapshot_date, "rawAuthor": {"$in": required_authors}, "snapshotVersion": version},
                {"_id": 0, "rawAuthor": 1, "payload": 1},
            )
        )
        docs_by_author = {str(doc.get("rawAuthor") or ""): dict(doc.get("payload") or {}) for doc in docs}

        ready_authors = sorted(docs_by_author)
        live_authors: list[str] = []
        pending_authors: list[str] = []

        for raw_author in required_authors:
            if raw_author in docs_by_author:
                continue

            if raw_author not in input_authors:
                continue

            if self._is_author_day_live(raw_author, snapshot_date, now):
                live_authors.append(raw_author)
            else:
                pending_authors.append(raw_author)

        if not ready_authors and not pending_authors:
            return None

        payload = self._compose_activity_author_day_snapshot_payload(
            snapshot_date,
            required_authors,
            docs_by_author,
            ready_authors=ready_authors,
            pending_authors=sorted(pending_authors),
            live_authors=sorted(live_authors),
        )

        if view == "activity-hourly":
            payload = {
                "hourlyActivityByAuthor": payload.get("hourlyActivityByAuthor", []),
                "snapshot": payload.get("snapshot", {}),
            }

        return payload

    def activity_day_summary_preparing_payload(self, day_date: str, now: dt.datetime | None = None) -> dict[str, Any]:
        snapshot_date = str(day_date or "").strip()
        now = now or dt.datetime.now(dt.UTC)
        status = self.activity_day_summary_snapshot_status(snapshot_date, now)
        return {
            "totals": {
                "daySeconds": 0,
                "telegramDaySeconds": 0,
                "pluginDaySeconds": 0,
                "rawPluginDaySeconds": 0,
                "telegramToFirstActivitySeconds": 0,
                "activeSeconds": 0,
                "idleSeconds": 0,
                "meetingSeconds": 0,
                "overtimeActiveSeconds": 0,
                "breakSeconds": 0,
            },
            "activityMix": [],
            "savedPrefabs": [],
            "overtimeActivityMix": [],
            "overtimeSavedPrefabs": [],
            "authors": [],
            "profiles": [],
            "authorAliases": [],
            "hourlyActivityByAuthor": [],
            "snapshot": {"hit": False, "status": "preparing", "date": snapshot_date, **status},
        }

    def start_activity_snapshot_scheduler(self) -> dict[str, Any]:
        if getattr(self, "activity_snapshot_background_disabled", False):
            return {"started": False, "reason": "background_disabled"}

        self.start_activity_snapshot_background_drain()
        return self.reschedule_activity_snapshot_timers()

    def stop_activity_snapshot_scheduler(self) -> None:
        timers = getattr(self, "_activity_snapshot_timers", None) or {}
        for timer in list(timers.values()):
            try:
                timer.cancel()
            except Exception:
                pass
        self._activity_snapshot_timers = {}

    def reschedule_activity_snapshot_timers(self, now: dt.datetime | None = None) -> dict[str, Any]:
        if getattr(self, "activity_snapshot_background_disabled", False):
            return {"started": False, "reason": "background_disabled"}

        now = now or dt.datetime.now(dt.UTC)
        timers = getattr(self, "_activity_snapshot_timers", None)

        if timers is None:
            timers = {}
            self._activity_snapshot_timers = timers

        scheduled: list[dict[str, Any]] = []
        active_authors = self._activity_snapshot_timer_authors()
        active_keys = {author["rawAuthor"] for author in active_authors}

        for raw_author, timer in list(timers.items()):
            if raw_author not in active_keys:
                timer.cancel()
                timers.pop(raw_author, None)

        for author in active_authors:
            raw_author = author["rawAuthor"]
            run_at = self._next_activity_snapshot_timer_at(author.get("timeZoneId"), now)
            existing = timers.get(raw_author)

            if existing is not None and getattr(existing, "run_at", None) == run_at:
                scheduled.append({"rawAuthor": raw_author, "runAt": _iso(run_at)})
                continue

            if existing is not None:
                existing.cancel()

            delay = max(0.0, (run_at - now).total_seconds())
            timer = threading.Timer(delay, self._run_activity_snapshot_timer, args=(raw_author,))
            timer.daemon = True
            timer.run_at = run_at  # type: ignore[attr-defined]
            timers[raw_author] = timer
            timer.start()
            scheduled.append({"rawAuthor": raw_author, "runAt": _iso(run_at)})

        return {"started": True, "scheduled": scheduled}

    def _run_activity_snapshot_timer(self, raw_author: str) -> None:
        try:
            self.materialize_completed_author_day_snapshots_for_author(raw_author)
        finally:
            timers = getattr(self, "_activity_snapshot_timers", None) or {}
            timers.pop(raw_author, None)
            self.reschedule_activity_snapshot_timers()

    def materialize_completed_author_day_snapshots_for_author(
        self,
        raw_author: str,
        now: dt.datetime | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.UTC)
        max_items = self.ACTIVITY_SNAPSHOT_BACKGROUND_DRAIN_LIMIT if limit is None else max(0, int(limit))
        processed: list[dict[str, Any]] = []

        for day_date in self._completed_snapshot_candidate_dates_for_author(raw_author, now):
            if len(processed) >= max_items:
                break

            result = self.materialize_claimed_activity_author_day_snapshot(day_date, raw_author, now=now)
            if result.get("processed"):
                processed.append(result)

        return {"processed": processed, "remaining": len(processed) >= max_items}

    def materialize_completed_author_day_snapshot_if_ready(
        self,
        day_date: str,
        raw_author: str,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.UTC)
        snapshot_date = str(day_date or "").strip()
        resolved_author = composed(self).resolve_author_alias(str(raw_author or "Unknown User"))

        if not snapshot_date or not resolved_author:
            return {"processed": False, "reason": "missing_author_or_date"}

        if resolved_author not in set(self._snapshot_input_authors_for_date(snapshot_date)):
            return {"processed": False, "reason": "no_author_day_inputs", "rawAuthor": resolved_author, "date": snapshot_date}

        if self._is_author_day_live(resolved_author, snapshot_date, now):
            return {"processed": False, "reason": "author_day_live", "rawAuthor": resolved_author, "date": snapshot_date}

        return self.materialize_claimed_activity_author_day_snapshot(snapshot_date, resolved_author, now=now)

    def start_activity_snapshot_author_day_background_materialization(
        self,
        dates: list[str] | tuple[str, ...] | set[str],
        authors: list[str] | tuple[str, ...] | set[str],
    ) -> dict[str, Any]:
        if getattr(self, "activity_snapshot_background_disabled", False):
            return {"started": False, "reason": "background_disabled"}

        date_values = sorted({str(day) for day in dates if str(day or "").strip()})
        author_values = sorted({composed(self).resolve_author_alias(str(author or "Unknown User")) for author in authors if str(author or "").strip()})
        author_values = [author for author in author_values if author]

        if not date_values or not author_values:
            return {"started": False, "reason": "missing_author_or_date"}

        if getattr(self, "_rebuild_in_progress", False):
            return {"started": False, "reason": "rebuild_in_progress"}

        lock = getattr(self, "activity_snapshot_maintenance_lock", None)

        if lock is not None and lock.locked():
            return {"started": False, "reason": "maintenance_already_running"}

        def run() -> None:
            acquired = lock.acquire(blocking=False) if lock is not None else True

            if not acquired:
                return

            try:
                for day in date_values:
                    for author in author_values:
                        self.materialize_completed_author_day_snapshot_if_ready(day, author)
            finally:
                if lock is not None:
                    lock.release()

        thread = threading.Thread(target=run, name="activity-snapshot-author-day-maintenance", daemon=True)
        thread.start()
        return {"started": True, "dates": date_values, "authors": author_values}

    def _activity_snapshot_timer_authors(self) -> list[dict[str, Any]]:
        authors: dict[str, dict[str, Any]] = {}
        profiles = composed(self)._profiles_by_raw_author()

        for item in self.db.daily_author_activity.find({}, {"_id": 0, "author": 1, "timeZoneId": 1}):
            raw_author = composed(self).resolve_author_alias(str(item.get("author") or "Unknown User"))
            if not raw_author:
                continue
            profile = profiles.get(raw_author, {})
            if _is_device_profile_raw_author(raw_author) or str(profile.get("profileType") or "person") == "publisher":
                continue
            authors[raw_author] = {
                "rawAuthor": raw_author,
                "timeZoneId": profile.get("timeZoneId") or item.get("timeZoneId") or "UTC",
            }

        return sorted(authors.values(), key=lambda item: str(item["rawAuthor"]).lower())

    def _next_activity_snapshot_timer_at(self, time_zone_id: Any, now: dt.datetime) -> dt.datetime:
        normalized_time_zone = _valid_time_zone_id(time_zone_id) or "UTC"
        local_now = now.astimezone(ZoneInfo(normalized_time_zone))
        next_midnight_date = local_now.date() + dt.timedelta(days=1)
        next_midnight = dt.datetime.combine(next_midnight_date, dt.time.min, tzinfo=ZoneInfo(normalized_time_zone))
        return next_midnight.astimezone(dt.UTC) + dt.timedelta(seconds=self.ACTIVITY_SNAPSHOT_LOCAL_MIDNIGHT_DELAY_SECONDS)

    def activity_day_summary_empty_completed_day_payload(self, day_date: str) -> dict[str, Any]:
        snapshot_date = str(day_date or "").strip()
        authors = self._empty_completed_day_authors()
        hourly = [
            {
                "author": author["displayName"],
                "rawAuthor": author["rawAuthor"],
                "timeZoneId": author.get("timeZoneId"),
                "timeZoneDisplayName": author.get("timeZoneDisplayName"),
                "hourlyActivity": empty_hourly_activity(),
            }
            for author in authors
        ]
        return {
            "totals": {
                "daySeconds": 0,
                "telegramDaySeconds": 0,
                "pluginDaySeconds": 0,
                "rawPluginDaySeconds": 0,
                "telegramToFirstActivitySeconds": 0,
                "activeSeconds": 0,
                "idleSeconds": 0,
                "meetingSeconds": 0,
                "overtimeActiveSeconds": 0,
                "breakSeconds": 0,
            },
            "activityMix": [],
            "savedPrefabs": [],
            "overtimeActivityMix": [],
            "overtimeSavedPrefabs": [],
            "authors": authors,
            "profiles": [],
            "authorAliases": [],
            "hourlyActivityByAuthor": hourly,
            "snapshot": {"hit": False, "status": "empty", "date": snapshot_date},
        }

    def build_activity_day_summary_snapshot(self, day_date: str, now: dt.datetime | None = None) -> dict[str, Any]:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            raise ValueError("day_date is required")

        payload = self.activity_summary(
            start_date=snapshot_date,
            end_date=snapshot_date,
            date_mode=None,
            now=now,
            include_profiles=False,
            include_hourly=True,
            include_breakdowns=True,
        )
        return self.store_activity_day_summary_snapshot(snapshot_date, payload)

    def store_activity_day_summary_snapshot(self, day_date: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            raise ValueError("day_date is required")

        version = self.activity_day_summary_snapshot_version()
        stored_payload = {key: value for key, value in payload.items() if key not in {"cache", "snapshot"}}
        self.db.activity_day_summary_snapshots.delete_many(
            {
                "date": snapshot_date,
                "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                "snapshotVersion": {"$ne": version},
            }
        )
        self.db.activity_day_summary_snapshots.update_one(
            {
                "date": snapshot_date,
                "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                "snapshotVersion": version,
            },
            {
                "$set": {
                    "date": snapshot_date,
                    "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                    "snapshotVersion": version,
                    "payload": stored_payload,
                    "builtAt": dt.datetime.now(dt.UTC),
                }
            },
            upsert=True,
        )
        return stored_payload

    def materialize_activity_author_day_summary_snapshots(self, limit: int | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
        max_items = self.ACTIVITY_AUTHOR_DAY_SUMMARY_MAINTENANCE_LIMIT if limit is None else max(0, int(limit))
        processed: list[dict[str, Any]] = []

        for _index in range(max_items):
            result = self.materialize_next_completed_author_day_snapshot(now=now)

            if not result.get("processed"):
                return {**result, "processed": processed, "remaining": False}

            processed.append(result)

        return {"processed": processed, "remaining": self._next_completed_author_day_snapshot_candidate(now=now) is not None}

    def materialize_activity_author_day_summary_snapshots_locked(
        self,
        limit: int | None = None,
        now: dt.datetime | None = None,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        lock = getattr(self, "activity_snapshot_maintenance_lock", None)

        if lock is None:
            return self.materialize_activity_author_day_summary_snapshots(limit=limit, now=now)

        acquired = lock.acquire(blocking=wait)

        if not acquired:
            return {"processed": [], "remaining": True, "skipped": "maintenance_already_running"}

        try:
            return self.materialize_activity_author_day_summary_snapshots(limit=limit, now=now)
        finally:
            lock.release()

    def start_activity_snapshot_background_drain(self, limit: int | None = None) -> dict[str, Any]:
        if getattr(self, "activity_snapshot_background_disabled", False):
            return {"started": False, "reason": "background_disabled"}

        if getattr(self, "_rebuild_in_progress", False):
            return {"started": False, "reason": "rebuild_in_progress"}

        lock = getattr(self, "activity_snapshot_maintenance_lock", None)

        if lock is not None and lock.locked():
            return {"started": False, "reason": "maintenance_already_running"}

        def run() -> None:
            self.materialize_activity_author_day_summary_snapshots_locked(
                limit=self.ACTIVITY_SNAPSHOT_BACKGROUND_DRAIN_LIMIT if limit is None else limit,
                wait=False,
            )

        thread = threading.Thread(target=run, name="activity-snapshot-maintenance", daemon=True)
        thread.start()
        return {"started": True}

    def materialize_next_completed_author_day_snapshot(self, now: dt.datetime | None = None) -> dict[str, Any]:
        claimed = self.claim_next_activity_author_day_snapshot(now=now)

        if not claimed.get("claimed"):
            return {"processed": False, "reason": claimed.get("reason") or "no_completed_author_day_without_snapshot"}

        return self.materialize_claimed_activity_author_day_snapshot(
            str(claimed["date"]),
            str(claimed["rawAuthor"]),
            int(claimed["snapshotVersion"]),
            now=now,
        )

    def claim_next_activity_author_day_snapshot(self, now: dt.datetime | None = None) -> dict[str, Any]:
        version = self.activity_day_summary_snapshot_version()
        existing = self._activity_snapshot_processing_state()

        if existing:
            return {"claimed": False, "reason": "maintenance_already_running", **existing}

        candidate = self._next_completed_author_day_snapshot_candidate(now=now)

        if not candidate:
            return {"claimed": False, "reason": "no_completed_author_day_without_snapshot"}

        raw_author = str(candidate["rawAuthor"])
        day_date = str(candidate["date"])
        started_at = dt.datetime.now(dt.UTC)
        self.db.activity_snapshot_maintenance_state.update_one(
            {"kind": "author-day"},
            {
                "$set": {
                    "kind": "author-day",
                    "date": day_date,
                    "rawAuthor": raw_author,
                    "snapshotVersion": version,
                    "startedAt": started_at,
                }
            },
            upsert=True,
        )
        return {
            "claimed": True,
            "date": day_date,
            "rawAuthor": raw_author,
            "snapshotVersion": version,
            "startedAt": _iso(started_at),
        }

    def materialize_claimed_activity_author_day_snapshot(
        self,
        day_date: str,
        raw_author: str,
        snapshot_version: int | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        raw_author = str(raw_author)
        day_date = str(day_date)
        version = self.activity_day_summary_snapshot_version()

        if snapshot_version is not None and int(snapshot_version) != version:
            self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day", "date": day_date, "rawAuthor": raw_author})
            return {"processed": False, "reason": "snapshot_version_changed", "rawAuthor": raw_author, "date": day_date}

        try:
            payload = self.activity_summary(
                start_date=day_date,
                end_date=day_date,
                date_mode=None,
                now=now,
                include_profiles=False,
                include_hourly=True,
                include_breakdowns=True,
                raw_author_scope=raw_author,
            )
            author_payload = self._author_day_payload_from_summary(payload, raw_author)
            author_payload["reportsPage"] = self._reports_page_for_author_day_snapshot(day_date, raw_author)
            self.db.activity_author_day_summary_snapshots.delete_many(
                {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": {"$ne": version}}
            )
            self.db.activity_author_day_summary_snapshots.update_one(
                {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {
                    "$set": {
                        "date": day_date,
                        "rawAuthor": raw_author,
                        "snapshotVersion": version,
                        "payload": author_payload,
                        "builtAt": dt.datetime.now(dt.UTC),
                    }
                },
                upsert=True,
            )
            composed_payload = self.compose_completed_day_snapshot(day_date, now=now)
            return {
                "processed": True,
                "rawAuthor": raw_author,
                "date": day_date,
                "composed": composed_payload is not None,
            }
        finally:
            self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day", "date": day_date, "rawAuthor": raw_author})

    def activity_snapshot_materialization_status(self, now: dt.datetime | None = None, limit_days: int = 30) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        processing = self._activity_snapshot_processing_state(now=now)
        excluded_candidates = {
            (processing["date"], processing["rawAuthor"])
        } if processing else set()
        next_candidate = self._next_completed_author_day_snapshot_candidate(now=now, exclude=excluded_candidates)
        dates = sorted(
            {
                str(item)
                for item in self.db.daily_author_activity.distinct("date")
                if str(item or "").strip()
            },
            reverse=True,
        )[: max(1, int(limit_days or 30))]
        rows: list[dict[str, Any]] = []
        totals = {"ready": 0, "processing": 0, "next": 0, "pending": 0, "live": 0}

        for day_date in dates:
            authors = self._snapshot_status_authors_for_date(day_date, now)
            day_snapshot_ready = bool(
                self.db.activity_day_summary_snapshots.find_one(
                    {
                        "date": day_date,
                        "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                        "snapshotVersion": version,
                    },
                    {"_id": 1},
                )
            )

            for item in authors:
                raw_author = item["rawAuthor"]
                author_snapshot = self.db.activity_author_day_summary_snapshots.find_one(
                    {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": version},
                    {"_id": 0, "builtAt": 1},
                )
                status = "ready" if author_snapshot else "pending"

                if item["live"]:
                    status = "live"
                elif processing and processing.get("date") == day_date and processing.get("rawAuthor") == raw_author:
                    status = "processing"
                elif next_candidate and next_candidate.get("date") == day_date and next_candidate.get("rawAuthor") == raw_author:
                    status = "next"

                totals[status] = int(totals.get(status, 0)) + 1
                rows.append(
                    {
                        "date": day_date,
                        "rawAuthor": raw_author,
                        "displayName": item["displayName"],
                        "timeZoneId": item.get("timeZoneId") or "",
                        "status": status,
                        "authorSnapshotReady": bool(author_snapshot),
                        "daySnapshotReady": day_snapshot_ready,
                        "builtAt": _iso(author_snapshot.get("builtAt")) if author_snapshot else "",
                    }
                )

        return {
            "snapshotVersion": version,
            "processing": processing,
            "next": next_candidate,
            "totals": totals,
            "rows": rows,
        }

    def compose_completed_day_snapshot(self, day_date: str, now: dt.datetime | None = None) -> dict[str, Any] | None:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            return None

        now = now or dt.datetime.now(dt.UTC)
        required_authors = self._snapshot_authors_for_date(snapshot_date)
        version = self.activity_day_summary_snapshot_version()

        if not required_authors:
            return None

        input_authors = set(self._snapshot_input_authors_for_date(snapshot_date))

        for raw_author in required_authors:
            if raw_author not in input_authors:
                continue

            if self._is_author_day_live(raw_author, snapshot_date, now):
                return None

        for raw_author in sorted(input_authors):
            if not self.db.activity_author_day_summary_snapshots.find_one(
                {"date": snapshot_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                return None

        payload = self._compose_activity_day_snapshot_payload(snapshot_date, required_authors, version)
        self.store_activity_day_summary_snapshot(snapshot_date, payload)
        return payload

    def rebuild_activity_day_summary_snapshots_for_dates(
        self,
        dates: list[str] | tuple[str, ...] | set[str],
        authors: list[str] | tuple[str, ...] | set[str] | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        target_dates = sorted({str(day) for day in dates if str(day or "").strip()})
        target_authors = sorted({str(author) for author in authors or [] if str(author or "").strip()})
        processed: list[dict[str, Any]] = []
        composed_dates: list[str] = []

        for day_date in target_dates:
            candidate_authors = self._completed_snapshot_candidate_authors_for_date(day_date, now)

            if target_authors:
                candidate_authors = [author for author in candidate_authors if author in target_authors]

            for raw_author in candidate_authors:
                if self._is_author_day_live(raw_author, day_date, now):
                    continue

                payload = self.activity_summary(
                    start_date=day_date,
                    end_date=day_date,
                    date_mode=None,
                    now=now,
                    include_profiles=False,
                    include_hourly=True,
                    include_breakdowns=True,
                    raw_author_scope=raw_author,
                )
                author_payload = self._author_day_payload_from_summary(payload, raw_author)
                author_payload["reportsPage"] = self._reports_page_for_author_day_snapshot(day_date, raw_author)
                self.db.activity_author_day_summary_snapshots.delete_many(
                    {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": {"$ne": version}}
                )
                self.db.activity_author_day_summary_snapshots.update_one(
                    {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": version},
                    {
                        "$set": {
                            "date": day_date,
                            "rawAuthor": raw_author,
                            "snapshotVersion": version,
                            "payload": author_payload,
                            "builtAt": dt.datetime.now(dt.UTC),
                        }
                    },
                    upsert=True,
                )
                processed.append({"date": day_date, "rawAuthor": raw_author})

            if self.compose_completed_day_snapshot(day_date, now=now) is not None:
                composed_dates.append(day_date)

        return {"processed": processed, "composedDates": composed_dates}

    def remake_activity_day_summary_snapshots_for_range(self, start_date: str, end_date: str) -> dict[str, Any]:
        start = _parse_date(start_date)
        end = _parse_date(end_date)

        if end < start:
            raise ValueError("endDate must be on or after startDate")

        day_count = (end - start).days + 1
        if day_count > 120:
            raise ValueError("Snapshot remake range cannot exceed 120 days")

        dates = [(start + dt.timedelta(days=index)).isoformat() for index in range(day_count)]
        self.invalidate_activity_day_summary_snapshots(dates)
        self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day", "date": {"$in": dates}})
        return {"ok": True, "dates": dates, "deletedDates": len(dates)}

    def remake_all_activity_day_summary_snapshots(self) -> dict[str, Any]:
        dates = sorted(
            {
                str(item)
                for item in self.db.daily_author_activity.distinct("date")
                if str(item or "").strip()
            }
        )
        self.invalidate_activity_day_summary_snapshots(dates)
        self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day"})
        return {"ok": True, "dates": dates, "deletedDates": len(dates)}

    def cleanup_old_activity_day_summary_snapshot_versions(self) -> dict[str, Any]:
        version = self.activity_day_summary_snapshot_version()
        deleted_author_day = self.db.activity_author_day_summary_snapshots.delete_many({"snapshotVersion": {"$ne": version}}).deleted_count
        deleted_day = self.db.activity_day_summary_snapshots.delete_many({"snapshotVersion": {"$ne": version}}).deleted_count
        self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day", "snapshotVersion": {"$ne": version}})
        return {"ok": True, "snapshotVersion": version, "deletedAuthorDaySnapshots": deleted_author_day, "deletedDaySnapshots": deleted_day}

    def invalidate_activity_day_summary_snapshots(
        self,
        dates: list[str] | tuple[str, ...] | set[str] | None = None,
        authors: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> None:
        if dates is not None:
            date_values = sorted({str(day) for day in dates if str(day or "").strip()})

            if not date_values:
                return

            author_values = sorted({str(author) for author in authors or [] if str(author or "").strip()})
            author_day_query: dict[str, Any] = {"date": {"$in": date_values}}

            if author_values:
                author_day_query["rawAuthor"] = {"$in": author_values}

            self.db.activity_author_day_summary_snapshots.delete_many(author_day_query)
            self.db.activity_day_summary_snapshots.delete_many({"date": {"$in": date_values}})
            return

        self.db.activity_author_day_summary_snapshots.delete_many({})
        self.db.activity_day_summary_snapshots.delete_many({})

    def _activity_snapshot_processing_state(self, now: dt.datetime | None = None) -> dict[str, str] | None:
        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        doc = self.db.activity_snapshot_maintenance_state.find_one(
            {"kind": "author-day", "snapshotVersion": version},
            {"_id": 0, "date": 1, "rawAuthor": 1, "startedAt": 1},
        )

        if not doc or not doc.get("date") or not doc.get("rawAuthor"):
            return None

        started_at = _coerce_datetime(doc.get("startedAt"))
        if started_at and (now - started_at).total_seconds() > self.ACTIVITY_SNAPSHOT_STALE_LOCK_SECONDS:
            self.db.activity_snapshot_maintenance_state.delete_many({"kind": "author-day", "snapshotVersion": version})
            return None

        return {
            "date": str(doc.get("date")),
            "rawAuthor": str(doc.get("rawAuthor")),
            "startedAt": _iso(doc.get("startedAt")),
        }

    def _next_completed_author_day_snapshot_candidate(
        self,
        now: dt.datetime | None = None,
        exclude: set[tuple[str, str]] | None = None,
    ) -> dict[str, str] | None:
        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        candidates: set[tuple[str, str]] = set()
        excluded_candidates = exclude or set()

        for item in self.db.daily_author_activity.find({}, {"_id": 0, "author": 1, "date": 1, "timeZoneId": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            raw_author = composed(self).resolve_author_alias(source_raw_author)
            day_date = str(item.get("date") or "")

            if not raw_author or not day_date:
                continue

            if self._is_author_day_live(raw_author, day_date, now, item.get("timeZoneId")):
                continue

            if self.db.activity_author_day_summary_snapshots.find_one(
                {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                continue

            if (day_date, raw_author) in excluded_candidates:
                continue

            candidates.add((day_date, raw_author))

        if not candidates:
            return None

        day_date = sorted({date for date, _author in candidates}, reverse=True)[0]
        raw_author = sorted(author for date, author in candidates if date == day_date)[0]
        return {"date": day_date, "rawAuthor": raw_author}

    def _is_completed_day_snapshot_ready(self, day_date: str, now: dt.datetime) -> bool:
        version = self.activity_day_summary_snapshot_version()
        required_authors = self._snapshot_authors_for_date(day_date)
        input_authors = set(self._snapshot_input_authors_for_date(day_date))

        if not required_authors:
            return False

        for raw_author in required_authors:
            if raw_author not in input_authors:
                continue

            if self._is_author_day_live(raw_author, day_date, now):
                return False

            if not self.db.activity_author_day_summary_snapshots.find_one(
                {"date": day_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                return False

        return True

    def activity_day_summary_snapshot_status(self, day_date: str, now: dt.datetime | None = None) -> dict[str, Any]:
        snapshot_date = str(day_date or "").strip()
        now = now or dt.datetime.now(dt.UTC)
        version = self.activity_day_summary_snapshot_version()
        required_authors = self._snapshot_authors_for_date(snapshot_date)
        input_authors = set(self._snapshot_input_authors_for_date(snapshot_date))
        ready_authors: list[str] = []
        live_authors: list[str] = []

        for raw_author in required_authors:
            if self._is_author_day_live(raw_author, snapshot_date, now):
                live_authors.append(raw_author)
                continue

            if self.db.activity_author_day_summary_snapshots.find_one(
                {"date": snapshot_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                ready_authors.append(raw_author)

        ready = set(ready_authors)
        live = set(live_authors)
        return {
            "readyAuthors": sorted(ready_authors),
            "pendingAuthors": [author for author in required_authors if author in input_authors and author not in ready and author not in live],
            "liveAuthors": sorted(live_authors),
        }

    def activity_day_has_summary_inputs(self, day_date: str) -> bool:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            return False

        for collection_name in (
            "daily_author_activity",
            "raw_activity_events",
            "activity_snapshots",
            "report_rows",
            "break_events",
            "break_intervals",
            "meeting_events",
            "meeting_intervals",
            "status_events",
        ):
            collection = getattr(self.db, collection_name, None)

            if collection is not None and collection.count_documents({"date": snapshot_date}) > 0:
                return True

        return False

    def _compose_activity_day_snapshot_payload(self, day_date: str, required_authors: list[str], version: int) -> dict[str, Any]:
        docs = list(
            self.db.activity_author_day_summary_snapshots.find(
                {"date": day_date, "rawAuthor": {"$in": required_authors}, "snapshotVersion": version},
                {"_id": 0, "rawAuthor": 1, "payload": 1},
            )
        )
        docs_by_author = {str(doc.get("rawAuthor") or ""): doc for doc in docs}
        totals = {
            "daySeconds": 0,
            "telegramDaySeconds": 0,
            "pluginDaySeconds": 0,
            "rawPluginDaySeconds": 0,
            "telegramToFirstActivitySeconds": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "overtimeActiveSeconds": 0,
            "breakSeconds": 0,
        }
        activity_counts: dict[str, int] = {}
        overtime_activity_counts: dict[str, int] = {}
        saved_prefabs: dict[str, dict[str, Any]] = {}
        overtime_saved_prefabs: dict[str, dict[str, Any]] = {}
        authors: list[dict[str, Any]] = []
        hourly: list[dict[str, Any]] = []

        def merge_counts(target: dict[str, int], values: list[dict[str, Any]]) -> None:
            for value in values or []:
                key = str(value.get("type") or "")
                if key:
                    target[key] = target.get(key, 0) + int(value.get("count", 0))

        def merge_saved(target: dict[str, dict[str, Any]], values: list[dict[str, Any]]) -> None:
            for value in values or []:
                path = str(value.get("path") or "")
                if not path:
                    continue
                if path in target:
                    target[path]["saveCount"] = int(target[path].get("saveCount", 0)) + int(value.get("saveCount", 0))
                else:
                    target[path] = dict(value)

        for raw_author in required_authors:
            payload = dict((docs_by_author.get(raw_author) or {}).get("payload") or {})
            author = dict(payload.get("author") or {})
            if author:
                authors.append(author)
            hourly_payload = dict(payload.get("hourlyActivity") or {})
            if hourly_payload:
                hourly.append(hourly_payload)
            for key in totals:
                totals[key] += int((payload.get("totals") or {}).get(key, 0))
            merge_counts(activity_counts, payload.get("activityCounts", []))
            merge_counts(overtime_activity_counts, payload.get("overtimeActivityCounts", []))
            merge_saved(saved_prefabs, payload.get("savedPrefabs", []))
            merge_saved(overtime_saved_prefabs, payload.get("overtimeSavedPrefabs", []))

        existing_authors = {str(author.get("rawAuthor") or "") for author in authors}
        for raw_author, profile in composed(self)._profiles_by_raw_author().items():
            if not raw_author or raw_author in existing_authors:
                continue

            if _is_device_profile_raw_author(raw_author):
                continue

            if str(profile.get("profileType") or "person") == "publisher":
                continue

            empty_author = self._empty_completed_day_author_row(raw_author, profile)
            authors.append(empty_author)
            hourly.append(
                {
                    "author": empty_author["displayName"],
                    "rawAuthor": raw_author,
                    "timeZoneId": profile.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                    "hourlyActivity": empty_hourly_activity(),
                }
            )
            existing_authors.add(raw_author)

        return {
            "totals": totals,
            "activityMix": sorted(_activity_mix_from_counts(activity_counts), key=lambda item: item["count"], reverse=True),
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "overtimeActivityMix": sorted(_activity_mix_from_counts(overtime_activity_counts), key=lambda item: item["count"], reverse=True),
            "overtimeSavedPrefabs": sorted(overtime_saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "authors": sorted(authors, key=lambda item: str(item.get("displayName") or "").lower()),
            "profiles": [],
            "authorAliases": [],
            "hourlyActivityByAuthor": sorted(hourly, key=lambda item: str(item.get("author") or "")),
        }

    def _compose_activity_author_day_snapshot_payload(
        self,
        day_date: str,
        required_authors: list[str],
        docs_by_author: dict[str, dict[str, Any]],
        *,
        ready_authors: list[str],
        pending_authors: list[str],
        live_authors: list[str],
    ) -> dict[str, Any]:
        profiles = composed(self)._profiles_by_raw_author()
        totals = {
            "daySeconds": 0,
            "telegramDaySeconds": 0,
            "pluginDaySeconds": 0,
            "rawPluginDaySeconds": 0,
            "telegramToFirstActivitySeconds": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "overtimeActiveSeconds": 0,
            "breakSeconds": 0,
        }
        activity_counts: dict[str, int] = {}
        overtime_activity_counts: dict[str, int] = {}
        saved_prefabs: dict[str, dict[str, Any]] = {}
        overtime_saved_prefabs: dict[str, dict[str, Any]] = {}
        authors: list[dict[str, Any]] = []
        hourly: list[dict[str, Any]] = []

        def merge_counts(target: dict[str, int], values: list[dict[str, Any]]) -> None:
            for value in values or []:
                key = str(value.get("type") or "")
                if key:
                    target[key] = target.get(key, 0) + int(value.get("count", 0))

        def merge_saved(target: dict[str, dict[str, Any]], values: list[dict[str, Any]]) -> None:
            for value in values or []:
                path = str(value.get("path") or "")
                if not path:
                    continue
                if path in target:
                    target[path]["saveCount"] = int(target[path].get("saveCount", 0)) + int(value.get("saveCount", 0))
                else:
                    target[path] = dict(value)

        for raw_author in ready_authors:
            payload = docs_by_author.get(raw_author) or {}
            author = dict(payload.get("author") or {})
            if author:
                author["snapshotStatus"] = "ready"
                authors.append(author)

            hourly_value = payload.get("hourlyActivity")
            if isinstance(hourly_value, dict):
                hourly_payload = dict(hourly_value)
            elif isinstance(hourly_value, list):
                profile = profiles.get(raw_author, {})
                hourly_payload = {
                    "author": author.get("displayName") or _display_name(raw_author, profile),
                    "rawAuthor": raw_author,
                    "timeZoneId": profile.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                    "hourlyActivity": hourly_value,
                }
            else:
                hourly_payload = {}
            if hourly_payload:
                hourly.append(hourly_payload)

            for key in totals:
                totals[key] += int((payload.get("totals") or {}).get(key, 0))
            merge_counts(activity_counts, payload.get("activityCounts", []))
            merge_counts(overtime_activity_counts, payload.get("overtimeActivityCounts", []))
            merge_saved(saved_prefabs, payload.get("savedPrefabs", []))
            merge_saved(overtime_saved_prefabs, payload.get("overtimeSavedPrefabs", []))

        for raw_author in sorted(set(required_authors) - set(ready_authors)):
            profile = profiles.get(raw_author, {})
            author = self._empty_completed_day_author_row(raw_author, profile)
            if raw_author in live_authors:
                author["snapshotStatus"] = "live"
            elif raw_author in pending_authors:
                author["snapshotStatus"] = "preparing"
            else:
                author["snapshotStatus"] = "ready"
            authors.append(author)
            hourly.append(
                {
                    "author": author["displayName"],
                    "rawAuthor": raw_author,
                    "timeZoneId": profile.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                    "hourlyActivity": empty_hourly_activity(),
                }
            )

        return {
            "totals": totals,
            "activityMix": sorted(_activity_mix_from_counts(activity_counts), key=lambda item: item["count"], reverse=True),
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "overtimeActivityMix": sorted(_activity_mix_from_counts(overtime_activity_counts), key=lambda item: item["count"], reverse=True),
            "overtimeSavedPrefabs": sorted(overtime_saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "authors": sorted(authors, key=lambda item: str(item.get("displayName") or "").lower()),
            "profiles": [],
            "authorAliases": [],
            "hourlyActivityByAuthor": sorted(hourly, key=lambda item: str(item.get("author") or "")),
            "snapshot": {
                "hit": bool(ready_authors),
                "partial": bool(pending_authors or live_authors),
                "status": "partial" if ready_authors and (pending_authors or live_authors) else ("ready" if ready_authors else "preparing"),
                "date": day_date,
                "readyAuthors": sorted(ready_authors),
                "pendingAuthors": sorted(pending_authors),
                "preparingAuthors": sorted(pending_authors),
                "liveAuthors": sorted(live_authors),
            },
        }

    def _reports_page_for_author_day_snapshot(self, day_date: str, raw_author: str) -> dict[str, Any]:
        return self.reports_page(
            start_date=day_date,
            end_date=day_date,
            date_mode=None,
            author=raw_author,
            limit=self.ACTIVITY_SNAPSHOT_REPORTS_LIMIT,
            offset=0,
            use_snapshots=False,
        )

    def _completed_snapshot_candidate_authors_for_date(self, day_date: str, now: dt.datetime) -> list[str]:
        authors: set[str] = set()

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1, "timeZoneId": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            raw_author = composed(self).resolve_author_alias(source_raw_author)

            if not raw_author:
                continue

            if not self._is_author_day_live(raw_author, day_date, now, item.get("timeZoneId")):
                authors.add(raw_author)

        return sorted(authors)

    def _completed_snapshot_candidate_dates_for_author(self, raw_author: str, now: dt.datetime) -> list[str]:
        version = self.activity_day_summary_snapshot_version()
        resolved_author = composed(self).resolve_author_alias(str(raw_author or "Unknown User"))
        dates: set[str] = set()

        for item in self.db.daily_author_activity.find({}, {"_id": 0, "author": 1, "date": 1, "timeZoneId": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            if composed(self).resolve_author_alias(source_raw_author) != resolved_author:
                continue

            day_date = str(item.get("date") or "")
            if not day_date:
                continue

            if self._is_author_day_live(resolved_author, day_date, now, item.get("timeZoneId")):
                continue

            if self.db.activity_author_day_summary_snapshots.find_one(
                {"date": day_date, "rawAuthor": resolved_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                continue

            dates.add(day_date)

        return sorted(dates, reverse=True)

    def _snapshot_authors_for_date(self, day_date: str) -> list[str]:
        authors: set[str] = set()

        for item in self._empty_completed_day_authors():
            raw_author = str(item.get("rawAuthor") or "")
            if raw_author:
                authors.add(raw_author)

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            raw_author = composed(self).resolve_author_alias(source_raw_author)

            if raw_author:
                authors.add(raw_author)

        return sorted(authors)

    def _snapshot_input_authors_for_date(self, day_date: str) -> list[str]:
        authors: set[str] = set()

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            raw_author = composed(self).resolve_author_alias(source_raw_author)

            if raw_author:
                authors.add(raw_author)

        return sorted(authors)

    def _snapshot_status_authors_for_date(self, day_date: str, now: dt.datetime) -> list[dict[str, Any]]:
        profiles = composed(self)._profiles_by_raw_author()
        authors: dict[str, dict[str, Any]] = {}

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1, "timeZoneId": 1}):
            source_raw_author = str(item.get("author") or "Unknown User")
            raw_author = composed(self).resolve_author_alias(source_raw_author)

            if not raw_author:
                continue

            profile = profiles.get(raw_author, {})
            authors[raw_author] = {
                "rawAuthor": raw_author,
                "displayName": _display_name(raw_author, profile),
                "timeZoneId": profile.get("timeZoneId") or item.get("timeZoneId") or "",
                "live": self._is_author_day_live(raw_author, day_date, now, item.get("timeZoneId")),
            }

        return sorted(authors.values(), key=lambda item: (str(item["displayName"]).lower(), str(item["rawAuthor"])))

    def _empty_completed_day_authors(self) -> list[dict[str, Any]]:
        authors: list[dict[str, Any]] = []

        for raw_author, profile in composed(self)._profiles_by_raw_author().items():
            if not raw_author:
                continue

            if _is_device_profile_raw_author(raw_author):
                continue

            if str(profile.get("profileType") or "person") == "publisher":
                continue

            authors.append(self._empty_completed_day_author_row(raw_author, profile))

        return sorted(authors, key=lambda item: (str(item.get("displayName") or "").lower(), str(item.get("rawAuthor") or "")))

    def _empty_completed_day_author_row(self, raw_author: str, profile: dict[str, Any]) -> dict[str, Any]:
        avatar_url = _cached_author_avatar_api_url(raw_author, _github_username_for_avatar_fetch(raw_author, profile), profile)
        return {
            "rawAuthor": raw_author,
            "authorEmail": profile.get("authorEmail", ""),
            "displayName": _display_name(raw_author, profile),
            "team": profile.get("team", ""),
            "profileType": profile.get("profileType") or "person",
            "telegramUsername": profile.get("telegramUsername", ""),
            "telegramPrivateChatId": profile.get("telegramPrivateChatId"),
            "discordUserId": profile.get("discordUserId", ""),
            "discordUsername": profile.get("discordUsername", ""),
            "autoBreakEnabled": profile.get("autoBreakEnabled", False),
            "authorColor": profile.get("authorColor") or _author_color(raw_author),
            "avatarUrl": avatar_url,
            "source": None,
            "pluginVersion": None,
            "timeZoneId": profile.get("timeZoneId"),
            "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
            "lastRecordedAt": "",
            "lastReceivedAt": "",
            "daySeconds": 0,
            "telegramDaySeconds": 0,
            "pluginDaySeconds": 0,
            "rawPluginDaySeconds": 0,
            "telegramToFirstActivitySeconds": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "productivity": 0,
            "activityCounts": [],
            "activityMix": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "status": "stale",
            "stalePresence": "telegram",
        }

    def _is_author_day_live(self, raw_author: str, day_date: str, now: dt.datetime, fallback_time_zone_id: Any = None) -> bool:
        profiles = composed(self)._profiles_by_raw_author()
        local_today = _local_date_for_time_zone(now, _author_time_zone_id(raw_author, profiles, fallback_time_zone_id))
        return day_date == local_today

    def _author_day_payload_from_summary(self, payload: dict[str, Any], raw_author: str) -> dict[str, Any]:
        authors = [item for item in payload.get("authors", []) if item.get("rawAuthor") == raw_author]
        hourly = [item for item in payload.get("hourlyActivityByAuthor", []) if item.get("rawAuthor") == raw_author]
        author = authors[0] if authors else {}
        return {
            "author": author,
            "hourlyActivity": hourly[0] if hourly else {"rawAuthor": raw_author, "hourlyActivity": empty_hourly_activity()},
            "totals": {
                "daySeconds": int(author.get("daySeconds", 0)),
                "telegramDaySeconds": int(author.get("telegramDaySeconds", 0)),
                "pluginDaySeconds": int(author.get("pluginDaySeconds", 0)),
                "rawPluginDaySeconds": int(author.get("rawPluginDaySeconds", 0)),
                "telegramToFirstActivitySeconds": int(author.get("telegramToFirstActivitySeconds", 0)),
                "activeSeconds": int(author.get("activeSeconds", 0)),
                "idleSeconds": int(author.get("idleSeconds", 0)),
                "meetingSeconds": int(author.get("meetingSeconds", 0)),
                "breakSeconds": int(author.get("breakSeconds", 0)),
                "overtimeActiveSeconds": int(author.get("overtimeActiveSeconds", 0)),
            },
            "activityCounts": author.get("activityCounts", []),
            "savedPrefabs": author.get("savedPrefabs", []),
            "overtimeActivityCounts": author.get("overtimeActivityCounts", []),
            "overtimeSavedPrefabs": author.get("overtimeSavedPrefabs", []),
        }

    def snapshot_reports_page(
        self,
        *,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        author: str | None,
        source: str | None = None,
        hour: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any] | None:
        if date_mode or not start_date or start_date != end_date or not author:
            return None

        if source or _normalize_report_hour_filter(hour) is not None or int(offset or 0) != 0:
            return None

        try:
            dt.date.fromisoformat(str(start_date))
        except ValueError:
            return None

        version = self.activity_day_summary_snapshot_version()
        raw_author = composed(self).resolve_author_alias(author)
        doc = self.db.activity_author_day_summary_snapshots.find_one(
            {"date": start_date, "rawAuthor": raw_author, "snapshotVersion": version},
            {"_id": 0, "payload.reportsPage": 1},
        )
        reports_page = (((doc or {}).get("payload") or {}).get("reportsPage") or None)

        if not reports_page:
            return None

        snapshot_limit = max(1, int(reports_page.get("limit", len(reports_page.get("reports") or [])) or 1))
        requested_limit = max(1, min(int(limit), 200))

        if requested_limit > snapshot_limit:
            return None

        reports = list(reports_page.get("reports") or [])
        total = int(reports_page.get("total", len(reports)))
        offset = 0
        limit = requested_limit
        page_reports = reports[offset : offset + limit]

        return {
            "reports": page_reports,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sources": list(reports_page.get("sources") or []),
            "snapshot": {"hit": True, "date": start_date, "rawAuthor": raw_author},
        }

    def _activity_day_summary_snapshot_date_for_request(
        self,
        *,
        view: str,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        include_profiles: bool,
        include_hourly: bool,
        include_breakdowns: bool,
        now: dt.datetime,
    ) -> str | None:
        if view not in {"activity", "activity-hourly"}:
            return None

        if date_mode:
            return None

        if include_profiles:
            return None

        if start_date != end_date or not start_date:
            return None

        try:
            requested_date = dt.date.fromisoformat(start_date)
        except ValueError:
            return None

        status_authors = self._snapshot_status_authors_for_date(start_date, now)
        if status_authors and all(item["live"] for item in status_authors):
            return None

        if not status_authors:
            live_dates = set()
            for profile in composed(self)._profiles_by_raw_author().values():
                profile_time_zone_id = _valid_time_zone_id(profile.get("timeZoneId"))
                if profile_time_zone_id:
                    live_dates.add(_local_date_for_time_zone(now, profile_time_zone_id))

            if start_date in live_dates:
                return None

        if view == "activity" and (not include_hourly or not include_breakdowns):
            return None

        if view == "activity-hourly" and (not include_hourly or include_breakdowns):
            return None

        return start_date
