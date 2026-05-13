from __future__ import annotations

from ..activity_math import *
from ..hourly_fill_rules import (
    apply_offline_idle_gaps,
    apply_visual_missed_hours,
    apply_visual_overtime_hour_gaps,
    apply_night_overtime_missed_end,
    apply_plugin_hour_idle_gaps,
    apply_workday_idle_fill,
    apply_overtime_start_boundaries,
    convert_hourly_to_vacation_overtime,
    empty_hourly_activity,
    hourly_activity_has_workday_signal,
    public_hourly_activity,
    transfer_summary_idle_to_auto_break,
)
from ..backend_composable_host import composed
from .activity_summary_helpers import _is_device_profile_raw_author


class ActivitySummaryHourlyMixin:
    def _sync_author_idle_totals_from_hourly(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
    ) -> None:
        synced_total = 0

        for raw_author, author_row in authors_by_raw.items():
            hourly_author = hourly_by_author.get(raw_author)

            if not hourly_author:
                synced_total += int(author_row.get("idleSeconds", 0))
                continue

            public_hours = public_hourly_activity(hourly_author.get("hourlyActivity", []))
            source_hours = hourly_author.get("hourlyActivity", [])
            idle_seconds = 0

            for public_hour, source_hour in zip(public_hours, source_hours, strict=False):
                public_idle_seconds = int(public_hour.get("totals", {}).get("idleSeconds", 0))
                visual_idle_seconds = 0

                if int(author_row.get("breakSeconds", 0)) <= 0:
                    visual_idle_seconds += int(source_hour.get("pluginHourGapIdleSeconds", 0))

                idle_seconds += max(0, public_idle_seconds - visual_idle_seconds)

            previous_idle_seconds = int(author_row.get("idleSeconds", 0))
            if (
                idle_seconds < previous_idle_seconds
                and int(author_row.get("meetingSeconds", 0)) > 0
                and int(author_row.get("telegramToFirstActivitySeconds", 0)) <= 0
            ):
                idle_seconds = previous_idle_seconds

            idle_delta = idle_seconds - previous_idle_seconds
            author_row["idleSeconds"] = idle_seconds
            author_row["pluginDaySeconds"] = max(0, int(author_row.get("pluginDaySeconds", 0)) + idle_delta)
            author_row["rawPluginDaySeconds"] = max(0, int(author_row.get("rawPluginDaySeconds", 0)) + idle_delta)
            synced_total += idle_seconds

        totals["idleSeconds"] = synced_total
        totals["pluginDaySeconds"] = sum(int(item.get("pluginDaySeconds", 0)) for item in authors_by_raw.values())
        totals["rawPluginDaySeconds"] = sum(int(item.get("rawPluginDaySeconds", 0)) for item in authors_by_raw.values())

    def _hidden_device_authors(self) -> set[str]:
        device_authors = {
            str(author or "")
            for collection_name in ("daily_author_activity", "raw_activity_events", "activity_snapshots", "report_rows")
            for author in getattr(self.db, collection_name).distinct("author")
            if _is_device_profile_raw_author(str(author or ""))
        }
        active_device_authors = {
            str(item.get("rawAuthor") or "")
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1})
            if item.get("rawAuthor")
        }
        alias_device_authors = {
            str(item.get("sourceRawAuthor") or "")
            for item in self.db.author_aliases.find({}, {"_id": 0, "sourceRawAuthor": 1})
            if _is_device_profile_raw_author(str(item.get("sourceRawAuthor") or ""))
        }
        return device_authors - active_device_authors - alias_device_authors

    def _apply_summary_auto_breaks(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        summary_dates_by_author: dict[str, set[str]],
        auto_break_applied_by_author: dict[str, int] | None = None,
    ) -> None:
        applied_by_author = auto_break_applied_by_author if auto_break_applied_by_author is not None else {}

        for raw_author, hourly_author in hourly_by_author.items():
            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                continue

            remaining_seconds = self._summary_auto_break_remaining_seconds(
                raw_author,
                profiles,
                summary_dates_by_author.get(raw_author, set()),
            )
            remaining_seconds = max(0, remaining_seconds - int(applied_by_author.get(raw_author, 0)))

            if remaining_seconds <= 0:
                continue

            transferred_seconds = transfer_summary_idle_to_auto_break(
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
            applied_by_author[raw_author] = int(applied_by_author.get(raw_author, 0)) + transferred_seconds

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
                hourly_author["hourlyActivity"] = convert_hourly_to_vacation_overtime(
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
        apply_plugin_hour_idle_gaps(
            authors_by_raw,
            hourly_by_author,
            latest_report_by_author_date,
            time_zone_id_for_author=lambda raw_author, time_zone_id: _author_time_zone_id(raw_author, profiles, time_zone_id),
            is_date_in_scope=lambda day_date, raw_author, time_zone_id: _date_in_summary_scope(
                day_date,
                raw_author,
                profiles,
                time_zone_id,
                now,
                start_date,
                end_date,
                date_mode,
            ),
            is_vacation_day=lambda raw_author, day_date: composed(self).is_vacation_day(raw_author, day_date),
        )

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
        apply_offline_idle_gaps(
            authors_by_raw,
            hourly_by_author,
            list(self.db.day_sessions.find(session_query, {"_id": 0})),
            latest_report_by_author_date,
            default_plugin_work_window_seconds=DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
            time_zone_id_for_author=lambda raw_author, time_zone_id: _author_time_zone_id(raw_author, profiles, time_zone_id),
            is_date_in_scope=lambda day_date, raw_author, time_zone_id: _date_in_summary_scope(
                day_date,
                raw_author,
                profiles,
                time_zone_id,
                now,
                start_date,
                end_date,
                date_mode,
            ),
            is_vacation_day=lambda raw_author, day_date: composed(self).is_vacation_day(raw_author, day_date),
        )

    def _apply_workday_idle_fill(
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
        session_query = _report_date_query(start_date, end_date, date_mode, profiles, now)
        sessions = list(self.db.day_sessions.find(session_query, {"_id": 0}))
        latest_signal_by_author_date = self._latest_workday_signal_times_by_author_date(
            start_date,
            end_date,
            date_mode,
            profiles,
            now,
        )

        for session in sessions:
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            day_date = str(session.get("date") or "")
            time_zone_id = _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))

            if not day_date or not _date_in_summary_scope(day_date, raw_author, profiles, time_zone_id, now, start_date, end_date, date_mode):
                continue

            if composed(self).is_vacation_day(raw_author, day_date):
                continue

            hourly_author = hourly_by_author.get(raw_author)

            if not hourly_author:
                continue

            hourly_activity = hourly_author.get("hourlyActivity", [])
            added_idle_seconds = apply_workday_idle_fill(
                hourly_activity,
                _coerce_datetime(session.get("startedAt")),
                _coerce_datetime(session.get("lastOfflineAt")) or latest_signal_by_author_date.get((raw_author, day_date)),
                time_zone_id,
                hourly_activity_has_workday_signal(hourly_activity),
            )

            if added_idle_seconds <= 0:
                continue

            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                continue

            author_row["idleSeconds"] = int(author_row.get("idleSeconds", 0)) + added_idle_seconds
            author_row["pluginDaySeconds"] = int(author_row.get("pluginDaySeconds", 0)) + added_idle_seconds
            author_row["rawPluginDaySeconds"] = int(author_row.get("rawPluginDaySeconds", 0)) + added_idle_seconds
            totals["idleSeconds"] = int(totals.get("idleSeconds", 0)) + added_idle_seconds
            totals["pluginDaySeconds"] = int(totals.get("pluginDaySeconds", 0)) + added_idle_seconds
            totals["rawPluginDaySeconds"] = int(totals.get("rawPluginDaySeconds", 0)) + added_idle_seconds

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
        apply_visual_missed_hours(
            hourly_by_author,
            list(self.db.day_sessions.find(session_query, {"_id": 0})),
            latest_report_by_author_date,
            include_start=include_start,
            include_end=include_end,
            time_zone_id_for_author=lambda raw_author, session_time_zone_id: _author_time_zone_id(
                raw_author,
                profiles,
                session_time_zone_id,
            ),
            is_date_in_scope=lambda day_date, raw_author, time_zone_id: _date_in_summary_scope(
                day_date,
                raw_author,
                profiles,
                time_zone_id,
                now,
                start_date,
                end_date,
                date_mode,
            ),
            is_vacation_day=lambda raw_author, day_date: composed(self).is_vacation_day(raw_author, day_date),
            display_name_for_author=lambda raw_author: _display_name(raw_author, profiles.get(raw_author, {})),
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

    def _latest_activity_signal_times_by_author_date(
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
                "activeDeltaSeconds": 1,
                "idleDeltaSeconds": 1,
                "overtimeActiveDeltaSeconds": 1,
                "activityCountDeltas": 1,
                "savedPrefabDeltas": 1,
                "overtimeActivityCountDeltas": 1,
                "overtimeSavedPrefabDeltas": 1,
            },
        ):
            if report.get("source") in {"telegram", "status"} or report.get("reportType") in {"telegram", "status"}:
                continue

            if report.get("source") != "discord" and report.get("reportType") != "meeting" and self._is_empty_plugin_report_without_signal(report):
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

    def _latest_workday_signal_times_by_author_date(
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
                "activeDeltaSeconds": 1,
                "idleDeltaSeconds": 1,
                "overtimeActiveDeltaSeconds": 1,
                "activityCountDeltas": 1,
                "savedPrefabDeltas": 1,
                "overtimeActivityCountDeltas": 1,
                "overtimeSavedPrefabDeltas": 1,
            },
        ):
            if report.get("source") not in {"telegram", "status", "discord"} and report.get("reportType") not in {
                "telegram",
                "status",
                "meeting",
            } and self._is_empty_plugin_report_without_signal(report):
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
        apply_visual_overtime_hour_gaps(
            hourly_by_author,
            list(
                self.db.report_rows.find(
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
                )
            ),
            list(self.db.day_sessions.find(query, {"_id": 0})),
            time_zone_id_for_author=lambda raw_author, report_time_zone_id: _author_time_zone_id(raw_author, profiles, report_time_zone_id),
            is_date_in_scope=lambda day_date, raw_author, time_zone_id: _date_in_summary_scope(
                day_date,
                raw_author,
                profiles,
                time_zone_id,
                now,
                start_date,
                end_date,
                date_mode,
            ),
            is_vacation_day=lambda raw_author, day_date: composed(self).is_vacation_day(raw_author, day_date),
        )
