from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class ActivitySummaryService(MongoComposableMixin):
    SUMMARY_CACHE_TTL_SECONDS = 5
    REPORTS_PAGE_SCAN_MULTIPLIER = 5
    REPORTS_PAGE_SCAN_FLOOR = 50
    REPORTS_PAGE_SCAN_CEILING = 1000

    def latest_reports(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        author: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        composed(self).materialize_live_meeting_reports()
        reports = []
        projection = {
            "_id": 0,
            "rawReportId": 0,
            "encryptedPacket": 0,
            "activityCounts": 0,
            "savedPrefabs": 0,
            "overtimeActivityCounts": 0,
            "overtimeSavedPrefabs": 0,
            "hourlyActivity": 0,
            "activityCountDeltas": 0,
            "savedPrefabDeltas": 0,
            "overtimeActivityCountDeltas": 0,
            "overtimeSavedPrefabDeltas": 0,
            "hourlyActivityDelta": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activeMicroseconds": 0,
            "idleMicroseconds": 0,
            "overtimeActiveMicroseconds": 0,
            "activeDeltaMicroseconds": 0,
            "idleDeltaMicroseconds": 0,
            "overtimeActiveDeltaMicroseconds": 0,
            "firstActivity": 0,
            "lastActivity": 0,
            "idleThresholdSeconds": 0,
            "workWindowSeconds": 0,
        }

        profiles = composed(self)._profiles_by_raw_author()
        now = dt.datetime.now(dt.UTC)
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        if author:
            query["author"] = {"$in": composed(self).author_alias_keys(author)}

        if source:
            query["source"] = source

        report_rows = list(
            self.db.report_rows.find(query, projection).sort(
                [("receivedAt", DESCENDING), ("recordedAt", DESCENDING), ("batchId", DESCENDING)]
            )
        )
        meeting_lookup = self._meeting_lookup_for_reports(report_rows)
        break_lookup = self._break_lookup_for_reports(report_rows)

        for item in report_rows:
            if date_mode == "authorLocalToday" and not _is_live_date_match(
                item.get("date"),
                item.get("author") or "Unknown User",
                profiles,
                item.get("timeZoneId"),
                now,
                start_date,
                end_date,
            ):
                continue

            if (
                self._is_empty_plugin_report_without_signal(item)
                or self._is_idle_report_during_meeting(item, meeting_lookup)
                or self._is_idle_report_during_break(item, break_lookup)
            ):
                continue

            profile = profiles.get(item.get("author") or "Unknown User", {})
            item["displayName"] = _display_name(item.get("author"), profile)
            item["team"] = profile.get("team", "")
            item["receivedAt"] = _iso(item.get("receivedAt"))
            reports.append(item)

            if limit is not None and len(reports) >= limit:
                break

        return reports

    def _reports_query_context(
        self,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        author: str | None = None,
        source: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dt.datetime]:
        profiles = composed(self)._profiles_by_raw_author()
        now = dt.datetime.now(dt.UTC)
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        if author:
            query["author"] = {"$in": composed(self).author_alias_keys(author)}

        if source:
            query["source"] = source

        return query, profiles, now

    def _reports_projection(self) -> dict[str, int]:
        return {
            "_id": 0,
            "rawReportId": 0,
            "encryptedPacket": 0,
            "activityCounts": 0,
            "savedPrefabs": 0,
            "overtimeActivityCounts": 0,
            "overtimeSavedPrefabs": 0,
            "hourlyActivity": 0,
            "activityCountDeltas": 0,
            "savedPrefabDeltas": 0,
            "overtimeActivityCountDeltas": 0,
            "overtimeSavedPrefabDeltas": 0,
            "hourlyActivityDelta": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activeMicroseconds": 0,
            "idleMicroseconds": 0,
            "overtimeActiveMicroseconds": 0,
            "activeDeltaMicroseconds": 0,
            "idleDeltaMicroseconds": 0,
            "overtimeActiveDeltaMicroseconds": 0,
            "firstActivity": 0,
            "lastActivity": 0,
            "idleThresholdSeconds": 0,
            "workWindowSeconds": 0,
        }

    def reports_page(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        author: str | None = None,
        source: str | None = None,
        hour: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        hour = _normalize_report_hour_filter(hour)
        composed(self).materialize_live_meeting_reports()
        query, profiles, now = self._reports_query_context(start_date, end_date, date_mode, author, source)
        source_query, _, _ = self._reports_query_context(start_date, end_date, date_mode, author)
        sources = sorted(
            {str(item or "") for item in self.db.report_rows.distinct("source", source_query)},
            key=lambda value: value.lower(),
        )
        status_rows = list(self.db.report_rows.find({**source_query, "source": "status"}, self._reports_projection()))
        status_intervals = self._status_intervals_for_reports(status_rows)
        candidate_rows = self._bounded_report_rows_for_page(query, self._reports_projection(), offset, limit, status_intervals)
        meeting_lookup = self._meeting_lookup_for_reports(candidate_rows)
        break_lookup = self._break_lookup_for_reports(candidate_rows)
        reports: list[dict[str, Any]] = []
        total = 0

        for item in candidate_rows:
            if date_mode == "authorLocalToday" and not _is_live_date_match(
                item.get("date"),
                item.get("author") or "Unknown User",
                profiles,
                item.get("timeZoneId"),
                now,
                start_date,
                end_date,
            ):
                continue

            if not _report_matches_hour_filter(item, profiles, hour):
                continue

            if self._is_report_inside_status_interval(item, status_intervals):
                continue

            if (
                self._is_empty_plugin_report_without_signal(item)
                or self._is_idle_report_during_meeting(item, meeting_lookup)
                or self._is_idle_report_during_break(item, break_lookup)
            ):
                continue

            if total >= offset and len(reports) < limit:
                resolved_author = composed(self).resolve_author_alias(item.get("author") or "Unknown User")
                profile = profiles.get(resolved_author, {})
                item["displayName"] = _display_name(resolved_author, profile)
                item["team"] = profile.get("team", "")
                item["timeZoneId"] = profile.get("timeZoneId") or item.get("timeZoneId")
                item["timeZoneDisplayName"] = profile.get("timeZoneDisplayName") or item.get("timeZoneDisplayName")
                item["receivedAt"] = _iso(item.get("receivedAt"))
                reports.append(item)

            total += 1

        if not self._reports_page_uses_post_filters(hour, status_intervals, candidate_rows, meeting_lookup, break_lookup):
            total = self.db.report_rows.count_documents(query)

        return {
            "reports": reports,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sources": sources,
        }

    def _bounded_report_rows_for_page(
        self,
        query: dict[str, Any],
        projection: dict[str, int],
        offset: int,
        limit: int,
        status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]],
    ) -> list[dict[str, Any]]:
        scan_limit = min(
            self.REPORTS_PAGE_SCAN_CEILING,
            max(self.REPORTS_PAGE_SCAN_FLOOR, offset + limit * self.REPORTS_PAGE_SCAN_MULTIPLIER),
        )
        cursor = self.db.report_rows.find(query, projection).sort(
            [("receivedAt", DESCENDING), ("recordedAt", DESCENDING), ("batchId", DESCENDING)]
        )
        limit_method = getattr(cursor, "limit", None)

        if limit_method:
            cursor = limit_method(scan_limit)

        rows = list(cursor)
        rows.sort(key=lambda row: _report_table_sort_key(row, status_intervals), reverse=True)
        return rows

    def _reports_page_uses_post_filters(
        self,
        hour: int | None,
        status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]],
        candidate_rows: list[dict[str, Any]],
        meeting_lookup: dict[str, dict[str, list[dict[str, Any]]]],
        break_lookup: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> bool:
        if hour is not None or status_intervals:
            return True

        return any(
            self._is_empty_plugin_report_without_signal(row)
            or self._is_idle_report_during_meeting(row, meeting_lookup)
            or self._is_idle_report_during_break(row, break_lookup)
            for row in candidate_rows
        )

    def cached_activity_summary(
        self,
        *,
        view: str,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        include_profiles: bool = True,
        include_hourly: bool = True,
        include_breakdowns: bool = True,
    ) -> dict[str, Any]:
        composed(self).materialize_live_meeting_reports()
        now = dt.datetime.now(dt.UTC)
        cache_key = self._activity_summary_cache_key(
            view,
            start_date,
            end_date,
            date_mode,
            include_profiles,
            include_hourly,
            include_breakdowns,
            now,
        )
        cached = self.db.activity_summary_cache.find_one(
            {"cacheKey": cache_key, "expiresAt": {"$gt": now}},
            {"_id": 0, "payload": 1},
        )

        if cached:
            payload = dict(cached.get("payload") or {})
            payload["cache"] = {"hit": True, "key": cache_key}
            return payload

        payload = self.activity_summary(
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
            now=now,
            include_profiles=include_profiles,
            include_hourly=include_hourly,
            include_breakdowns=include_breakdowns,
        )
        expires_at = now + dt.timedelta(seconds=self.SUMMARY_CACHE_TTL_SECONDS)
        self.db.activity_summary_cache.update_one(
            {"cacheKey": cache_key},
            {
                "$set": {
                    "cacheKey": cache_key,
                    "view": view,
                    "startDate": start_date or "",
                    "endDate": end_date or "",
                    "dateMode": date_mode or "",
                    "payload": payload,
                    "createdAt": now,
                    "expiresAt": expires_at,
                }
            },
            upsert=True,
        )
        payload = dict(payload)
        payload["cache"] = {"hit": False, "key": cache_key}
        return payload

    def invalidate_activity_summary_cache(self, dates: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        if dates:
            date_values = {str(day) for day in dates if str(day or "").strip()}
            self.db.activity_summary_cache.delete_many({"dates": {"$in": sorted(date_values)}})

        self.db.activity_summary_cache.delete_many({})

    def _activity_summary_cache_key(
        self,
        view: str,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        include_profiles: bool,
        include_hourly: bool,
        include_breakdowns: bool,
        now: dt.datetime,
    ) -> str:
        profiles = composed(self)._profiles_by_raw_author()
        scope_query = _report_date_query(start_date, end_date, date_mode, profiles, now)
        return "|".join(
            [
                view,
                start_date or "",
                end_date or "",
                date_mode or "",
                "profiles" if include_profiles else "no-profiles",
                "hourly" if include_hourly else "no-hourly",
                "breakdowns" if include_breakdowns else "no-breakdowns",
                repr(scope_query.get("date", "")),
            ]
        )

    def _status_intervals_for_reports(self, status_rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]]:
        intervals_by_key: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]] = {}
        ordered_rows = sorted(status_rows, key=lambda row: _report_sort_datetime(row) or dt.datetime.min.replace(tzinfo=dt.UTC))
        open_offline_by_key: dict[tuple[str, str], tuple[dt.datetime, str]] = {}

        for row in ordered_rows:
            if row.get("source") != "status" and row.get("reportType") != "status":
                continue

            raw_author = str(row.get("author") or "Unknown User")
            report_date = str(row.get("date") or "")
            event_type = str(row.get("statusEventType") or row.get("activityType") or "")
            reason = str(row.get("statusReason") or (row.get("metadata") or {}).get("reason") or "")
            sort_at = _report_sort_datetime(row)

            if not report_date or not sort_at:
                continue

            key = (raw_author, report_date)

            if event_type == "offline":
                open_offline_by_key[key] = (sort_at, reason)
                continue

            if event_type == "online":
                opened = open_offline_by_key.pop(key, None)

                if not opened:
                    continue

                opened_at, opened_reason = opened

                if opened_reason == "reports_stopped" and reason == "reports_resumed":
                    continue

                if opened_at:
                    intervals_by_key.setdefault(key, []).append((opened_at, sort_at))

        for key, (opened_at, _) in open_offline_by_key.items():
            intervals_by_key.setdefault(key, []).append((opened_at, None))

        return intervals_by_key

    def _is_report_inside_status_interval(
        self,
        report_row: dict[str, Any],
        status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]],
    ) -> bool:
        if report_row.get("source") == "status" or report_row.get("reportType") == "status":
            return False

        if report_row.get("source") == "telegram" and report_row.get("telegramEventType") == "online":
            return False

        raw_author = str(report_row.get("author") or "Unknown User")
        report_date = str(report_row.get("date") or "")
        sort_at = _report_sort_datetime(report_row)

        if not report_date or not sort_at:
            return False

        for opened_at, closed_at in status_intervals.get((raw_author, report_date), []):
            if sort_at <= opened_at:
                continue

            if closed_at is None:
                return True

            if sort_at >= closed_at:
                continue

            return True

        return False

    def _is_idle_report_during_break(
        self,
        report_row: dict[str, Any],
        break_lookup: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    ) -> bool:
        if report_row.get("source") in {"discord", "telegram"} or report_row.get("reportType") in {"meeting", "telegram"}:
            return False

        raw_author = report_row.get("author") or "Unknown User"
        recorded_at = _coerce_datetime(report_row.get("recordedAt") or report_row.get("lastRecordedAt") or report_row.get("receivedAt"))

        if not recorded_at:
            return False

        if break_lookup is not None:
            author_lookup = break_lookup.get(raw_author, {})

            for interval in author_lookup.get("intervals", []):
                started_at = interval.get("startedAt")
                ended_at = interval.get("endedAt")

                if started_at and ended_at and started_at <= recorded_at <= ended_at:
                    return True

            for session in author_lookup.get("sessions", []):
                started_at = session.get("startedAt")

                if started_at and started_at <= recorded_at:
                    return True

            return False

        if self.db.break_intervals.find_one(
            {"rawAuthor": raw_author, "startedAt": {"$lte": recorded_at}, "endedAt": {"$gte": recorded_at}},
            {"_id": 1},
        ):
            return True

        return bool(
            self.db.break_sessions.find_one(
                {"rawAuthor": raw_author, "startedAt": {"$lte": recorded_at}},
                {"_id": 1},
            )
        )

    def _is_idle_report_during_meeting(
        self,
        report_row: dict[str, Any],
        meeting_lookup: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    ) -> bool:
        if report_row.get("source") in {"discord", "telegram"} or report_row.get("reportType") in {"meeting", "telegram"}:
            return False

        idle_seconds = int(report_row.get("idleDeltaSeconds", 0))
        active_seconds = int(report_row.get("activeDeltaSeconds", 0))
        overtime_seconds = int(report_row.get("overtimeActiveDeltaSeconds", 0))

        if idle_seconds <= 0 or active_seconds > 0 or overtime_seconds > 0:
            return False

        raw_author = report_row.get("author") or "Unknown User"
        recorded_at = _coerce_datetime(report_row.get("recordedAt") or report_row.get("lastRecordedAt") or report_row.get("receivedAt"))

        if not recorded_at:
            return False

        if meeting_lookup is not None:
            author_lookup = meeting_lookup.get(raw_author, {})

            for interval in author_lookup.get("intervals", []):
                started_at = interval.get("startedAt")
                ended_at = interval.get("endedAt")

                if started_at and ended_at and started_at <= recorded_at <= ended_at:
                    return True

            for session in author_lookup.get("sessions", []):
                started_at = session.get("startedAt")

                if started_at and started_at <= recorded_at:
                    return True

            return False

        if self.db.meeting_intervals.find_one(
            {"rawAuthor": raw_author, "startedAt": {"$lte": recorded_at}, "endedAt": {"$gte": recorded_at}},
            {"_id": 1},
        ):
            return True

        return bool(
            self.db.meeting_sessions.find_one(
                {"rawAuthor": raw_author, "startedAt": {"$lte": recorded_at}},
                {"_id": 1},
            )
        )

    def _meeting_lookup_for_reports(self, report_rows: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        candidate_times_by_author: dict[str, list[dt.datetime]] = {}

        for report_row in report_rows:
            if report_row.get("source") in {"discord", "telegram"} or report_row.get("reportType") in {"meeting", "telegram"}:
                continue

            idle_seconds = int(report_row.get("idleDeltaSeconds", 0))
            active_seconds = int(report_row.get("activeDeltaSeconds", 0))
            overtime_seconds = int(report_row.get("overtimeActiveDeltaSeconds", 0))

            if idle_seconds <= 0 or active_seconds > 0 or overtime_seconds > 0:
                continue

            recorded_at = _coerce_datetime(
                report_row.get("recordedAt") or report_row.get("lastRecordedAt") or report_row.get("receivedAt")
            )

            if not recorded_at:
                continue

            raw_author = str(report_row.get("author") or "Unknown User")
            candidate_times_by_author.setdefault(raw_author, []).append(recorded_at)

        if not candidate_times_by_author:
            return {}

        authors = sorted(candidate_times_by_author)
        min_recorded_at = min(min(values) for values in candidate_times_by_author.values())
        max_recorded_at = max(max(values) for values in candidate_times_by_author.values())
        lookup = {author: {"intervals": [], "sessions": []} for author in authors}

        for interval in self.db.meeting_intervals.find(
            {
                "rawAuthor": {"$in": authors},
                "startedAt": {"$lte": max_recorded_at},
                "endedAt": {"$gte": min_recorded_at},
            },
            {"_id": 0},
        ):
            raw_author = str(interval.get("rawAuthor") or "")
            started_at = _coerce_datetime(interval.get("startedAt"))
            ended_at = _coerce_datetime(interval.get("endedAt"))

            if raw_author in lookup and started_at and ended_at:
                lookup[raw_author]["intervals"].append({"startedAt": started_at, "endedAt": ended_at})

        for session in self.db.meeting_sessions.find(
            {"rawAuthor": {"$in": authors}, "startedAt": {"$lte": max_recorded_at}},
            {"_id": 0},
        ):
            raw_author = str(session.get("rawAuthor") or "")
            started_at = _coerce_datetime(session.get("startedAt"))

            if raw_author in lookup and started_at:
                lookup[raw_author]["sessions"].append({"startedAt": started_at})

        return lookup

    def _break_lookup_for_reports(self, report_rows: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        candidate_times_by_author: dict[str, list[dt.datetime]] = {}

        for report_row in report_rows:
            if report_row.get("source") in {"discord", "telegram"} or report_row.get("reportType") in {"meeting", "telegram"}:
                continue

            recorded_at = _coerce_datetime(
                report_row.get("recordedAt") or report_row.get("lastRecordedAt") or report_row.get("receivedAt")
            )

            if not recorded_at:
                continue

            raw_author = str(report_row.get("author") or "Unknown User")
            candidate_times_by_author.setdefault(raw_author, []).append(recorded_at)

        if not candidate_times_by_author:
            return {}

        authors = sorted(candidate_times_by_author)
        min_recorded_at = min(min(values) for values in candidate_times_by_author.values())
        max_recorded_at = max(max(values) for values in candidate_times_by_author.values())
        lookup = {author: {"intervals": [], "sessions": []} for author in authors}

        for interval in self.db.break_intervals.find(
            {
                "rawAuthor": {"$in": authors},
                "startedAt": {"$lte": max_recorded_at},
                "endedAt": {"$gte": min_recorded_at},
            },
            {"_id": 0},
        ):
            raw_author = str(interval.get("rawAuthor") or "")
            started_at = _coerce_datetime(interval.get("startedAt"))
            ended_at = _coerce_datetime(interval.get("endedAt"))

            if raw_author in lookup and started_at and ended_at:
                lookup[raw_author]["intervals"].append({"startedAt": started_at, "endedAt": ended_at})

        for session in self.db.break_sessions.find(
            {"rawAuthor": {"$in": authors}, "startedAt": {"$lte": max_recorded_at}},
            {"_id": 0},
        ):
            raw_author = str(session.get("rawAuthor") or "")
            started_at = _coerce_datetime(session.get("startedAt"))

            if raw_author in lookup and started_at:
                lookup[raw_author]["sessions"].append({"startedAt": started_at})

        return lookup

    def _is_empty_plugin_report_without_signal(self, report_row: dict[str, Any]) -> bool:
        if report_row.get("source") in {"discord", "telegram", "status"} or report_row.get("reportType") in {"meeting", "telegram", "status"}:
            return False

        if _has_time_delta(report_row):
            return False

        return not (
            report_row.get("activityCountDeltas")
            or report_row.get("savedPrefabDeltas")
            or report_row.get("overtimeActivityCountDeltas")
            or report_row.get("overtimeSavedPrefabDeltas")
        )

    def activity_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        now: dt.datetime | None = None,
        include_profiles: bool = True,
        include_hourly: bool = True,
        include_breakdowns: bool = True,
    ) -> dict[str, Any]:
        composed(self).materialize_live_meeting_reports(now)
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
        hourly_by_author: dict[str, dict[str, Any]] = {}
        authors_by_raw: dict[str, dict[str, Any]] = {}
        normal_consumed_by_author_date: dict[tuple[str, str], int] = {}
        telegram_seconds_by_author_date: dict[tuple[str, str], int] = {}
        break_seconds_by_author_date: dict[tuple[str, str], int] = {}
        meeting_seconds_by_author_date: dict[tuple[str, str], int] = {}
        break_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        meeting_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        summary_dates_by_author: dict[str, set[str]] = {}
        profiles = composed(self)._profiles_by_raw_author()
        now = now or dt.datetime.now(dt.UTC)
        daily_items = sorted(
            self.db.daily_author_activity.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0}),
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("lastRecordedAt") or item.get("lastReceivedAt") or ""),
                str(item.get("source") or ""),
            ),
        )
        if date_mode == "authorLocalToday":
            daily_items = [
                item
                for item in daily_items
                if _is_live_date_match(
                    item.get("date"),
                    item.get("author") or "Unknown User",
                    profiles,
                    item.get("timeZoneId"),
                    now,
                    start_date,
                    end_date,
                )
            ]
        break_buckets = composed(self)._break_buckets_for_daily_items(daily_items)
        meeting_buckets = composed(self)._meeting_buckets_for_daily_items(daily_items, now)
        telegram_gaps = composed(self)._telegram_gaps_for_daily_items(daily_items)
        telegram_gap_counted: set[tuple[str, str]] = set()

        for item in daily_items:
            raw_author = item.get("author") or "Unknown User"
            item_date = item.get("date") or ""
            author_date_key = (raw_author, item_date)
            if item_date:
                summary_dates_by_author.setdefault(raw_author, set()).add(item_date)
            profile = profiles.get(raw_author, {})
            display_name = _display_name(raw_author, profile)
            hourly_activity = _apply_breaks_to_hourly_activity(
                item.get("hourlyActivity", []),
                break_buckets.get(author_date_key, []),
                break_consumed_by_author_date_hour.setdefault(author_date_key, _empty_hourly_activity()),
            )
            hourly_activity = _apply_meetings_to_hourly_activity(
                hourly_activity,
                meeting_buckets.get(author_date_key, []),
                meeting_consumed_by_author_date_hour.setdefault(author_date_key, _empty_hourly_activity()),
            )
            vacation_mark = composed(self).vacation_mark_for_author_date(raw_author, item_date)
            telegram_gap = telegram_gaps.get(author_date_key, {})
            telegram_gap_hours = telegram_gap.get("hourlyActivity", [])
            can_apply_telegram_gap = item.get("source") not in {"telegram", "discord"}
            telegram_gap_seconds = (
                int(telegram_gap.get("seconds", 0))
                if can_apply_telegram_gap and author_date_key not in telegram_gap_counted
                else 0
            )

            if telegram_gap_seconds:
                _merge_hourly_activity(hourly_activity, telegram_gap_hours)
                telegram_gap_counted.add(author_date_key)

            if vacation_mark:
                hourly_activity = composed(self).convert_hourly_to_vacation_overtime(hourly_activity)

            report_active_seconds = int(item.get("activeSeconds", 0))
            report_idle_seconds = sum(int(hour.get("idleSeconds", 0)) for hour in hourly_activity)
            raw_plugin_day_seconds = max(0, int(item.get("activeSeconds", 0)) + int(item.get("idleSeconds", 0)) + telegram_gap_seconds)
            effective_break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly_activity)
            effective_meeting_seconds = sum(int(hour.get("meetingSeconds", 0)) for hour in hourly_activity)
            vacation_overtime_seconds = 0

            if vacation_mark:
                vacation_overtime_seconds = sum(int(hour.get("overtimeActiveSeconds", 0)) for hour in hourly_activity)
                report_active_seconds = 0
                report_idle_seconds = 0
                effective_break_seconds = 0
                effective_meeting_seconds = 0

            telegram_day_seconds = int(item.get("daySeconds", 0))
            telegram_to_first_activity_seconds = telegram_gap_seconds
            work_window_seconds = int(item.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
            normal_consumed = normal_consumed_by_author_date.get(author_date_key, 0)
            normal_available = max(0, work_window_seconds - normal_consumed)
            plugin_day_seconds = min(max(0, report_active_seconds + report_idle_seconds), normal_available)
            effective_active_seconds = min(report_active_seconds, plugin_day_seconds)
            effective_idle_seconds = min(
                max(0, report_idle_seconds),
                max(0, plugin_day_seconds - effective_active_seconds),
            )
            normal_consumed_by_author_date[author_date_key] = normal_consumed + plugin_day_seconds
            telegram_seconds_by_author_date[author_date_key] = telegram_seconds_by_author_date.get(author_date_key, 0) + telegram_day_seconds
            break_seconds_by_author_date[author_date_key] = break_seconds_by_author_date.get(author_date_key, 0) + effective_break_seconds
            meeting_seconds_by_author_date[author_date_key] = meeting_seconds_by_author_date.get(author_date_key, 0) + effective_meeting_seconds
            totals["daySeconds"] += telegram_day_seconds
            totals["telegramDaySeconds"] += telegram_day_seconds
            totals["pluginDaySeconds"] += plugin_day_seconds
            totals["rawPluginDaySeconds"] += raw_plugin_day_seconds
            totals["telegramToFirstActivitySeconds"] += telegram_to_first_activity_seconds
            totals["activeSeconds"] += effective_active_seconds
            totals["idleSeconds"] += effective_idle_seconds
            totals["meetingSeconds"] += effective_meeting_seconds
            totals["overtimeActiveSeconds"] += vacation_overtime_seconds if vacation_mark else int(item.get("overtimeActiveSeconds", 0))
            totals["breakSeconds"] += effective_break_seconds

            for count in item.get("activityCounts", []):
                activity_type = count.get("type")

                if activity_type:
                    target_counts = overtime_activity_counts if vacation_mark else activity_counts
                    target_counts[activity_type] = target_counts.get(activity_type, 0) + int(count.get("count", 0))

            for count in item.get("overtimeActivityCounts", []):
                activity_type = count.get("type")

                if activity_type:
                    overtime_activity_counts[activity_type] = overtime_activity_counts.get(activity_type, 0) + int(count.get("count", 0))

            saved_prefab_items = _saved_prefabs_for_summary_item(item)
            overtime_saved_prefab_items = item.get("overtimeSavedPrefabs", [])

            for prefab in saved_prefab_items:
                path = prefab.get("path")

                if not path:
                    continue

                target_prefabs = overtime_saved_prefabs if vacation_mark else saved_prefabs
                existing = target_prefabs.get(path)

                if existing:
                    existing["saveCount"] += int(prefab.get("saveCount", 0))
                else:
                    target_prefabs[path] = dict(prefab)

            for prefab in overtime_saved_prefab_items:
                path = prefab.get("path")

                if not path:
                    continue

                existing = overtime_saved_prefabs.get(path)

                if existing:
                    existing["saveCount"] += int(prefab.get("saveCount", 0))
                else:
                    overtime_saved_prefabs[path] = dict(prefab)

            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                author_row = {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or item.get("authorEmail", ""),
                    "displayName": display_name,
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "telegramPrivateChatId": profile.get("telegramPrivateChatId"),
                    "discordUserId": profile.get("discordUserId", ""),
                    "discordUsername": profile.get("discordUsername", ""),
                    "autoBreakEnabled": profile.get("autoBreakEnabled", False),
                    "autoBreakEffectiveDate": profile.get("autoBreakEffectiveDate", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "source": item.get("source"),
                    "pluginVersion": item.get("pluginVersion"),
                    "timeZoneId": profile.get("timeZoneId") or item.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName") or item.get("timeZoneDisplayName"),
                    "lastRecordedAt": item.get("lastRecordedAt"),
                    "lastReceivedAt": item.get("lastReceivedAt"),
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
                    "activityCounts": [],
                    "savedPrefabs": [],
                    "overtimeActivityCounts": [],
                    "overtimeSavedPrefabs": [],
                    "_activityCountsBySource": {},
                    "_savedPrefabsBySource": {},
                    "_overtimeActivityCountsBySource": {},
                    "_overtimeSavedPrefabsBySource": {},
                }
                authors_by_raw[raw_author] = author_row

            author_row["daySeconds"] += telegram_day_seconds
            author_row["telegramDaySeconds"] += telegram_day_seconds
            author_row["pluginDaySeconds"] += plugin_day_seconds
            author_row["rawPluginDaySeconds"] += raw_plugin_day_seconds
            author_row["telegramToFirstActivitySeconds"] += telegram_to_first_activity_seconds
            author_row["activeSeconds"] += effective_active_seconds
            author_row["idleSeconds"] += effective_idle_seconds
            author_row["meetingSeconds"] += effective_meeting_seconds
            author_row["breakSeconds"] += effective_break_seconds
            author_row["overtimeActiveSeconds"] += vacation_overtime_seconds if vacation_mark else int(item.get("overtimeActiveSeconds", 0))
            if vacation_mark:
                author_row["dayOverride"] = vacation_mark
                author_row["calendarDayMark"] = vacation_mark
            author_row["authorEmail"] = profile.get("authorEmail") or item.get("authorEmail") or author_row.get("authorEmail", "")
            author_row["pluginVersion"] = item.get("pluginVersion") or author_row.get("pluginVersion")
            author_row["source"] = item.get("source") or author_row.get("source")
            author_row["timeZoneId"] = profile.get("timeZoneId") or item.get("timeZoneId") or author_row.get("timeZoneId")
            author_row["timeZoneDisplayName"] = (
                profile.get("timeZoneDisplayName") or item.get("timeZoneDisplayName") or author_row.get("timeZoneDisplayName")
            )
            if vacation_mark:
                author_row["overtimeActivityCounts"] = _merge_count_list(
                    author_row.get("overtimeActivityCounts", []), item.get("activityCounts", []), "type", "count"
                )
                author_row["overtimeSavedPrefabs"] = _merge_count_list(
                    author_row.get("overtimeSavedPrefabs", []), saved_prefab_items, "path", "saveCount"
                )
            else:
                author_row["activityCounts"] = _merge_count_list(
                    author_row.get("activityCounts", []), item.get("activityCounts", []), "type", "count"
                )
                author_row["savedPrefabs"] = _merge_count_list(
                    author_row.get("savedPrefabs", []), saved_prefab_items, "path", "saveCount"
                )
            author_row["overtimeActivityCounts"] = _merge_count_list(
                author_row.get("overtimeActivityCounts", []), item.get("overtimeActivityCounts", []), "type", "count"
            )
            author_row["overtimeSavedPrefabs"] = _merge_count_list(
                author_row.get("overtimeSavedPrefabs", []), overtime_saved_prefab_items, "path", "saveCount"
            )
            source_key = str(item.get("source") or "unknown")
            if vacation_mark:
                author_row["_overtimeActivityCountsBySource"][source_key] = _merge_count_list(
                    author_row["_overtimeActivityCountsBySource"].get(source_key, []),
                    item.get("activityCounts", []),
                    "type",
                    "count",
                )
                author_row["_overtimeSavedPrefabsBySource"][source_key] = _merge_count_list(
                    author_row["_overtimeSavedPrefabsBySource"].get(source_key, []),
                    saved_prefab_items,
                    "path",
                    "saveCount",
                )
            else:
                author_row["_activityCountsBySource"][source_key] = _merge_count_list(
                    author_row["_activityCountsBySource"].get(source_key, []), item.get("activityCounts", []), "type", "count"
                )
                author_row["_savedPrefabsBySource"][source_key] = _merge_count_list(
                    author_row["_savedPrefabsBySource"].get(source_key, []), saved_prefab_items, "path", "saveCount"
                )
            author_row["_overtimeActivityCountsBySource"][source_key] = _merge_count_list(
                author_row["_overtimeActivityCountsBySource"].get(source_key, []),
                item.get("overtimeActivityCounts", []),
                "type",
                "count",
            )
            author_row["_overtimeSavedPrefabsBySource"][source_key] = _merge_count_list(
                author_row["_overtimeSavedPrefabsBySource"].get(source_key, []),
                overtime_saved_prefab_items,
                "path",
                "saveCount",
            )

            if str(item.get("lastRecordedAt") or "") > str(author_row.get("lastRecordedAt") or ""):
                author_row["lastRecordedAt"] = item.get("lastRecordedAt")

            if item.get("lastReceivedAt") and (
                not author_row.get("lastReceivedAt") or item.get("lastReceivedAt") > author_row.get("lastReceivedAt")
            ):
                author_row["lastReceivedAt"] = item.get("lastReceivedAt")

            current_author = hourly_by_author.get(raw_author)

            if not current_author:
                current_author = {
                    "author": display_name,
                    "rawAuthor": raw_author,
                    "timeZoneId": item.get("timeZoneId"),
                    "timeZoneDisplayName": item.get("timeZoneDisplayName"),
                    "hourlyActivity": _empty_hourly_activity(),
                }
                hourly_by_author[raw_author] = current_author
            else:
                current_author["timeZoneId"] = current_author.get("timeZoneId") or item.get("timeZoneId")
                current_author["timeZoneDisplayName"] = current_author.get("timeZoneDisplayName") or item.get(
                    "timeZoneDisplayName"
                )

            _merge_hourly_activity(current_author["hourlyActivity"], hourly_activity)

        activity_mix = _activity_mix_from_counts(activity_counts)
        overtime_activity_mix = _activity_mix_from_counts(overtime_activity_counts)

        composed(self)._apply_live_activity_summary(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            telegram_seconds_by_author_date,
            break_seconds_by_author_date,
            start_date,
            end_date,
            date_mode,
            now,
            meeting_seconds_by_author_date,
            meeting_buckets,
        )
        meeting_hourly_adjustments = _merge_meeting_buckets_into_hourly_author_rows(hourly_by_author, meeting_buckets, profiles)

        for raw_author, adjustment in meeting_hourly_adjustments.items():
            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                continue

            meeting_addition = int(adjustment.get("meetingSeconds", 0))

            if meeting_addition > 0:
                author_row["meetingSeconds"] = int(author_row.get("meetingSeconds", 0)) + meeting_addition
                totals["meetingSeconds"] = int(totals.get("meetingSeconds", 0)) + meeting_addition

            idle_reduction = int(adjustment.get("idleSeconds", 0))

            if idle_reduction > 0:
                applied_reduction = min(int(author_row.get("idleSeconds", 0)), idle_reduction)
                author_row["idleSeconds"] = int(author_row.get("idleSeconds", 0)) - applied_reduction
                totals["idleSeconds"] = max(0, int(totals.get("idleSeconds", 0)) - applied_reduction)

        for raw_author in composed(self).list_authors():
            composed(self)._ensure_summary_author(authors_by_raw, raw_author, profiles)

        for raw_author, author_row in authors_by_raw.items():
            if raw_author in hourly_by_author:
                continue

            hourly_by_author[raw_author] = {
                "author": author_row["displayName"],
                "rawAuthor": raw_author,
                "timeZoneId": profiles.get(raw_author, {}).get("timeZoneId"),
                "timeZoneDisplayName": profiles.get(raw_author, {}).get("timeZoneDisplayName"),
                "hourlyActivity": _empty_hourly_activity(),
            }

        self._apply_vacation_summary_overrides(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            start_date,
            end_date,
            date_mode,
            now,
        )
        self._apply_visual_missed_hours(hourly_by_author, profiles, start_date, end_date, date_mode, now, include_end=False)
        self._apply_plugin_hour_idle_gaps(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            start_date,
            end_date,
            date_mode,
            now,
        )
        self._apply_offline_idle_gaps(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            start_date,
            end_date,
            date_mode,
            now,
        )
        self._apply_workday_hour_idle_gaps(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            start_date,
            end_date,
            date_mode,
            now,
        )
        self._apply_summary_auto_breaks(
            authors_by_raw,
            hourly_by_author,
            totals,
            profiles,
            summary_dates_by_author,
        )
        self._apply_visual_missed_hours(hourly_by_author, profiles, start_date, end_date, date_mode, now, include_start=False)
        self._apply_visual_overtime_hour_gaps(hourly_by_author, profiles, start_date, end_date, date_mode, now)
        self._apply_latest_report_metadata(
            authors_by_raw,
            start_date,
            end_date,
            date_mode,
            profiles,
            now,
        )
        _clear_inactive_author_report_metadata(authors_by_raw.values())
        presence_overrides = self._author_presence_overrides(
            authors_by_raw.keys(),
            profiles,
            start_date,
            end_date,
            date_mode,
            now,
        )
        for author in authors_by_raw.values():
            raw_author = author["rawAuthor"]
            profile = profiles.get(raw_author, {})
            url = _cached_author_avatar_api_url(raw_author, _github_username_for_avatar_fetch(raw_author, profile), profile)
            if url:
                author["avatarUrl"] = url
            else:
                author.pop("avatarUrl", None)

        author_rows = []

        for author in authors_by_raw.values():
            raw_author = author["rawAuthor"]
            presence_override = presence_overrides.get(raw_author)
            workday_started = self._author_workday_started_for_summary(
                raw_author,
                author,
                profiles,
                start_date,
                end_date,
                date_mode,
                now,
            )
            track_plugin_staleness = _should_track_plugin_staleness(
                raw_author,
                profiles,
                author.get("timeZoneId"),
                start_date,
                end_date,
                date_mode,
                now,
                workday_started,
            )

            if presence_override and presence_override.get("offlineAt"):
                track_plugin_staleness = False

            send_interval_seconds = composed(self).get_interval_for_author(raw_author)
            summary_author = _with_author_presence(
                _with_source_breakdowns(_with_activity_mix(_with_productivity(author))),
                send_interval_seconds,
                now,
                presence_override,
                track_plugin_staleness,
            )
            composed(self)._record_status_transition_for_author(
                summary_author,
                send_interval_seconds,
                now,
                track_plugin_staleness,
            )
            author_rows.append(summary_author)
        hourly_author_rows = [
            {**item, "hourlyActivity": _public_hourly_activity(item.get("hourlyActivity", []))}
            for item in hourly_by_author.values()
        ]

        return {
            "totals": totals,
            "activityMix": sorted(activity_mix, key=lambda item: item["count"], reverse=True) if include_breakdowns else [],
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True) if include_breakdowns else [],
            "overtimeActivityMix": sorted(overtime_activity_mix, key=lambda item: item["count"], reverse=True) if include_breakdowns else [],
            "overtimeSavedPrefabs": sorted(overtime_saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True) if include_breakdowns else [],
            "authors": sorted(author_rows, key=lambda item: item["displayName"].lower()),
            "profiles": composed(self).author_profiles() if include_profiles else [],
            "authorAliases": composed(self).author_aliases() if include_profiles else [],
            "hourlyActivityByAuthor": sorted(hourly_author_rows, key=lambda item: item["author"]) if include_hourly else [],
        }

    def _apply_summary_auto_breaks(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        summary_dates_by_author: dict[str, set[str]],
    ) -> None:
        for raw_author, hourly_author in hourly_by_author.items():
            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                continue

            remaining_seconds = self._summary_auto_break_remaining_seconds(
                raw_author,
                profiles,
                summary_dates_by_author.get(raw_author, set()),
            )

            if remaining_seconds <= 0:
                continue

            transferred_seconds = self._transfer_summary_idle_to_break(
                hourly_author.get("hourlyActivity", []),
                remaining_seconds,
            )

            if transferred_seconds <= 0:
                continue

            applied_idle_reduction = min(int(author_row.get("idleSeconds", 0)), transferred_seconds)
            author_row["idleSeconds"] = int(author_row.get("idleSeconds", 0)) - applied_idle_reduction
            author_row["breakSeconds"] = int(author_row.get("breakSeconds", 0)) + transferred_seconds
            author_row["pluginDaySeconds"] = max(0, int(author_row.get("pluginDaySeconds", 0)) - applied_idle_reduction)
            author_row["rawPluginDaySeconds"] = max(0, int(author_row.get("rawPluginDaySeconds", 0)) - applied_idle_reduction)
            totals["idleSeconds"] = max(0, int(totals.get("idleSeconds", 0)) - applied_idle_reduction)
            totals["breakSeconds"] = int(totals.get("breakSeconds", 0)) + transferred_seconds
            totals["pluginDaySeconds"] = max(0, int(totals.get("pluginDaySeconds", 0)) - applied_idle_reduction)
            totals["rawPluginDaySeconds"] = max(0, int(totals.get("rawPluginDaySeconds", 0)) - applied_idle_reduction)

    def _summary_auto_break_remaining_seconds(
        self,
        raw_author: str,
        profiles: dict[str, dict[str, Any]],
        summary_dates: set[str],
    ) -> int:
        if not raw_author or not summary_dates:
            return 0

        profile = profiles.get(raw_author, {})

        if not profile.get("autoBreakEnabled"):
            return 0

        effective_date = str(profile.get("autoBreakEffectiveDate") or "")

        if not effective_date:
            return 0

        remaining_seconds = 0

        for day_date in summary_dates:
            if day_date < effective_date:
                continue

            remaining_seconds += AUTO_BREAK_SECONDS

        return remaining_seconds

    def _transfer_summary_idle_to_break(self, hourly_activity: list[dict[str, Any]], remaining_seconds: int) -> int:
        if remaining_seconds <= 0:
            return 0

        transferred_seconds = 0

        for hour in sorted(hourly_activity, key=lambda item: int(item.get("hour", 0))):
            if transferred_seconds >= remaining_seconds:
                break

            idle_seconds = max(0, int(hour.get("idleSeconds", 0)))
            telegram_gap_idle_seconds = max(0, int(hour.get("telegramToFirstActivityIdleSeconds", 0)))
            plugin_hour_gap_idle_seconds = max(0, int(hour.get("pluginHourGapIdleSeconds", 0)))
            visual_occupied_seconds = _visual_hour_occupied_seconds(hour) + int(hour.get("missedSeconds", 0))
            blocked_plugin_gap_idle_seconds = plugin_hour_gap_idle_seconds if visual_occupied_seconds < 3600 else 0
            convertible_idle_seconds = max(0, idle_seconds - telegram_gap_idle_seconds - blocked_plugin_gap_idle_seconds)

            if convertible_idle_seconds <= 0:
                continue

            occupied_seconds = (
                max(0, int(hour.get("activeSeconds", 0)))
                + max(0, int(hour.get("meetingSeconds", 0)))
                + max(0, int(hour.get("breakSeconds", 0)))
                + max(0, int(hour.get("overtimeActiveSeconds", 0)))
            )
            available_break_seconds = max(0, 3600 - occupied_seconds)

            if available_break_seconds <= 0:
                continue

            move_seconds = min(convertible_idle_seconds, available_break_seconds, remaining_seconds - transferred_seconds)
            idle_microseconds = max(
                0,
                _time_microseconds(hour, "idleSeconds", "idleMicroseconds") - (move_seconds * MICROSECONDS_PER_SECOND),
            )
            hour["idleMicroseconds"] = idle_microseconds
            hour["idleSeconds"] = _seconds_from_microseconds(idle_microseconds)
            hour["pluginHourGapIdleSeconds"] = min(plugin_hour_gap_idle_seconds, hour["idleSeconds"])
            hour["breakSeconds"] = int(hour.get("breakSeconds", 0)) + move_seconds
            break_start_seconds = occupied_seconds if occupied_seconds > 0 else 3600 - move_seconds
            _add_break_segment_to_hour(hour, break_start_seconds, break_start_seconds + move_seconds)
            transferred_seconds += move_seconds

        return transferred_seconds

    def _apply_vacation_summary_overrides(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> None:
        for raw_author, author_row in authors_by_raw.items():
            vacation_dates = self._summary_vacation_dates_for_author(
                raw_author,
                profiles,
                start_date,
                end_date,
                date_mode,
                now,
            )

            if not vacation_dates:
                continue

            composed(self).apply_vacation_mark_to_author(author_row, vacation_dates[-1])
            active_seconds = int(author_row.get("activeSeconds", 0))
            meeting_seconds = int(author_row.get("meetingSeconds", 0))
            vacation_overtime_addition = active_seconds + meeting_seconds

            if vacation_overtime_addition > 0:
                author_row["overtimeActiveSeconds"] = int(author_row.get("overtimeActiveSeconds", 0)) + vacation_overtime_addition
                totals["overtimeActiveSeconds"] = int(totals.get("overtimeActiveSeconds", 0)) + vacation_overtime_addition

            for key in ("activeSeconds", "idleSeconds", "meetingSeconds", "breakSeconds"):
                removed_seconds = int(author_row.get(key, 0))
                author_row[key] = 0
                totals[key] = max(0, int(totals.get(key, 0)) - removed_seconds)

            hourly_author = hourly_by_author.get(raw_author)

            if hourly_author:
                hourly_author["dayOverride"] = author_row.get("dayOverride")
                hourly_author["calendarDayMark"] = author_row.get("calendarDayMark")
                hourly_author["hourlyActivity"] = composed(self).convert_hourly_to_vacation_overtime(
                    hourly_author.get("hourlyActivity", [])
                )

    def _summary_vacation_dates_for_author(
        self,
        raw_author: str,
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> list[str]:
        dates: list[str] = []

        for mark in self.db.calendar_marks.find({"rawAuthor": raw_author, "reasonId": "vacation"}, {"_id": 0, "date": 1}):
            day_date = str(mark.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, None)

            if day_date and _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                dates.append(day_date)

        return sorted(dates)

    def _apply_plugin_hour_idle_gaps(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> None:
        latest_report_by_author_date = self._latest_report_times_by_author_date(start_date, end_date, date_mode, profiles, now)

        for (raw_author, day_date), latest_report_at in latest_report_by_author_date.items():
            time_zone_id = _author_time_zone_id(raw_author, profiles, None)

            if not _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, day_date):
                continue

            author_row = authors_by_raw.get(raw_author)
            hourly_author = hourly_by_author.get(raw_author)

            if not author_row or not hourly_author:
                continue

            local_latest_report_at = _to_local_datetime(latest_report_at, time_zone_id)
            hourly_activity = hourly_author.get("hourlyActivity", [])

            if not hourly_activity:
                continue

            visual_plugin_seconds = int(author_row.get("pluginDaySeconds", 0))

            for hour_index in range(0, min(local_latest_report_at.hour, len(hourly_activity))):
                hour = hourly_activity[hour_index]
                accounted_seconds = _visual_hour_occupied_seconds(hour) + int(hour.get("missedSeconds", 0))

                if accounted_seconds <= 0:
                    continue

                gap_seconds = max(0, 3600 - min(3600, accounted_seconds))
                remaining_plugin_seconds = max(0, DEFAULT_PLUGIN_WORK_WINDOW_SECONDS - visual_plugin_seconds)
                idle_seconds = min(gap_seconds, remaining_plugin_seconds)

                if idle_seconds <= 0:
                    continue

                _add_idle_seconds_to_hour(hour, idle_seconds)
                hour["pluginHourGapIdleSeconds"] = int(hour.get("pluginHourGapIdleSeconds", 0)) + idle_seconds
                visual_plugin_seconds += idle_seconds

    def _apply_offline_idle_gaps(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> None:
        latest_report_by_author_date = self._latest_report_times_by_author_date(start_date, end_date, date_mode, profiles, now)
        session_query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for session in self.db.day_sessions.find(session_query, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            day_date = str(session.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))

            if not day_date or not _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, day_date):
                continue

            latest_report_at = latest_report_by_author_date.get((raw_author, day_date))
            ended_at = _coerce_datetime(session.get("lastOfflineAt"))

            if not latest_report_at or not ended_at or ended_at <= latest_report_at:
                continue

            author_row = authors_by_raw.get(raw_author)
            hourly_author = hourly_by_author.get(raw_author)

            if not author_row or not hourly_author:
                continue

            gap_seconds = max(0, int((ended_at - latest_report_at).total_seconds()))
            remaining_plugin_seconds = max(0, DEFAULT_PLUGIN_WORK_WINDOW_SECONDS - int(author_row.get("pluginDaySeconds", 0)))
            idle_seconds = min(gap_seconds, remaining_plugin_seconds)

            if idle_seconds <= 0:
                continue

            idle_end = latest_report_at + dt.timedelta(seconds=idle_seconds)
            hourly_activity = _empty_hourly_activity()
            _add_idle_interval_to_buckets(hourly_activity, latest_report_at, idle_end, time_zone_id)
            _merge_hourly_activity(hourly_author["hourlyActivity"], hourly_activity)

    def _apply_workday_hour_idle_gaps(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> None:
        latest_report_by_author_date = self._latest_report_times_by_author_date(start_date, end_date, date_mode, profiles, now)
        session_query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for session in self.db.day_sessions.find(session_query, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            day_date = str(session.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))

            if not day_date or not _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, day_date):
                continue

            started_at = _coerce_datetime(session.get("startedAt"))
            ended_at = _coerce_datetime(session.get("lastOfflineAt")) or latest_report_by_author_date.get((raw_author, day_date))

            if not started_at or not ended_at or ended_at <= started_at:
                continue

            author_row = authors_by_raw.get(raw_author)
            hourly_author = hourly_by_author.get(raw_author)

            if not author_row or not hourly_author:
                continue

            hourly_activity = hourly_author.get("hourlyActivity", [])

            if not hourly_activity:
                continue

            local_start = _to_local_datetime(started_at, time_zone_id)
            local_end = _to_local_datetime(ended_at, time_zone_id)
            current_hour = local_start.replace(minute=0, second=0, microsecond=0)
            final_hour = local_end.replace(minute=0, second=0, microsecond=0)

            while current_hour <= final_hour:
                hour_index = current_hour.hour

                if 0 <= hour_index < len(hourly_activity):
                    hour_start = current_hour
                    hour_end = current_hour + dt.timedelta(hours=1)
                    expected_start = max(local_start, hour_start)
                    if hour_end > local_end:
                        current_hour += dt.timedelta(hours=1)
                        continue

                    expected_end = hour_end
                    expected_seconds = max(0, int((expected_end - expected_start).total_seconds()))
                    occupied_seconds = _visual_hour_occupied_seconds(hourly_activity[hour_index]) + int(
                        hourly_activity[hour_index].get("missedSeconds", 0)
                    )

                    if occupied_seconds <= 0:
                        current_hour += dt.timedelta(hours=1)
                        continue

                    occupied_seconds = min(expected_seconds, occupied_seconds)
                    gap_seconds = max(0, expected_seconds - occupied_seconds)
                    remaining_plugin_seconds = max(0, DEFAULT_PLUGIN_WORK_WINDOW_SECONDS - int(author_row.get("pluginDaySeconds", 0)))
                    idle_seconds = min(gap_seconds, remaining_plugin_seconds)

                    if idle_seconds > 0:
                        _add_idle_seconds_to_hour(hourly_activity[hour_index], idle_seconds)

                current_hour += dt.timedelta(hours=1)

    def _apply_visual_missed_hours(
        self,
        hourly_by_author: dict[str, dict[str, Any]],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        include_start: bool = True,
        include_end: bool = True,
    ) -> None:
        latest_report_by_author_date = self._latest_report_times_by_author_date(start_date, end_date, date_mode, profiles, now)
        session_query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for session in self.db.day_sessions.find(session_query, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            day_date = str(session.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))

            if not day_date or not _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, day_date):
                continue

            started_at = _coerce_datetime(session.get("startedAt"))
            ended_at = _coerce_datetime(session.get("lastOfflineAt"))
            latest_report_at = latest_report_by_author_date.get((raw_author, day_date))
            latest_signal_at = (latest_report_at or ended_at) if ended_at else None

            if not started_at and not latest_signal_at:
                continue

            profile = profiles.get(raw_author, {})
            hourly_author = hourly_by_author.get(raw_author)

            if not hourly_author:
                hourly_author = {
                    "author": _display_name(raw_author, profile),
                    "rawAuthor": raw_author,
                    "timeZoneId": profile.get("timeZoneId") or session.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                    "hourlyActivity": _empty_hourly_activity(),
                }
                hourly_by_author[raw_author] = hourly_author

            hourly_activity = hourly_author.get("hourlyActivity", [])
            if include_start:
                self._add_visual_missed_start(hourly_activity, started_at, time_zone_id)
            if include_end:
                self._add_visual_missed_end(
                    hourly_activity,
                    latest_signal_at,
                    time_zone_id,
                    fill_to_hour=latest_report_at is not None,
                    offline_at=ended_at,
                )

    def _latest_report_times_by_author_date(
        self,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        profiles: dict[str, dict[str, Any]],
        now: dt.datetime,
    ) -> dict[tuple[str, str], dt.datetime]:
        latest_by_key: dict[tuple[str, str], dt.datetime] = {}
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for report in self.db.report_rows.find(
            query,
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "reportType": 1,
                "recordedAt": 1,
                "lastRecordedAt": 1,
                "receivedAt": 1,
                "lastReceivedAt": 1,
            },
        ):
            if report.get("source") in {"telegram", "discord", "status"} or report.get("reportType") in {"telegram", "meeting", "status"}:
                continue

            raw_author = str(report.get("author") or "Unknown User")
            report_date = str(report.get("date") or "")

            if not report_date:
                continue

            occurred_at = (
                _coerce_datetime(report.get("recordedAt"))
                or _coerce_datetime(report.get("lastRecordedAt"))
                or _coerce_datetime(report.get("receivedAt"))
                or _coerce_datetime(report.get("lastReceivedAt"))
            )

            if not occurred_at:
                continue

            key = (raw_author, report_date)
            current = latest_by_key.get(key)

            if not current or occurred_at > current:
                latest_by_key[key] = occurred_at

        return latest_by_key

    def _add_visual_missed_start(
        self,
        hourly_activity: list[dict[str, Any]],
        started_at: dt.datetime | None,
        time_zone_id: str,
    ) -> None:
        if not started_at:
            return

        local_start = _to_local_datetime(started_at, time_zone_id)
        hour_start = local_start.replace(minute=0, second=0, microsecond=0)
        missed_seconds = max(0, int((local_start - hour_start).total_seconds()))
        _add_visual_missed_seconds(hourly_activity, local_start.hour, missed_seconds, "missedStartSeconds")
        self._trim_visual_idle_overflow(hourly_activity, local_start.hour)

    def _add_visual_missed_end(
        self,
        hourly_activity: list[dict[str, Any]],
        ended_at: dt.datetime | None,
        time_zone_id: str,
        fill_to_hour: bool = False,
        offline_at: dt.datetime | None = None,
    ) -> None:
        if not ended_at:
            return

        local_end = _to_local_datetime(ended_at, time_zone_id)

        if fill_to_hour:
            target_hour = _visual_missed_end_hour(hourly_activity, local_end.hour, _to_local_datetime(offline_at, time_zone_id) if offline_at else None)
            missed_seconds = _visual_hour_available_seconds(target_hour)
            target_hour_index = int(target_hour.get("hour", local_end.hour))
        else:
            hour_end = local_end.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            missed_seconds = max(0, int((hour_end - local_end).total_seconds()))
            target_hour_index = local_end.hour

        _add_visual_missed_seconds(hourly_activity, target_hour_index, missed_seconds, "missedEndSeconds")
        self._trim_visual_idle_overflow(hourly_activity, target_hour_index)

    def _trim_visual_idle_overflow(self, hourly_activity: list[dict[str, Any]], hour_index: int) -> None:
        if hour_index < 0 or hour_index >= len(hourly_activity):
            return

        hour = hourly_activity[hour_index]
        occupied_seconds = _visual_hour_occupied_seconds(hour) + int(hour.get("missedSeconds", 0))
        overflow_seconds = max(0, occupied_seconds - 3600)

        if overflow_seconds <= 0:
            return

        idle_microseconds = _time_microseconds(hour, "idleSeconds", "idleMicroseconds")
        overflow_microseconds = overflow_seconds * MICROSECONDS_PER_SECOND
        hour["idleMicroseconds"] = max(0, idle_microseconds - overflow_microseconds)
        hour["idleSeconds"] = _seconds_from_microseconds(hour["idleMicroseconds"])

    def _apply_visual_overtime_hour_gaps(
        self,
        hourly_by_author: dict[str, dict[str, Any]],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> None:
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)
        overtime_reports_by_key: dict[tuple[str, str], list[dt.datetime]] = {}

        for report in self.db.report_rows.find(
            query,
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "reportType": 1,
                "timeZoneId": 1,
                "recordedAt": 1,
                "lastRecordedAt": 1,
                "receivedAt": 1,
                "lastReceivedAt": 1,
                "overtimeActiveDeltaSeconds": 1,
                "overtimeActiveDeltaMicroseconds": 1,
            },
        ):
            if report.get("source") in {"telegram", "discord", "status"} or report.get("reportType") in {"telegram", "meeting", "status"}:
                continue

            if _time_microseconds(report, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") <= 0:
                continue

            raw_author = str(report.get("author") or "Unknown User")
            report_date = str(report.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, report.get("timeZoneId"))

            if not report_date or not _date_in_summary_scope(report_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, report_date):
                continue

            occurred_at = (
                _coerce_datetime(report.get("recordedAt"))
                or _coerce_datetime(report.get("lastRecordedAt"))
                or _coerce_datetime(report.get("receivedAt"))
                or _coerce_datetime(report.get("lastReceivedAt"))
            )

            if not occurred_at:
                continue

            overtime_reports_by_key.setdefault((raw_author, report_date), []).append(_to_local_datetime(occurred_at, time_zone_id))

        for (raw_author, _report_date), reports in overtime_reports_by_key.items():
            hourly_author = hourly_by_author.get(raw_author)

            if not hourly_author:
                continue

            hourly_activity = hourly_author.get("hourlyActivity", [])
            ordered_reports = sorted(reports)

            _fill_overtime_hours_bracketed_by_reports(hourly_activity, ordered_reports)
            _fill_normal_to_overtime_transition_hours(hourly_activity)

    def _apply_latest_report_metadata(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        profiles: dict[str, dict[str, Any]],
        now: dt.datetime,
    ) -> None:
        latest_by_author: dict[str, dict[str, Any]] = {}
        latest_daily_by_author: dict[str, dict[str, Any]] = {}
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)
        report_rows = list(self.db.report_rows.find(query, {"_id": 0}))
        meeting_lookup = self._meeting_lookup_for_reports(report_rows)

        for report in report_rows:
            raw_author = str(report.get("author") or "Unknown User")

            if raw_author not in authors_by_raw:
                continue

            if date_mode == "authorLocalToday" and not _is_author_local_today(
                report.get("date"),
                raw_author,
                profiles,
                report.get("timeZoneId"),
                now,
            ):
                continue

            if report.get("source") == "status" or report.get("reportType") == "status":
                continue

            if self._is_empty_plugin_report_without_signal(report) or self._is_idle_report_during_meeting(report, meeting_lookup):
                continue

            received_at = _coerce_datetime(report.get("receivedAt") or report.get("lastReceivedAt"))
            recorded_at = _coerce_datetime(report.get("recordedAt") or report.get("lastRecordedAt") or report.get("receivedAt"))
            report_sort_at = received_at or recorded_at

            if not report_sort_at:
                continue

            current = latest_by_author.get(raw_author)

            if current and report_sort_at <= current["sortAt"]:
                continue

            latest_by_author[raw_author] = {"report": report, "sortAt": report_sort_at}

        for daily in self.db.daily_author_activity.find(query, {"_id": 0}):
            raw_author = str(daily.get("author") or "Unknown User")

            if raw_author not in authors_by_raw:
                continue

            if date_mode == "authorLocalToday" and not _is_author_local_today(
                daily.get("date"),
                raw_author,
                profiles,
                daily.get("timeZoneId"),
                now,
            ):
                continue

            received_at = _coerce_datetime(daily.get("lastReceivedAt") or daily.get("receivedAt"))

            if not received_at:
                continue

            current = latest_daily_by_author.get(raw_author)

            if current and received_at <= current["sortAt"]:
                continue

            latest_daily_by_author[raw_author] = {"daily": daily, "sortAt": received_at}

        for raw_author, latest in latest_by_author.items():
            report = latest["report"]
            author_row = authors_by_raw[raw_author]
            author_row["source"] = report.get("source") or author_row.get("source")
            author_row["pluginVersion"] = report.get("pluginVersion") or author_row.get("pluginVersion")
            author_row["lastRecordedAt"] = report.get("lastRecordedAt") or report.get("recordedAt") or author_row.get("lastRecordedAt")
            author_row["lastReceivedAt"] = _iso(report.get("lastReceivedAt") or report.get("receivedAt")) or author_row.get("lastReceivedAt")

        for raw_author, latest in latest_daily_by_author.items():
            daily = latest["daily"]
            author_row = authors_by_raw[raw_author]
            row_received_at = _coerce_datetime(author_row.get("lastReceivedAt"))

            if not row_received_at or latest["sortAt"] > row_received_at:
                author_row["source"] = daily.get("source") or author_row.get("source")
                author_row["pluginVersion"] = daily.get("pluginVersion") or author_row.get("pluginVersion")
                author_row["lastRecordedAt"] = daily.get("lastRecordedAt") or author_row.get("lastRecordedAt")
                author_row["lastReceivedAt"] = _iso(daily.get("lastReceivedAt") or daily.get("receivedAt")) or author_row.get("lastReceivedAt")

        for raw_author, author_row in authors_by_raw.items():
            profile = profiles.get(raw_author, {})
            profile_raw_dt = _coerce_datetime(profile.get("lastRawReportReceivedAt"))
            row_dt = _coerce_datetime(author_row.get("lastReceivedAt"))
            merged: list[dt.datetime] = []

            if profile_raw_dt:
                profile_raw_date = _local_date_for_time_zone(
                    profile_raw_dt,
                    _author_time_zone_id(raw_author, profiles, author_row.get("timeZoneId")),
                )

                if _date_in_summary_scope(
                    profile_raw_date,
                    raw_author,
                    profiles,
                    author_row.get("timeZoneId"),
                    now,
                    start_date,
                    end_date,
                    date_mode,
                ):
                    merged.append(profile_raw_dt)

            if row_dt:
                merged.append(row_dt)

            if merged:
                author_row["lastReceivedAt"] = _iso(max(merged))

    def _author_presence_overrides(
        self,
        raw_authors: Any,
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> dict[str, dict[str, Any]]:
        authors = [str(author or "") for author in raw_authors if str(author or "")]

        if not authors:
            return {}

        latest_break_by_author: dict[str, dict[str, Any]] = {}

        for event in self.db.break_events.find({"rawAuthor": {"$in": authors}, "eventType": {"$in": ["online", "offline"]}}, {"_id": 0}):
            raw_author = str(event.get("rawAuthor") or "")
            timestamp = _coerce_datetime(event.get("timestamp"))

            if not raw_author or not timestamp:
                continue

            event_date = _local_date_for_time_zone(timestamp, _author_time_zone_id(raw_author, profiles, None))

            if not _date_in_summary_scope(event_date, raw_author, profiles, None, now, start_date, end_date, date_mode):
                continue

            current = latest_break_by_author.get(raw_author)

            if not current or timestamp > current["timestamp"]:
                latest_break_by_author[raw_author] = {"eventType": str(event.get("eventType") or ""), "timestamp": timestamp}

        overrides: dict[str, dict[str, Any]] = {}

        for raw_author, event in latest_break_by_author.items():
            if event.get("eventType") != "offline":
                continue

            offline_at = event["timestamp"]
            latest_overtime_at: dt.datetime | None = None

            for report in self.db.report_rows.find({"author": raw_author, "receivedAt": {"$gt": offline_at}}, {"_id": 0}):
                if _time_microseconds(report, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") <= 0:
                    continue

                received_at = _coerce_datetime(report.get("receivedAt") or report.get("lastReceivedAt"))

                if received_at and (not latest_overtime_at or received_at > latest_overtime_at):
                    latest_overtime_at = received_at

            overrides[raw_author] = {
                "offlineAt": offline_at,
                "overtimeReceivedAt": latest_overtime_at,
            }

        return overrides

    def _author_workday_started_for_summary(
        self,
        raw_author: str,
        author: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
    ) -> bool:
        if bool(author.get("activeMeeting")):
            return True

        time_zone_id = _author_time_zone_id(raw_author, profiles, author.get("timeZoneId"))
        profile = profiles.get(raw_author, {})

        if _author_has_summary_activity(author) and not str(profile.get("telegramUsername") or "").strip():
            return True

        anchor_day = _local_date_for_time_zone(now, time_zone_id)

        if not _date_in_summary_scope(anchor_day, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
            return False

        session = self.db.day_sessions.find_one(
            {"rawAuthor": raw_author, "date": anchor_day},
            {"_id": 0, "startedAt": 1, "lastOfflineAt": 1, "lastOnlineAt": 1},
        )

        if session:
            started_at = _coerce_datetime(session.get("startedAt"))
            last_offline_at = _coerce_datetime(session.get("lastOfflineAt"))
            last_online_at = _coerce_datetime(session.get("lastOnlineAt"))

            if last_offline_at:
                if not last_online_at or last_online_at <= last_offline_at:
                    return False

            if started_at:
                return True

        return False

    def analytics_summary(self, period: str = "7d") -> dict[str, Any]:
        year = dt.date.today().year
        profiles = composed(self)._profiles_by_raw_author()
        start_date = dt.date(year - 1, 12, 1).isoformat()
        end_date = dt.date(year, 12, 31).isoformat()
        docs = list(self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}))
        authors = set(composed(self).list_authors())
        docs_by_author: dict[str, list[dict[str, Any]]] = {}

        for item in docs:
            raw_author = str(item.get("author") or "")

            if raw_author:
                authors.add(raw_author)
                docs_by_author.setdefault(raw_author, []).append(item)

        author_summaries = []

        for raw_author in sorted(authors):
            profile = profiles.get(raw_author, {})
            author_docs = docs_by_author.get(raw_author, [])
            author_summaries.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "avatarUrl": _cached_author_avatar_api_url(raw_author, _github_username_for_avatar_fetch(raw_author, profile), profile),
                    "months": _analytics_year_months(author_docs, year),
                }
            )

        return {
            "year": year,
            "authors": sorted(author_summaries, key=lambda item: item["displayName"].lower()),
        }


