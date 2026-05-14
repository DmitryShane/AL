from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class ActivitySummaryCacheMixin:
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
        snapshot_date, snapshot_doc = self.activity_day_summary_snapshot_for_request(
            view=view,
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
            include_profiles=include_profiles,
            include_hourly=include_hourly,
            include_breakdowns=include_breakdowns,
            now=now,
        )

        if snapshot_doc:
            payload = dict(snapshot_doc.get("payload") or {})
            payload["cache"] = {"hit": True, "key": cache_key}
            payload["snapshot"] = {"hit": True, "date": snapshot_date}
            return payload

        cached = self.db.activity_summary_cache.find_one(
            {"cacheKey": cache_key, "expiresAt": {"$gt": now}},
            {"_id": 0, "payload": 1},
        )

        if cached:
            payload = dict(cached.get("payload") or {})
            payload["cache"] = {"hit": True, "key": cache_key}
            return payload

        if snapshot_date:
            self.start_activity_snapshot_background_drain()
            payload = self.activity_day_summary_preparing_payload(snapshot_date, now=now)
            payload["cache"] = {"hit": False, "key": cache_key}
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
        self.invalidate_activity_day_summary_snapshots(dates)

        if dates is not None:
            date_values = {str(day) for day in dates if str(day or "").strip()}
            if not date_values:
                return
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
