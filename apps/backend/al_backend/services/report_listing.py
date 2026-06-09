from __future__ import annotations

from typing import Any

from ..activity_math import (
    DESCENDING,
    dt,
    live_date_in_scope,
    _display_name,
    _iso,
    _normalize_report_hour_filter,
    _report_date_query,
    _report_matches_hour_filter,
    _report_table_sort_key,
)
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class ReportListingService(MongoComposableMixin):
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
        reports = []
        projection = self._reports_projection()

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
            row_context = self._report_row_author_context(item, profiles)

            if not self._report_row_visible_in_scope(
                item,
                row_context,
                profiles,
                now,
                start_date,
                end_date,
                date_mode,
                None,
                None,
                meeting_lookup,
                break_lookup,
            ):
                continue

            profile = row_context["profile"]
            item["displayName"] = _display_name(item.get("author"), profile)
            item["team"] = profile.get("team", "")
            item["receivedAt"] = _iso(item.get("receivedAt"))
            reports.append(item)

            if limit is not None and len(reports) >= limit:
                break

        return reports

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
        query, profiles, now = self._reports_query_context(start_date, end_date, date_mode, author, source)
        source_query, _, _ = self._reports_query_context(start_date, end_date, date_mode, author)
        status_rows = list(self.db.report_rows.find({**source_query, "source": "status"}, self._reports_projection()))
        status_intervals = self._status_intervals_for_reports(status_rows)
        candidate_rows = self._bounded_report_rows_for_page(query, self._reports_projection(), offset, limit, status_intervals)
        source_candidate_rows = list(self.db.report_rows.find(source_query, self._reports_projection()))
        meeting_lookup = self._meeting_lookup_for_reports(candidate_rows)
        break_lookup = self._break_lookup_for_reports(candidate_rows)
        uses_post_filters = date_mode == "authorLocalToday" or self._reports_page_uses_post_filters(
            hour, status_intervals, candidate_rows, meeting_lookup, break_lookup
        )
        source_meeting_lookup = self._meeting_lookup_for_reports(source_candidate_rows)
        source_break_lookup = self._break_lookup_for_reports(source_candidate_rows)
        sources = sorted(
            {
                str(item.get("source") or "")
                for item in source_candidate_rows
                if item.get("source")
                and self._report_row_visible_in_scope(
                    item,
                    self._report_row_author_context(item, profiles),
                    profiles,
                    now,
                    start_date,
                    end_date,
                    date_mode,
                    hour,
                    status_intervals,
                    source_meeting_lookup,
                    source_break_lookup,
                )
            },
            key=lambda value: value.lower(),
        )
        reports: list[dict[str, Any]] = []

        if uses_post_filters:
            filtered_rows = self._filtered_reports_page_rows(
                query,
                profiles,
                now,
                start_date,
                end_date,
                date_mode,
                hour,
                status_intervals,
            )
            total = len(filtered_rows)
            reports = [self._enrich_report_table_row(item, profiles) for item in filtered_rows[offset : offset + limit]]
        else:
            total = 0
            for item in candidate_rows:
                row_context = self._report_row_author_context(item, profiles)

                if not self._report_row_visible_in_scope(
                    item,
                    row_context,
                    profiles,
                    now,
                    start_date,
                    end_date,
                    date_mode,
                    hour,
                    status_intervals,
                    meeting_lookup,
                    break_lookup,
                ):
                    continue

                if total >= offset and len(reports) < limit:
                    reports.append(self._enrich_report_table_row(item, profiles, row_context))

                total += 1

            total = self.db.report_rows.count_documents(query)

        return {
            "reports": reports,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sources": sources,
        }

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

    def _report_row_author_context(
        self,
        item: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        row_author = str(item.get("author") or "Unknown User")
        resolved_author = composed(self).resolve_author_alias(row_author)
        profile = profiles.get(resolved_author, {})

        return {
            "row_author": row_author,
            "resolved_author": resolved_author,
            "profile": profile,
            "timeZoneId": profile.get("timeZoneId") or item.get("timeZoneId"),
        }

    def _report_row_visible_in_scope(
        self,
        item: dict[str, Any],
        row_context: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        now: dt.datetime,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        hour: int | None,
        status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]] | None,
        meeting_lookup: dict[str, dict[str, list[dict[str, Any]]]],
        break_lookup: dict[str, dict[str, list[dict[str, Any]]]],
    ) -> bool:
        if date_mode == "authorLocalToday" and not _is_live_date_match(
            item.get("date"),
            row_context["resolved_author"],
            profiles,
            row_context["timeZoneId"],
            now,
            start_date,
            end_date,
        ):
            return False

        canonical_item = {
            **item,
            "author": row_context["resolved_author"],
            "timeZoneId": row_context["timeZoneId"],
        }

        if not _report_matches_hour_filter(canonical_item, profiles, hour):
            return False

        if status_intervals and self._is_report_inside_status_interval(item, status_intervals):
            return False

        return not (
            self._is_empty_plugin_report_without_signal(item)
            or self._is_idle_report_during_meeting(item, meeting_lookup)
            or self._is_idle_report_during_break(item, break_lookup)
        )

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

    def _filtered_reports_page_rows(
        self,
        query: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        now: dt.datetime,
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        hour: int | None,
        status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]],
    ) -> list[dict[str, Any]]:
        rows = list(self.db.report_rows.find(query, self._reports_projection()))
        rows.sort(key=lambda row: _report_table_sort_key(row, status_intervals), reverse=True)
        meeting_lookup = self._meeting_lookup_for_reports(rows)
        break_lookup = self._break_lookup_for_reports(rows)
        return [
            item
            for item in rows
            if self._report_row_visible_in_scope(
                item,
                self._report_row_author_context(item, profiles),
                profiles,
                now,
                start_date,
                end_date,
                date_mode,
                hour,
                status_intervals,
                meeting_lookup,
                break_lookup,
            )
        ]

    def _enrich_report_table_row(
        self,
        item: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        row_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = dict(item)
        context = row_context or self._report_row_author_context(row, profiles)
        resolved_author = context["resolved_author"]
        profile = context["profile"]
        row["displayName"] = _display_name(resolved_author, profile)
        row["team"] = profile.get("team", "")
        row["timeZoneId"] = profile.get("timeZoneId") or row.get("timeZoneId")
        row["timeZoneDisplayName"] = profile.get("timeZoneDisplayName") or row.get("timeZoneDisplayName")
        row["receivedAt"] = _iso(row.get("receivedAt"))
        return row

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


def _is_live_date_match(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    return live_date_in_scope(value, raw_author, profiles, fallback_time_zone_id, now, start_date, end_date)
