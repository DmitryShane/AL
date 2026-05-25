from __future__ import annotations

from ..activity_math import *


class ActivitySummaryReportFiltersMixin:
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

        if report_row.get("source") == "telegram" or report_row.get("reportType") == "telegram":
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

        if report_row.get("source") == "codex":
            return not (
                report_row.get("savedPrefabDeltas")
                or report_row.get("overtimeSavedPrefabDeltas")
            )

        return not (
            report_row.get("activityCountDeltas")
            or report_row.get("savedPrefabDeltas")
            or report_row.get("overtimeActivityCountDeltas")
            or report_row.get("overtimeSavedPrefabDeltas")
        )

    def _daily_item_has_presence_signal(self, item: dict[str, Any]) -> bool:
        if item.get("source") in {"discord", "telegram", "status"} or item.get("reportType") in {"meeting", "telegram", "status"}:
            return True

        if any(
            _time_microseconds(item, seconds_key, micros_key) > 0
            for seconds_key, micros_key in (
                ("activeSeconds", "activeMicroseconds"),
                ("idleSeconds", "idleMicroseconds"),
                ("breakSeconds", "breakMicroseconds"),
                ("overtimeActiveSeconds", "overtimeActiveMicroseconds"),
            )
        ):
            return True

        return bool(
            item.get("activityCounts")
            or item.get("savedPrefabs")
            or item.get("overtimeActivityCounts")
            or item.get("overtimeSavedPrefabs")
        )
