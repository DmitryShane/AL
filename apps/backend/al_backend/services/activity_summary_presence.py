from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class ActivitySummaryPresenceMixin:
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

            if not self._daily_item_has_presence_signal(daily):
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
                    author_row["_lastReportReceivedAt"] = _iso(profile_raw_dt)

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
