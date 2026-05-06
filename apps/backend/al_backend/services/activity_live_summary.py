from __future__ import annotations

from typing import Any

from ..activity_math import *
from ..mongo_composable import MongoComposableMixin


class ActivityLiveSummaryService(MongoComposableMixin):
    def _break_buckets_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, int]]]:
        author_dates = {
            (str(item.get("author") or "Unknown User"), str(item.get("date") or ""))
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        profiles = self._profiles_by_raw_author()
        min_start = _date_start(dates[0]) - dt.timedelta(days=1)
        max_end = _date_start(dates[-1]) + dt.timedelta(days=2)
        buckets = {key: _empty_hourly_activity() for key in author_dates}

        interval_query = {
            "rawAuthor": {"$in": authors},
            "startedAt": {"$lt": max_end},
            "endedAt": {"$gt": min_start},
        }

        for interval in self.db.break_intervals.find(interval_query, {"_id": 0}):
            _add_break_interval_to_buckets(
                buckets,
                interval.get("rawAuthor"),
                _coerce_datetime(interval.get("startedAt")),
                _coerce_datetime(interval.get("endedAt")),
                _author_time_zone_id(interval.get("rawAuthor"), profiles, interval.get("timeZoneId")),
            )

        now = dt.datetime.now(dt.UTC)

        for session in self.db.break_sessions.find({"rawAuthor": {"$in": authors}}, {"_id": 0}):
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            _add_break_interval_to_buckets(
                buckets,
                session.get("rawAuthor"),
                started_at,
                now,
                _author_time_zone_id(session.get("rawAuthor"), profiles, session.get("timeZoneId")),
            )

        return buckets

    def _meeting_buckets_for_daily_items(
        self, daily_items: list[dict[str, Any]], now: dt.datetime | None = None
    ) -> dict[tuple[str, str], list[dict[str, int]]]:
        author_dates = {
            (str(item.get("author") or "Unknown User"), str(item.get("date") or ""))
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        profiles = self._profiles_by_raw_author()
        min_start = _date_start(dates[0]) - dt.timedelta(days=1)
        max_end = _date_start(dates[-1]) + dt.timedelta(days=2)
        buckets = {key: _empty_hourly_activity() for key in author_dates}

        for item in daily_items:
            key = (str(item.get("author") or "Unknown User"), str(item.get("date") or ""))
            target = buckets.get(key)

            if not target:
                continue

            for hour in item.get("hourlyActivity") or []:
                hour_index = int(hour.get("hour", 0))
                meeting_seconds = int(hour.get("meetingSeconds", 0))

                if meeting_seconds > 0 and 0 <= hour_index < len(target):
                    target[hour_index]["meetingSeconds"] = int(target[hour_index].get("meetingSeconds", 0)) + meeting_seconds

        interval_query = {
            "rawAuthor": {"$in": authors},
            "startedAt": {"$lt": max_end},
            "endedAt": {"$gt": min_start},
        }

        for interval in self.db.meeting_intervals.find(interval_query, {"_id": 0}):
            _add_meeting_interval_to_buckets(
                buckets,
                interval.get("rawAuthor"),
                _coerce_datetime(interval.get("startedAt")),
                _coerce_datetime(interval.get("endedAt")),
                _author_time_zone_id(interval.get("rawAuthor"), profiles, interval.get("timeZoneId")),
            )

        return buckets

    def _telegram_gaps_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        author_dates = {
            (str(item.get("author") or "Unknown User"), str(item.get("date") or ""))
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        first_online_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        latest_offline_by_key: dict[tuple[str, str], dt.datetime] = {}
        first_activity_by_key: dict[tuple[str, str], dt.datetime] = {}

        def is_after_first_online(key: tuple[str, str], occurred_at: dt.datetime) -> bool:
            first_online = first_online_by_key.get(key)
            return bool(first_online and occurred_at >= first_online["timestamp"])

        for event in self.db.break_events.find(
            {"rawAuthor": {"$in": authors}, "date": {"$in": dates}, "eventType": {"$in": ["online", "offline"]}},
            {"_id": 0},
        ):
            key = (str(event.get("rawAuthor") or "Unknown User"), str(event.get("date") or ""))

            if key not in author_dates:
                continue

            timestamp = _coerce_datetime(event.get("timestamp"))

            if not timestamp:
                continue

            if event.get("eventType") == "offline":
                if key not in latest_offline_by_key or timestamp > latest_offline_by_key[key]:
                    latest_offline_by_key[key] = timestamp
                continue

            if key not in first_online_by_key or timestamp < first_online_by_key[key]["timestamp"]:
                first_online_by_key[key] = {
                    "timestamp": timestamp,
                    "timeZoneId": str(event.get("timeZoneId") or "UTC"),
                }

        for event in self.db.raw_activity_events.find(
            {"author": {"$in": authors}, "date": {"$in": dates}, "source": {"$nin": ["telegram", "discord"]}},
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "eventType": 1,
                "occurredAtUtc": 1,
                "occurredAtLocal": 1,
                "receivedAt": 1,
            },
        ):
            if str(event.get("eventType") or "") in NON_ACTIVITY_EVENT_TYPES:
                continue

            key = (str(event.get("author") or "Unknown User"), str(event.get("date") or ""))

            if key not in author_dates:
                continue

            occurred_at = (
                _coerce_datetime(event.get("occurredAtUtc"))
                or _coerce_datetime(event.get("occurredAtLocal"))
                or _coerce_datetime(event.get("receivedAt"))
            )

            if occurred_at and not is_after_first_online(key, occurred_at):
                continue

            if occurred_at and (key not in first_activity_by_key or occurred_at < first_activity_by_key[key]):
                first_activity_by_key[key] = occurred_at

        for row in self.db.report_rows.find(
            {"author": {"$in": authors}, "date": {"$in": dates}},
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "reportType": 1,
                "recordedAt": 1,
                "lastRecordedAt": 1,
                "receivedAt": 1,
                "activeDeltaSeconds": 1,
                "idleDeltaSeconds": 1,
                "overtimeActiveDeltaSeconds": 1,
                "activeDeltaMicroseconds": 1,
                "idleDeltaMicroseconds": 1,
                "overtimeActiveDeltaMicroseconds": 1,
            },
        ):
            if row.get("source") in {"telegram", "discord"} or row.get("reportType") in {"telegram", "meeting"}:
                continue

            if not _has_time_delta(row):
                continue

            key = (str(row.get("author") or "Unknown User"), str(row.get("date") or ""))

            if key not in author_dates:
                continue

            if key in first_activity_by_key:
                continue

            occurred_at = (
                _coerce_datetime(row.get("recordedAt"))
                or _coerce_datetime(row.get("lastRecordedAt"))
                or _coerce_datetime(row.get("receivedAt"))
            )

            active_delta_microseconds = _time_microseconds(row, "activeDeltaSeconds", "activeDeltaMicroseconds")
            latest_offline_at = latest_offline_by_key.get(key)

            if occurred_at and active_delta_microseconds > 0 and latest_offline_at and latest_offline_at > occurred_at:
                occurred_at = occurred_at - dt.timedelta(microseconds=active_delta_microseconds)

            if occurred_at and not is_after_first_online(key, occurred_at):
                continue

            if occurred_at and (key not in first_activity_by_key or occurred_at < first_activity_by_key[key]):
                first_activity_by_key[key] = occurred_at

        gaps: dict[tuple[str, str], dict[str, Any]] = {}

        for key, first_activity_at in first_activity_by_key.items():
            first_online = first_online_by_key.get(key)

            if not first_online:
                continue

            first_online_at = first_online["timestamp"]
            gap_seconds = max(0, int((first_activity_at - first_online_at).total_seconds()))

            if gap_seconds <= 0:
                continue

            hourly_activity = _empty_hourly_activity()
            _add_idle_interval_to_buckets(
                hourly_activity,
                first_online_at,
                first_activity_at,
                str(first_online.get("timeZoneId") or "UTC"),
            )
            for hour in hourly_activity:
                hour["telegramToFirstActivityIdleSeconds"] = int(hour.get("idleSeconds", 0))
            gaps[key] = {
                "seconds": gap_seconds,
                "hourlyActivity": hourly_activity,
            }

        return gaps

    def _apply_live_activity_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        telegram_seconds_by_author_date: dict[tuple[str, str], int],
        break_seconds_by_author_date: dict[tuple[str, str], int],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        meeting_seconds_by_author_date: dict[tuple[str, str], int] | None = None,
        meeting_buckets: dict[tuple[str, str], list[dict[str, int]]] | None = None,
    ) -> None:
        meeting_seconds_by_author_date = meeting_seconds_by_author_date if meeting_seconds_by_author_date is not None else {}
        meeting_buckets = meeting_buckets if meeting_buckets is not None else {}
        totals.setdefault("meetingSeconds", 0)
        for session in self.db.day_sessions.find({}, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            day_date = str(session.get("date") or "")
            started_at = _coerce_datetime(session.get("startedAt"))

            if not day_date or not started_at:
                continue

            ended_at = _coerce_datetime(session.get("lastOfflineAt"))

            if ended_at and not _date_in_summary_scope(day_date, raw_author, profiles, None, now, start_date, end_date, date_mode):
                continue

            live_day_seconds = int(session.get("daySeconds", 0))

            if not ended_at:
                uncapped_live_day_seconds = max(0, int((now - started_at).total_seconds()))
                live_day_seconds = min(uncapped_live_day_seconds, TELEGRAM_DAY_REMINDER_SECONDS)
            elif live_day_seconds <= 0:
                live_day_seconds = max(0, int((ended_at - started_at).total_seconds()))

            existing_day_seconds = telegram_seconds_by_author_date.get((raw_author, day_date), 0)
            day_delta_seconds = max(0, live_day_seconds - existing_day_seconds)

            if day_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["daySeconds"] += day_delta_seconds
                author_row["telegramDaySeconds"] += day_delta_seconds
                totals["daySeconds"] += day_delta_seconds
                totals["telegramDaySeconds"] += day_delta_seconds

        for interval in self.db.break_intervals.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0}):
            raw_author = interval.get("rawAuthor") or "Unknown User"
            break_date = interval.get("date") or ""

            if not _date_in_summary_scope(break_date, raw_author, profiles, interval.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            break_seconds = int(interval.get("breakSeconds", 0))
            existing_break_seconds = break_seconds_by_author_date.get((raw_author, break_date), 0)
            break_delta_seconds = max(0, break_seconds - existing_break_seconds)

            if break_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["breakSeconds"] += break_delta_seconds
                totals["breakSeconds"] += break_delta_seconds

        meeting_query = _meeting_interval_date_query(start_date, end_date, date_mode, profiles, now)
        meeting_bucket_keys_from_daily_items = set(meeting_buckets)

        for interval in self.db.meeting_intervals.find(meeting_query, {"_id": 0}):
            raw_author = str(interval.get("rawAuthor") or "Unknown User")
            time_zone_id = _author_time_zone_id(raw_author, profiles, interval.get("timeZoneId"))
            meeting_dates = _meeting_interval_scope_dates(start_date, end_date, date_mode, now, time_zone_id)

            for meeting_date in meeting_dates:
                meeting_key = (raw_author, meeting_date)

                if meeting_key in meeting_bucket_keys_from_daily_items:
                    continue

                interval_bucket = {meeting_key: _empty_hourly_activity()}
                _add_meeting_interval_to_buckets(
                    interval_bucket,
                    raw_author,
                    _coerce_datetime(interval.get("startedAt")),
                    _coerce_datetime(interval.get("endedAt")),
                    time_zone_id,
                )
                meeting_delta_seconds = sum(
                    int(hour.get("meetingSeconds", 0)) for hour in interval_bucket[meeting_key]
                )

                if meeting_delta_seconds <= 0:
                    continue

                existing_meeting_seconds = meeting_seconds_by_author_date.get(meeting_key, 0)
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["meetingSeconds"] += meeting_delta_seconds
                totals["meetingSeconds"] += meeting_delta_seconds
                meeting_seconds_by_author_date[meeting_key] = existing_meeting_seconds + meeting_delta_seconds
                meeting_bucket = meeting_buckets.setdefault(meeting_key, _empty_hourly_activity())
                _merge_hourly_activity(meeting_bucket, interval_bucket[meeting_key])

                hourly_author = hourly_by_author.get(raw_author)

                if not hourly_author:
                    profile = profiles.get(raw_author, {})
                    hourly_author = {
                        "author": _display_name(raw_author, profile),
                        "rawAuthor": raw_author,
                        "timeZoneId": profile.get("timeZoneId") or interval.get("timeZoneId"),
                        "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                        "hourlyActivity": _empty_hourly_activity(),
                    }
                    hourly_by_author[raw_author] = hourly_author

                _merge_hourly_activity(hourly_author["hourlyActivity"], interval_bucket[meeting_key])

        for session in self.db.meeting_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            meeting_date = str(session.get("date") or _telegram_event_date(started_at, _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))))

            if not _date_in_summary_scope(meeting_date, raw_author, profiles, session.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
            author_row["activeMeeting"] = True

        for session in self.db.break_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            break_date = str(session.get("date") or _telegram_event_date(started_at, _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))))

            if not _date_in_summary_scope(break_date, raw_author, profiles, session.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            live_break_seconds = max(0, int((now - started_at).total_seconds()))
            existing_break_seconds = break_seconds_by_author_date.get((raw_author, break_date), 0)
            break_delta_seconds = max(0, live_break_seconds - existing_break_seconds)

            if break_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["breakSeconds"] += break_delta_seconds
                totals["breakSeconds"] += break_delta_seconds
                break_seconds_by_author_date[(raw_author, break_date)] = existing_break_seconds + break_delta_seconds

    def _apply_live_telegram_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        telegram_seconds_by_author_date: dict[tuple[str, str], int],
        break_seconds_by_author_date: dict[tuple[str, str], int],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        meeting_seconds_by_author_date: dict[tuple[str, str], int] | None = None,
        meeting_buckets: dict[tuple[str, str], list[dict[str, int]]] | None = None,
    ) -> None:
        self._apply_live_activity_summary(
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

    def _ensure_summary_author(
        self, authors_by_raw: dict[str, dict[str, Any]], raw_author: str, profiles: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        author_row = authors_by_raw.get(raw_author)

        if author_row:
            return author_row

        profile = profiles.get(raw_author, {})
        author_row = {
            "rawAuthor": raw_author,
            "authorEmail": profile.get("authorEmail", ""),
            "displayName": _display_name(raw_author, profile),
            "team": profile.get("team", ""),
            "telegramUsername": profile.get("telegramUsername", ""),
            "discordUserId": profile.get("discordUserId", ""),
            "discordUsername": profile.get("discordUsername", ""),
            "authorColor": profile.get("authorColor") or _author_color(raw_author),
            "source": None,
            "pluginVersion": None,
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
            "activityCounts": [],
            "activityMix": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
        }
        authors_by_raw[raw_author] = author_row
        return author_row

    def _profiles_by_raw_author(self) -> dict[str, dict[str, Any]]:
        return {
            item["rawAuthor"]: item
            for item in self.db.author_profiles.find({}, {"_id": 0})
            if item.get("rawAuthor")
        }
