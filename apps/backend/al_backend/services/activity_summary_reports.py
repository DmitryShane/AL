from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed
from .activity_summary_helpers import _is_live_date_match


class ActivitySummaryReportsMixin:
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