def _with_source_breakdowns(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    activity_counts_by_source = item.pop("_activityCountsBySource", {})
    saved_prefabs_by_source = item.pop("_savedPrefabsBySource", {})
    overtime_activity_counts_by_source = item.pop("_overtimeActivityCountsBySource", {})
    overtime_saved_prefabs_by_source = item.pop("_overtimeSavedPrefabsBySource", {})

    item["activityMixBySource"] = _activity_mix_source_groups(activity_counts_by_source)
    item["savedPrefabsBySource"] = _saved_prefab_source_groups(saved_prefabs_by_source)
    item["overtimeActivityMixBySource"] = _activity_mix_source_groups(overtime_activity_counts_by_source)
    item["overtimeSavedPrefabsBySource"] = _saved_prefab_source_groups(overtime_saved_prefabs_by_source)
    return item


def _is_live_date_match(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    if _is_author_local_today(value, raw_author, profiles, fallback_time_zone_id, now):
        return True

    if start_date or end_date:
        return _date_in_range(str(value or ""), start_date, end_date)

    return False


def _activity_mix_source_groups(items_by_source: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups = []

    for source, items in items_by_source.items():
        activity_mix = _activity_mix_from_list(items)

        if not activity_mix:
            continue

        groups.append(
            {
                "source": source,
                "totalCount": sum(int(item.get("count", 0)) for item in items),
                "activityMix": activity_mix,
            }
        )

    return sorted(groups, key=lambda item: (-int(item.get("totalCount", 0)), str(item.get("source") or "")))


def _saved_prefab_source_groups(items_by_source: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups = []

    for source, items in items_by_source.items():
        saved_prefabs = sorted(items, key=lambda item: int(item.get("saveCount", 0)), reverse=True)

        if not saved_prefabs:
            continue

        groups.append(
            {
                "source": source,
                "totalSaveCount": sum(int(item.get("saveCount", 0)) for item in saved_prefabs),
                "savedPrefabs": saved_prefabs,
            }
        )

    return sorted(groups, key=lambda item: (-int(item.get("totalSaveCount", 0)), str(item.get("source") or "")))


