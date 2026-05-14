from __future__ import annotations

from ..activity_math import *
from ..hourly_fill_rules import (
    add_report_activity_fill_segments,
    apply_offline_idle_gaps,
    apply_visual_missed_hours,
    apply_visual_overtime_hour_gaps,
    apply_night_overtime_hour_fills,
    apply_night_overtime_missed_end,
    apply_plugin_hour_idle_gaps,
    apply_workday_idle_fill,
    apply_visual_missed_end_fallbacks,
    apply_overtime_start_boundaries,
    apply_breaks_to_hourly_activity,
    apply_meetings_to_hourly_activity,
    convert_hourly_to_vacation_overtime,
    empty_hourly_activity,
    hourly_activity_has_workday_signal,
    mark_break_source_overlaps,
    merge_hourly_activity,
    merge_meeting_buckets_into_hourly_author_rows,
    public_hourly_activity,
    transfer_summary_idle_to_auto_break,
)
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin
from .activity_summary_analytics import ActivitySummaryAnalyticsMixin
from .activity_summary_cache import ActivitySummaryCacheMixin
from .activity_day_summary_snapshots import ActivityDaySummarySnapshotsMixin
from .activity_summary_helpers import _with_source_breakdowns
from .activity_summary_hourly import ActivitySummaryHourlyMixin
from .activity_summary_presence import ActivitySummaryPresenceMixin
from .activity_summary_report_filters import ActivitySummaryReportFiltersMixin
from .activity_summary_reports import ActivitySummaryReportsMixin


def _is_device_profile_raw_author(value: str) -> bool:
    normalized = str(value or "").strip()
    return normalized.startswith("Device") and normalized[6:].isdigit()


