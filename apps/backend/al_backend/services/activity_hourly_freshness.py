from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class ActivityHourlyFreshnessMixin:
    def activity_hourly_freshness(
        self,
        author: str,
        *,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        checked_at = now or dt.datetime.now(dt.UTC)
        raw_author = composed(self).resolve_author_alias(_normalize_author(author))
        author_keys = self._activity_hourly_freshness_author_keys(raw_author)
        profiles = composed(self)._profiles_by_raw_author()
        time_zone_id = _author_time_zone_id(raw_author, profiles)
        local_now = _to_local_datetime(checked_at, time_zone_id)
        local_date = local_now.date().isoformat()
        local_day_start = dt.datetime.combine(local_now.date(), dt.time.min, tzinfo=local_now.tzinfo).astimezone(dt.UTC)
        timeout_seconds = max(60, int(composed(self).get_interval_for_author(raw_author)) * 2)
        overdue_before = checked_at - dt.timedelta(seconds=timeout_seconds)

        pending_query = {
            "authorKey": {"$in": author_keys},
            "status": {"$in": ["queued", "processing"]},
        }
        pending_report_count = self.db.raw_reports.count_documents(pending_query)
        pending_report = self.db.raw_reports.find_one(
            pending_query,
            {"_id": 0, "queuedAt": 1, "receivedAt": 1, "processingStartedAt": 1},
            sort=[("queuedAt", ASCENDING), ("receivedAt", ASCENDING)],
        )
        waiting_docs = [
            self.db.report_refresh_requests.find_one(
                {"author": {"$in": author_keys}},
                {"_id": 0, "requestedAt": 1},
                sort=[("requestedAt", ASCENDING)],
            ),
            self.db.manual_report_expectations.find_one(
                {"author": {"$in": author_keys}},
                {"_id": 0, "requestedAt": 1},
                sort=[("requestedAt", ASCENDING)],
            ),
        ]
        waiting_times = [
            value
            for value in (
                _coerce_datetime((pending_report or {}).get("queuedAt"))
                or _coerce_datetime((pending_report or {}).get("receivedAt"))
                or _coerce_datetime((pending_report or {}).get("processingStartedAt")),
                *(_coerce_datetime((item or {}).get("requestedAt")) for item in waiting_docs),
            )
            if value is not None
        ]
        waiting = pending_report_count > 0 or any(waiting_docs)
        waiting_overdue = waiting and bool(waiting_times) and min(waiting_times) < overdue_before

        latest_failed = self.db.raw_reports.find_one(
            {
                "authorKey": {"$in": author_keys},
                "status": "failed",
                "receivedAt": {"$gte": local_day_start},
            },
            {"_id": 0, "failedAt": 1},
            sort=[("failedAt", DESCENDING), ("receivedAt", DESCENDING)],
        )
        latest_processed = self.db.raw_reports.find_one(
            {
                "authorKey": {"$in": author_keys},
                "status": "processed",
                "affectedDates": {"$in": [local_date]},
                "processedAt": {"$exists": True},
            },
            {"_id": 0, "processedAt": 1},
            sort=[("processedAt", DESCENDING)],
        )
        latest_failed_at = _coerce_datetime((latest_failed or {}).get("failedAt"))
        latest_processed_at = _coerce_datetime((latest_processed or {}).get("processedAt"))
        has_unrecovered_failure = bool(
            latest_failed_at
            and (latest_processed_at is None or latest_failed_at > latest_processed_at)
        )
        data_through = _latest_datetime(
            *(
                _coerce_datetime(item.get("lastRecordedAt"))
                for item in self.db.daily_author_activity.find(
                    {"author": {"$in": author_keys}, "date": local_date},
                    {"_id": 0, "lastRecordedAt": 1},
                )
            )
        )

        if waiting_overdue or (not waiting and has_unrecovered_failure):
            status = "delayed"
        elif waiting:
            status = "updating"
        else:
            status = "current"

        return {
            "status": status,
            "checkedAt": checked_at.isoformat(),
            "dataVersion": _isoformat_or_none((latest_processed or {}).get("processedAt")),
            "dataThrough": data_through.isoformat() if data_through else None,
            "pendingReportCount": pending_report_count,
        }

    def _activity_hourly_freshness_author_keys(self, raw_author: str) -> list[str]:
        author_keys = {raw_author}

        for alias in self.db.author_aliases.find({}, {"_id": 0, "sourceRawAuthor": 1}):
            source_author = str(alias.get("sourceRawAuthor") or "").strip()
            if source_author and composed(self).resolve_author_alias(source_author) == raw_author:
                author_keys.add(source_author)

        return sorted(author_keys)
