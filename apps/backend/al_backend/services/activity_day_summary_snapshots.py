from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed
from ..hourly_fill_rules import empty_hourly_activity


class ActivityDaySummarySnapshotsMixin:
    ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW = "activity-day"
    ACTIVITY_AUTHOR_DAY_SUMMARY_MAINTENANCE_LIMIT = 3

    def activity_day_summary_snapshot_version(self) -> int:
        return int(getattr(composed(self), "aggregates_version", 0))

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

        doc = self.db.activity_day_summary_snapshots.find_one(
            {
                "date": snapshot_date,
                "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                "snapshotVersion": self.activity_day_summary_snapshot_version(),
            },
            {"_id": 0, "payload": 1},
        )

        return snapshot_date, doc

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

        stored_payload = {key: value for key, value in payload.items() if key not in {"cache", "snapshot"}}
        self.db.activity_day_summary_snapshots.update_one(
            {
                "date": snapshot_date,
                "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                "snapshotVersion": self.activity_day_summary_snapshot_version(),
            },
            {
                "$set": {
                    "date": snapshot_date,
                    "view": self.ACTIVITY_DAY_SUMMARY_SNAPSHOT_VIEW,
                    "snapshotVersion": self.activity_day_summary_snapshot_version(),
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
                return {"processed": processed, "remaining": False, **result}

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

    def materialize_next_completed_author_day_snapshot(self, now: dt.datetime | None = None) -> dict[str, Any]:
        candidate = self._next_completed_author_day_snapshot_candidate(now=now)

        if not candidate:
            return {"processed": False, "reason": "no_completed_author_day_without_snapshot"}

        raw_author = str(candidate["rawAuthor"])
        day_date = str(candidate["date"])
        version = self.activity_day_summary_snapshot_version()
        self.db.activity_snapshot_maintenance_state.update_one(
            {"kind": "author-day"},
            {
                "$set": {
                    "kind": "author-day",
                    "date": day_date,
                    "rawAuthor": raw_author,
                    "snapshotVersion": version,
                    "startedAt": dt.datetime.now(dt.UTC),
                }
            },
            upsert=True,
        )

        try:
            payload = self.activity_summary(
                start_date=day_date,
                end_date=day_date,
                date_mode=None,
                now=now,
                include_profiles=False,
                include_hourly=True,
                include_breakdowns=True,
            )
            author_payload = self._author_day_payload_from_summary(payload, raw_author)
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
            composed_payload = self.compose_completed_day_snapshot(day_date)
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
        processing = self._activity_snapshot_processing_state()
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

    def compose_completed_day_snapshot(self, day_date: str) -> dict[str, Any] | None:
        snapshot_date = str(day_date or "").strip()

        if not snapshot_date:
            return None

        required_authors = self._completed_snapshot_candidate_authors_for_date(snapshot_date, dt.datetime.now(dt.UTC))
        version = self.activity_day_summary_snapshot_version()

        for raw_author in required_authors:
            if not self.db.activity_author_day_summary_snapshots.find_one(
                {"date": snapshot_date, "rawAuthor": raw_author, "snapshotVersion": version},
                {"_id": 1},
            ):
                return None

        payload = self.build_activity_day_summary_snapshot(snapshot_date)
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
                )
                author_payload = self._author_day_payload_from_summary(payload, raw_author)
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

            if self.compose_completed_day_snapshot(day_date) is not None:
                composed_dates.append(day_date)

        return {"processed": processed, "composedDates": composed_dates}

    def invalidate_activity_day_summary_snapshots(
        self,
        dates: list[str] | tuple[str, ...] | set[str] | None = None,
        authors: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> None:
        if dates:
            date_values = sorted({str(day) for day in dates if str(day or "").strip()})

            if date_values:
                author_values = sorted({str(author) for author in authors or [] if str(author or "").strip()})
                author_day_query: dict[str, Any] = {"date": {"$in": date_values}}

                if author_values:
                    author_day_query["rawAuthor"] = {"$in": author_values}

                self.db.activity_author_day_summary_snapshots.delete_many(author_day_query)
                self.db.activity_day_summary_snapshots.delete_many({"date": {"$in": date_values}})
                return

        self.db.activity_author_day_summary_snapshots.delete_many({})
        self.db.activity_day_summary_snapshots.delete_many({})

    def _activity_snapshot_processing_state(self) -> dict[str, str] | None:
        version = self.activity_day_summary_snapshot_version()
        doc = self.db.activity_snapshot_maintenance_state.find_one(
            {"kind": "author-day", "snapshotVersion": version},
            {"_id": 0, "date": 1, "rawAuthor": 1, "startedAt": 1},
        )

        if not doc or not doc.get("date") or not doc.get("rawAuthor"):
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
            raw_author = composed(self).resolve_author_alias(item.get("author") or "Unknown User")
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

        day_date, raw_author = sorted(candidates)[0]
        return {"date": day_date, "rawAuthor": raw_author}

    def _completed_snapshot_candidate_authors_for_date(self, day_date: str, now: dt.datetime) -> list[str]:
        authors: set[str] = set()

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1, "timeZoneId": 1}):
            raw_author = composed(self).resolve_author_alias(item.get("author") or "Unknown User")

            if raw_author and not self._is_author_day_live(raw_author, day_date, now, item.get("timeZoneId")):
                authors.add(raw_author)

        return sorted(authors)

    def _snapshot_status_authors_for_date(self, day_date: str, now: dt.datetime) -> list[dict[str, Any]]:
        profiles = composed(self)._profiles_by_raw_author()
        authors: dict[str, dict[str, Any]] = {}

        for item in self.db.daily_author_activity.find({"date": day_date}, {"_id": 0, "author": 1, "timeZoneId": 1}):
            raw_author = composed(self).resolve_author_alias(item.get("author") or "Unknown User")

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

        live_dates = {now.astimezone(dt.UTC).date().isoformat()}

        for profile in composed(self)._profiles_by_raw_author().values():
            live_dates.add(
                _local_date_for_time_zone(
                    now,
                    _author_time_zone_id(profile.get("rawAuthor"), {}, profile.get("timeZoneId")),
                )
            )

        if start_date in live_dates or requested_date > now.astimezone(dt.UTC).date():
            return None

        if view == "activity" and (not include_hourly or not include_breakdowns):
            return None

        if view == "activity-hourly" and (not include_hourly or include_breakdowns):
            return None

        return start_date