class ActivitySummaryService(
    ActivitySummaryReportsMixin,
    ActivitySummaryCacheMixin,
    ActivityDaySummarySnapshotsMixin,
    ActivitySummaryReportFiltersMixin,
    ActivitySummaryHourlyMixin,
    ActivitySummaryPresenceMixin,
    ActivitySummaryAnalyticsMixin,
    MongoComposableMixin,
):
    SUMMARY_CACHE_TTL_SECONDS = 5
    REPORTS_PAGE_SCAN_MULTIPLIER = 5
    REPORTS_PAGE_SCAN_FLOOR = 50
    REPORTS_PAGE_SCAN_CEILING = 1000




    def activity_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        now: dt.datetime | None = None,
        include_profiles: bool = True,
        include_hourly: bool = True,
        include_breakdowns: bool = True,
        raw_author_scope: str | None = None,
    ) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.UTC)
        historical_single_day = bool(start_date and start_date == end_date and not date_mode)
        profiles = composed(self)._profiles_by_raw_author()
        selected_day_is_live = False
        if historical_single_day and start_date:
            live_dates = {now.astimezone(dt.UTC).date().isoformat()}
            for profile in profiles.values():
                live_dates.add(
                    _local_date_for_time_zone(
                        now,
                        _author_time_zone_id(profile.get("rawAuthor"), {}, profile.get("timeZoneId")),
                    )
                )
            selected_day_is_live = start_date in live_dates
        if not historical_single_day or selected_day_is_live:
            composed(self).materialize_live_meeting_reports(now)
        hidden_device_authors = self._hidden_device_authors()
        if historical_single_day:
            selected_daily_device_authors = {
                str(author or "")
                for author in self.db.daily_author_activity.distinct("author", {"date": start_date})
                if _is_device_profile_raw_author(str(author or ""))
            }
            selected_raw_device_authors = {
                str(author or "")
                for collection_name in ("raw_activity_events", "activity_snapshots", "report_rows")
                for author in getattr(self.db, collection_name).distinct("author", {"date": start_date})
                if _is_device_profile_raw_author(str(author or ""))
            }
            hidden_device_authors -= selected_daily_device_authors - selected_raw_device_authors
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
        meeting_active_overlap_by_author_date: dict[tuple[str, str], int] = {}
        meeting_plugin_overlap_by_author_date: dict[tuple[str, str], int] = {}
        break_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        meeting_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        meeting_overlap_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        summary_dates_by_author: dict[str, set[str]] = {}
        daily_items = sorted(
            self.db.daily_author_activity.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0}),
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("lastRecordedAt") or item.get("lastReceivedAt") or ""),
                str(item.get("source") or ""),
            ),
        )
        scoped_raw_author = str(raw_author_scope or "").strip()
        if scoped_raw_author:
            daily_items = [
                item
                for item in daily_items
                if composed(self).resolve_author_alias(str(item.get("author") or "Unknown User")) == scoped_raw_author
            ]
        if date_mode == "authorLocalToday":
            daily_items = [
                item
                for item in daily_items
                if composed(self).publisher_live_date_in_scope(item, profiles, now, start_date, end_date)
                or live_date_in_scope(
                    item.get("date"),
                    item.get("author") or "Unknown User",
                    profiles,
                    item.get("timeZoneId"),
                    now,
                    start_date,
                    end_date,
                )
            ]
        report_rows_by_daily_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        report_projection = {
            "_id": 0,
            "author": 1,
            "date": 1,
            "source": 1,
            "recordedAt": 1,
            "activeDeltaSeconds": 1,
            "overtimeActiveDeltaSeconds": 1,
        }
        report_rows_for_daily_items = list(
            self.db.report_rows.find(_report_date_query(start_date, end_date, date_mode, profiles, now), report_projection).sort("recordedAt", 1)
        )
        if scoped_raw_author:
            report_rows_for_daily_items = [
                row
                for row in report_rows_for_daily_items
                if composed(self).resolve_author_alias(str(row.get("author") or "Unknown User")) == scoped_raw_author
            ]
        for row in report_rows_for_daily_items:
            report_rows_by_daily_key.setdefault(
                (str(row.get("author") or "Unknown User"), str(row.get("date") or ""), str(row.get("source") or "")),
                [],
            ).append(row)
        break_buckets = composed(self)._break_buckets_for_daily_items(daily_items)
        mark_break_source_overlaps(break_buckets, daily_items)
        meeting_buckets = composed(self)._meeting_buckets_for_daily_items(daily_items, now)
        telegram_gaps = composed(self)._telegram_gaps_for_daily_items(daily_items)
        telegram_gap_counted: set[tuple[str, str]] = set()

        for item in daily_items:
            source_raw_author = item.get("author") or "Unknown User"
            raw_author = composed(self).resolve_author_alias(source_raw_author)
            if source_raw_author in hidden_device_authors:
                continue

            item_date = item.get("date") or ""
            author_date_key = (raw_author, item_date)
            if item_date:
                summary_dates_by_author.setdefault(raw_author, set()).add(item_date)
            profile = profiles.get(raw_author, {})
            display_name = _display_name(raw_author, profile)
            source_hourly_activity = item.get("hourlyActivity", [])
            source_reports = report_rows_by_daily_key.get((str(source_raw_author), str(item_date), str(item.get("source") or "")), [])
            add_report_activity_fill_segments(
                source_hourly_activity,
                source_reports,
                _author_time_zone_id(raw_author, profiles, item.get("timeZoneId")),
            )
            hourly_activity = apply_breaks_to_hourly_activity(
                source_hourly_activity,
                break_buckets.get(author_date_key, []),
                break_consumed_by_author_date_hour.setdefault(author_date_key, empty_hourly_activity()),
            )
            pre_meeting_active_seconds = sum(int(hour.get("activeSeconds", 0)) for hour in hourly_activity)
            pre_meeting_plugin_seconds = sum(
                int(hour.get("activeSeconds", 0))
                + int(hour.get("idleSeconds", 0))
                + int(hour.get("overtimeActiveSeconds", 0))
                for hour in hourly_activity
            )
            should_count_meeting_overlap = item.get("source") not in {"telegram", "discord", "status"} and item.get("reportType") not in {
                "telegram",
                "meeting",
                "status",
            }
            meeting_overlap_activity = (
                apply_meetings_to_hourly_activity(
                    hourly_activity,
                    meeting_buckets.get(author_date_key, []),
                    meeting_overlap_consumed_by_author_date_hour.setdefault(author_date_key, empty_hourly_activity()),
                )
                if should_count_meeting_overlap
                else hourly_activity
            )
            post_meeting_active_seconds = sum(int(hour.get("activeSeconds", 0)) for hour in meeting_overlap_activity)
            post_meeting_plugin_seconds = sum(
                int(hour.get("activeSeconds", 0))
                + int(hour.get("idleSeconds", 0))
                + int(hour.get("overtimeActiveSeconds", 0))
                for hour in meeting_overlap_activity
            )
            hourly_activity = apply_meetings_to_hourly_activity(
                hourly_activity,
                meeting_buckets.get(author_date_key, []),
                meeting_consumed_by_author_date_hour.setdefault(author_date_key, empty_hourly_activity()),
            )
            meeting_active_overlap_by_author_date[author_date_key] = meeting_active_overlap_by_author_date.get(
                author_date_key, 0
            ) + max(0, pre_meeting_active_seconds - post_meeting_active_seconds)
            meeting_plugin_overlap_by_author_date[author_date_key] = meeting_plugin_overlap_by_author_date.get(
                author_date_key, 0
            ) + max(0, pre_meeting_plugin_seconds - post_meeting_plugin_seconds)
            vacation_mark = composed(self).vacation_mark_for_author_date(raw_author, item_date)
            telegram_gap = telegram_gaps.get(author_date_key, {})
            telegram_gap_hours = telegram_gap.get("hourlyActivity", [])
            can_apply_telegram_gap = item.get("source") != "telegram"
            telegram_gap_seconds = (
                int(telegram_gap.get("seconds", 0))
                if can_apply_telegram_gap and author_date_key not in telegram_gap_counted
                else 0
            )

            if telegram_gap_seconds:
                merge_hourly_activity(hourly_activity, telegram_gap_hours)
                telegram_gap_counted.add(author_date_key)

            if vacation_mark:
                hourly_activity = convert_hourly_to_vacation_overtime(hourly_activity)

            report_active_seconds = int(item.get("activeSeconds", 0))
            report_idle_seconds = sum(int(hour.get("idleSeconds", 0)) for hour in hourly_activity)
            plugin_idle_seconds = max(0, report_idle_seconds - telegram_gap_seconds)
            raw_plugin_day_seconds = max(0, int(item.get("activeSeconds", 0)) + int(item.get("idleSeconds", 0)))
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
            plugin_day_seconds = min(max(0, report_active_seconds + plugin_idle_seconds), normal_available)
            effective_active_seconds = min(report_active_seconds, plugin_day_seconds)
            effective_idle_seconds = max(0, report_idle_seconds)
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
            overtime_saved_prefab_items = _overtime_saved_prefabs_for_summary_item(item)

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
                has_presence_signal = self._daily_item_has_presence_signal(item)
                author_row = {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or item.get("authorEmail", ""),
                    "displayName": display_name,
                    "team": profile.get("team", ""),
                    "profileType": profile.get("profileType") or "person",
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
                    "lastRecordedAt": item.get("lastRecordedAt") if has_presence_signal else "",
                    "lastReceivedAt": _iso(item.get("lastReceivedAt")) if has_presence_signal else "",
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
                    "_activeSecondsBySource": {},
                    "_savedPrefabsBySource": {},
                    "_overtimeActivityCountsBySource": {},
                    "_overtimeActiveSecondsBySource": {},
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
                author_row["_overtimeActiveSecondsBySource"][source_key] = (
                    int(author_row["_overtimeActiveSecondsBySource"].get(source_key, 0)) + effective_active_seconds
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
                author_row["_activeSecondsBySource"][source_key] = (
                    int(author_row["_activeSecondsBySource"].get(source_key, 0)) + effective_active_seconds
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
            author_row["_overtimeActiveSecondsBySource"][source_key] = (
                int(author_row["_overtimeActiveSecondsBySource"].get(source_key, 0)) + int(item.get("overtimeActiveSeconds", 0))
            )
            author_row["_overtimeSavedPrefabsBySource"][source_key] = _merge_count_list(
                author_row["_overtimeSavedPrefabsBySource"].get(source_key, []),
                overtime_saved_prefab_items,
                "path",
                "saveCount",
            )

            has_presence_signal = self._daily_item_has_presence_signal(item)

            if has_presence_signal and str(item.get("lastRecordedAt") or "") > str(author_row.get("lastRecordedAt") or ""):
                author_row["lastRecordedAt"] = item.get("lastRecordedAt")

            item_last_received_at = _coerce_datetime(item.get("lastReceivedAt"))
            author_last_received_at = _coerce_datetime(author_row.get("lastReceivedAt"))

            if has_presence_signal and item_last_received_at and (
                not author_last_received_at or item_last_received_at > author_last_received_at
            ):
                author_row["lastReceivedAt"] = _iso(item.get("lastReceivedAt")) or item.get("lastReceivedAt")

            current_author = hourly_by_author.get(raw_author)

            if not current_author:
                current_author = {
                    "author": display_name,
                    "rawAuthor": raw_author,
                    "timeZoneId": item.get("timeZoneId"),
                    "timeZoneDisplayName": item.get("timeZoneDisplayName"),
                    "hourlyActivity": empty_hourly_activity(),
                }
                hourly_by_author[raw_author] = current_author
            else:
                current_author["timeZoneId"] = current_author.get("timeZoneId") or item.get("timeZoneId")
                current_author["timeZoneDisplayName"] = current_author.get("timeZoneDisplayName") or item.get(
                    "timeZoneDisplayName"
                )

            merge_hourly_activity(current_author["hourlyActivity"], hourly_activity)

        for author_date_key, meeting_seconds in meeting_seconds_by_author_date.items():
            meeting_active_addition = max(
                0,
                int(meeting_seconds) - int(meeting_active_overlap_by_author_date.get(author_date_key, 0)),
            )
            meeting_plugin_addition = max(
                0,
                int(meeting_seconds) - int(meeting_plugin_overlap_by_author_date.get(author_date_key, 0)),
            )

            if meeting_active_addition <= 0 and meeting_plugin_addition <= 0:
                continue

            raw_author, _item_date = author_date_key
            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                continue

            work_window_seconds = DEFAULT_PLUGIN_WORK_WINDOW_SECONDS
            normal_consumed = normal_consumed_by_author_date.get(author_date_key, 0)
            normal_available = max(0, work_window_seconds - normal_consumed)
            effective_meeting_plugin_addition = min(meeting_plugin_addition, normal_available)
            effective_meeting_active_addition = meeting_active_addition
            normal_consumed_by_author_date[author_date_key] = normal_consumed + effective_meeting_plugin_addition

            author_row["pluginDaySeconds"] += effective_meeting_plugin_addition
            author_row["rawPluginDaySeconds"] += effective_meeting_plugin_addition
            author_row["activeSeconds"] += effective_meeting_active_addition
            totals["pluginDaySeconds"] += effective_meeting_plugin_addition
            totals["rawPluginDaySeconds"] += effective_meeting_plugin_addition
            totals["activeSeconds"] += effective_meeting_active_addition

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
        meeting_hourly_adjustments = merge_meeting_buckets_into_hourly_author_rows(hourly_by_author, meeting_buckets, profiles, _display_name)

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
                author_row["pluginDaySeconds"] = max(0, int(author_row.get("pluginDaySeconds", 0)) - applied_reduction)
                author_row["rawPluginDaySeconds"] = max(0, int(author_row.get("rawPluginDaySeconds", 0)) - applied_reduction)
                totals["idleSeconds"] = max(0, int(totals.get("idleSeconds", 0)) - applied_reduction)
                totals["pluginDaySeconds"] = max(0, int(totals.get("pluginDaySeconds", 0)) - applied_reduction)
                totals["rawPluginDaySeconds"] = max(0, int(totals.get("rawPluginDaySeconds", 0)) - applied_reduction)

        if not historical_single_day:
            for raw_author in composed(self).list_authors():
                if raw_author in hidden_device_authors:
                    continue

                composed(self)._ensure_summary_author(authors_by_raw, raw_author, profiles)

        for raw_author, author_row in authors_by_raw.items():
            if raw_author in hourly_by_author:
                continue

            hourly_by_author[raw_author] = {
                "author": author_row["displayName"],
                "rawAuthor": raw_author,
                "timeZoneId": profiles.get(raw_author, {}).get("timeZoneId"),
                "timeZoneDisplayName": profiles.get(raw_author, {}).get("timeZoneDisplayName"),
                "hourlyActivity": empty_hourly_activity(),
            }

        for raw_author in hidden_device_authors:
            authors_by_raw.pop(raw_author, None)
            hourly_by_author.pop(raw_author, None)

        if historical_single_day:
            for raw_author in list(hourly_by_author):
                if raw_author not in authors_by_raw:
                    hourly_by_author.pop(raw_author, None)
            if not authors_by_raw:
                for raw_author in {
                    composed(self).resolve_author_alias(str(author or "Unknown User"))
                    for author in self.db.raw_activity_events.distinct("author", {"date": start_date})
                    if str(author or "").strip()
                }:
                    if raw_author in profiles:
                        composed(self)._ensure_summary_author(authors_by_raw, raw_author, profiles)
                for raw_author, profile in profiles.items():
                    if not str(profile.get("telegramUsername") or "").strip():
                        continue
                    composed(self)._ensure_summary_author(authors_by_raw, raw_author, profiles)

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
        self._apply_workday_idle_fill(
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
        latest_report_by_author_date = self._latest_report_times_by_author_date(start_date, end_date, date_mode, profiles, now)
        apply_night_overtime_missed_end(
            hourly_by_author,
            latest_report_by_author_date,
            time_zone_id_for_author=lambda raw_author, report_time_zone_id: _author_time_zone_id(raw_author, profiles, report_time_zone_id),
        )
        apply_night_overtime_hour_fills(hourly_by_author)
        apply_overtime_start_boundaries(
            hourly_by_author,
            list(self.db.day_sessions.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0})),
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
        )
        apply_visual_missed_end_fallbacks(
            hourly_by_author,
            list(self.db.day_sessions.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0})),
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
        )
        self._apply_latest_report_metadata(
            authors_by_raw,
            start_date,
            end_date,
            date_mode,
            profiles,
            now,
        )
        self._sync_author_idle_totals_from_hourly(authors_by_raw, hourly_by_author, totals)
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
            base_author = _with_source_breakdowns(_with_activity_mix(_with_productivity(author)))

            if composed(self).is_publisher_profile(raw_author, profiles):
                summary_author = composed(self).with_publisher_device_presence(base_author, send_interval_seconds, now)
            else:
                summary_author = _with_author_presence(
                    base_author,
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
            {**item, "hourlyActivity": public_hourly_activity(item.get("hourlyActivity", []))}
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
