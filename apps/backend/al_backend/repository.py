from __future__ import annotations

import datetime as dt
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .settings import Settings

LOW_PRODUCTIVITY_THRESHOLD = 50
LONG_BREAK_THRESHOLD_SECONDS = 3600
SELECT_HEAVY_THRESHOLD_PERCENT = 90
SELECT_HEAVY_MIN_EVENTS = 20
ANALYTICS_PERIOD_DAYS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "year": 365,
}
DEFAULT_ANALYTICS_SCORE_SETTINGS = {
    "activeTimeWeight": 0.35,
    "productivityWeight": 0.35,
    "breakPenaltyWeight": 0.15,
    "alertsPenaltyWeight": 0.10,
    "staleReportsPenaltyWeight": 0.05,
}
DEFAULT_CALENDAR_REASONS = [
    {"id": "vacation", "label": "Vacation"},
    {"id": "day_off", "label": "Day off"},
    {"id": "absence", "label": "Absence"},
]
AUTHOR_COLORS = ["#13a37b", "#5b4dff", "#f59e0b", "#dc2626", "#0ea5e9", "#a855f7", "#14b8a6", "#ef4444"]


class Repository:
    aggregates_version = 1

    def __init__(self, settings: Settings):
        self.client: MongoClient = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
        self.db: Database = self.client[settings.mongo_database]
        self.default_send_interval_seconds = settings.default_send_interval_seconds

    def ensure_indexes(self) -> None:
        self.db.raw_reports.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.activity_snapshots.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("date", ASCENDING)]
        )
        self.db.activity_snapshots.create_index(
            [("sessionId", ASCENDING), ("date", ASCENDING), ("recordedAt", DESCENDING)]
        )
        self.db.report_rows.create_index([("receivedAt", DESCENDING)])
        self.db.report_rows.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("sessionId", ASCENDING), ("date", ASCENDING)]
        )
        self.db.daily_author_activity.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("projectId", ASCENDING), ("date", ASCENDING)],
            unique=True,
        )
        self.db.author_profiles.create_index("rawAuthor", unique=True)
        self.db.author_profiles.create_index("telegramUsername", unique=True, sparse=True)
        self.db.break_events.create_index([("telegramUsername", ASCENDING), ("timestamp", DESCENDING)])
        self.db.break_sessions.create_index("telegramUsername", unique=True)
        self.db.interval_settings.create_index("kind", unique=True)
        self.db.interval_settings.create_index("author", unique=True, sparse=True)
        self.db.report_refresh_requests.create_index("author", unique=True)
        self.db.report_refresh_requests.create_index("requestedAt")
        self.db.manual_report_expectations.create_index("author", unique=True)
        self.db.analytics_settings.create_index("kind", unique=True)
        self.db.calendar_marks.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.calendar_marks.create_index("date")
        self.db.calendar_reasons.create_index("id", unique=True)
        self.rebuild_aggregates_if_needed()

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True

    def get_interval_for_author(self, author: str) -> int:
        author_setting = self.db.interval_settings.find_one({"kind": "author", "author": author})

        if author_setting and author_setting.get("sendIntervalSeconds"):
            return int(author_setting["sendIntervalSeconds"])

        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return self.default_send_interval_seconds

    def is_plugin_enabled_for_author(self, author: str) -> bool:
        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"pluginEnabled": 1})

        if profile and profile.get("pluginEnabled") is False:
            return False

        return True

    def upsert_interval_settings(
        self,
        default_send_interval_seconds: int | None,
        author: str | None,
        author_send_interval_seconds: int | None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        if default_send_interval_seconds is not None:
            self.db.interval_settings.update_one(
                {"kind": "global"},
                {"$set": {"sendIntervalSeconds": default_send_interval_seconds, "updatedAt": now}},
                upsert=True,
            )

        if author and author_send_interval_seconds is not None:
            self.db.interval_settings.update_one(
                {"kind": "author", "author": author},
                {
                    "$set": {
                        "author": author,
                        "sendIntervalSeconds": author_send_interval_seconds,
                        "updatedAt": now,
                    }
                },
                upsert=True,
            )

        return self.get_interval_settings()

    def get_interval_settings(self) -> dict[str, Any]:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
        author_settings = list(
            self.db.interval_settings.find({"kind": "author"}, {"_id": 0}).sort("author", ASCENDING)
        )

        return {
            "defaultSendIntervalSeconds": int(
                global_setting.get("sendIntervalSeconds", self.default_send_interval_seconds)
            ),
            "authors": author_settings,
        }

    def get_analytics_score_settings(self) -> dict[str, float]:
        settings = self.db.analytics_settings.find_one({"kind": "score"}, {"_id": 0}) or {}
        return _normalize_score_settings(settings)

    def upsert_analytics_score_settings(self, settings: dict[str, Any]) -> dict[str, float]:
        normalized = _normalize_score_settings(settings)
        self.db.analytics_settings.update_one(
            {"kind": "score"},
            {"$set": {**normalized, "kind": "score", "updatedAt": dt.datetime.now(dt.UTC)}},
            upsert=True,
        )
        return normalized

    def save_report(self, source: str, plugin_version: str, encrypted_packet: str, payload: dict[str, Any]) -> str:
        now = dt.datetime.now(dt.UTC)
        report_type = self.consume_expected_report_type(payload.get("author"))
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
                "reportType": report_type,
            }
        )
        snapshot = dict(payload)
        snapshot.update(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
                "reportType": report_type,
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        self._apply_snapshot_to_aggregates(snapshot)
        return str(raw_result.inserted_id)

    def list_authors(self) -> list[str]:
        return sorted(author for author in self.db.activity_snapshots.distinct("author") if author)

    def request_report_refresh(self, author: str | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        authors = [author] if author else self.list_authors()

        for item in authors:
            self.db.report_refresh_requests.update_one(
                {"author": item},
                {"$set": {"author": item, "requestedAt": now}},
                upsert=True,
            )

        return {
            "ok": True,
            "requestedAuthors": authors,
            "requestedCount": len(authors),
        }

    def should_submit_report_now(self, author: str) -> bool:
        request = self.db.report_refresh_requests.find_one_and_delete({"author": author})
        if request:
            self.db.manual_report_expectations.update_one(
                {"author": author},
                {"$set": {"author": author, "requestedAt": request.get("requestedAt", dt.datetime.now(dt.UTC))}},
                upsert=True,
            )
        return request is not None

    def consume_expected_report_type(self, author: str | None) -> str:
        if not author:
            return "auto"

        result = self.db.manual_report_expectations.find_one_and_delete({"author": author})

        if result:
            return "manual"

        return "auto"

    def latest_reports(self, limit: int = 100, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        reports = []
        projection = {
            "_id": 0,
            "rawReportId": 0,
            "encryptedPacket": 0,
            "activityCounts": 0,
            "savedPrefabs": 0,
            "hourlyActivity": 0,
            "activityCountDeltas": 0,
            "savedPrefabDeltas": 0,
            "hourlyActivityDelta": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "firstActivity": 0,
            "lastActivity": 0,
            "idleThresholdSeconds": 0,
            "workWindowSeconds": 0,
        }

        query = _date_query(start_date, end_date)
        profiles = self._profiles_by_raw_author()

        for item in self.db.report_rows.find(query, projection).sort("receivedAt", DESCENDING).limit(limit):
            profile = profiles.get(item.get("author") or "Unknown User", {})
            item["displayName"] = _display_name(item.get("author"), profile)
            item["team"] = profile.get("team", "")
            item["receivedAt"] = _iso(item.get("receivedAt"))
            reports.append(item)

        return reports

    def activity_summary(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        totals = {
            "daySeconds": 0,
            "telegramDaySeconds": 0,
            "pluginDaySeconds": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "overtimeActiveSeconds": 0,
            "breakSeconds": 0,
        }
        activity_counts: dict[str, int] = {}
        saved_prefabs: dict[str, dict[str, Any]] = {}
        hourly_by_author: dict[str, dict[str, Any]] = {}
        authors_by_raw: dict[str, dict[str, Any]] = {}
        profiles = self._profiles_by_raw_author()
        daily_items = list(self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}))
        break_buckets = self._break_buckets_for_daily_items(daily_items)

        for item in daily_items:
            raw_author = item.get("author") or "Unknown User"
            profile = profiles.get(raw_author, {})
            display_name = _display_name(raw_author, profile)
            hourly_activity = _apply_breaks_to_hourly_activity(
                item.get("hourlyActivity", []), break_buckets.get((raw_author, item.get("date") or ""), [])
            )
            report_active_seconds = int(item.get("activeSeconds", 0))
            report_idle_seconds = int(item.get("idleSeconds", 0))
            effective_break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly_activity)
            effective_active_seconds = report_active_seconds
            effective_idle_seconds = max(0, report_idle_seconds - effective_break_seconds)
            telegram_day_seconds = int(item.get("daySeconds", 0))
            plugin_day_seconds = report_active_seconds + report_idle_seconds
            totals["daySeconds"] += telegram_day_seconds
            totals["telegramDaySeconds"] += telegram_day_seconds
            totals["pluginDaySeconds"] += plugin_day_seconds
            totals["activeSeconds"] += effective_active_seconds
            totals["idleSeconds"] += effective_idle_seconds
            totals["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            totals["breakSeconds"] += effective_break_seconds

            for count in item.get("activityCounts", []):
                activity_type = count.get("type")

                if activity_type:
                    activity_counts[activity_type] = activity_counts.get(activity_type, 0) + int(count.get("count", 0))

            for prefab in item.get("savedPrefabs", []):
                path = prefab.get("path")

                if not path:
                    continue

                existing = saved_prefabs.get(path)

                if existing:
                    existing["saveCount"] += int(prefab.get("saveCount", 0))
                else:
                    saved_prefabs[path] = dict(prefab)

            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                author_row = {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or item.get("authorEmail", ""),
                    "displayName": display_name,
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "source": item.get("source"),
                    "pluginVersion": item.get("pluginVersion"),
                    "lastRecordedAt": item.get("lastRecordedAt"),
                    "lastReceivedAt": item.get("lastReceivedAt"),
                    "daySeconds": 0,
                    "telegramDaySeconds": 0,
                    "pluginDaySeconds": 0,
                    "activeSeconds": 0,
                    "idleSeconds": 0,
                    "breakSeconds": 0,
                    "overtimeActiveSeconds": 0,
                    "activityCounts": [],
                }
                authors_by_raw[raw_author] = author_row

            author_row["daySeconds"] += telegram_day_seconds
            author_row["telegramDaySeconds"] += telegram_day_seconds
            author_row["pluginDaySeconds"] += plugin_day_seconds
            author_row["activeSeconds"] += effective_active_seconds
            author_row["idleSeconds"] += effective_idle_seconds
            author_row["breakSeconds"] += effective_break_seconds
            author_row["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            author_row["authorEmail"] = profile.get("authorEmail") or item.get("authorEmail") or author_row.get("authorEmail", "")
            author_row["pluginVersion"] = item.get("pluginVersion") or author_row.get("pluginVersion")
            author_row["source"] = item.get("source") or author_row.get("source")
            author_row["activityCounts"] = _merge_count_list(
                author_row.get("activityCounts", []), item.get("activityCounts", []), "type", "count"
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

        total_activities = sum(activity_counts.values())
        activity_mix = [
            {
                "type": activity_type,
                "count": count,
                "percent": round((count / total_activities) * 100) if total_activities else 0,
            }
            for activity_type, count in activity_counts.items()
        ]

        now = dt.datetime.now(dt.UTC)
        author_rows = [
            _with_alerts(_with_productivity(author), self.get_interval_for_author(author["rawAuthor"]), now)
            for author in authors_by_raw.values()
        ]

        return {
            "totals": totals,
            "activityMix": sorted(activity_mix, key=lambda item: item["count"], reverse=True),
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "authors": sorted(author_rows, key=lambda item: item["displayName"].lower()),
            "profiles": self.author_profiles(),
            "hourlyActivityByAuthor": sorted(hourly_by_author.values(), key=lambda item: item["author"]),
        }

    def analytics_summary(self, period: str = "7d") -> dict[str, Any]:
        score_settings = self.get_analytics_score_settings()
        profiles = self._profiles_by_raw_author()
        period_ranges = _analytics_period_ranges()
        docs_by_period = {
            key: {
                "current": list(self.db.daily_author_activity.find(_date_query(value["startDate"], value["endDate"]), {"_id": 0})),
                "previous": list(
                    self.db.daily_author_activity.find(
                        _date_query(value["previousStartDate"], value["previousEndDate"]), {"_id": 0}
                    )
                ),
            }
            for key, value in period_ranges.items()
        }
        authors = set(self.list_authors())

        for period_docs in docs_by_period.values():
            for item in [*period_docs["current"], *period_docs["previous"]]:
                if item.get("author"):
                    authors.add(str(item.get("author")))

        author_summaries = []

        for raw_author in sorted(authors):
            profile = profiles.get(raw_author, {})
            period_stats = {}

            for key, period_docs in docs_by_period.items():
                current_docs = [item for item in period_docs["current"] if item.get("author") == raw_author]
                previous_docs = [item for item in period_docs["previous"] if item.get("author") == raw_author]
                period_stats[key] = _analytics_period_stat(current_docs, previous_docs, score_settings)

            year_delta = period_stats["year"]["deltas"]["score"]
            month_delta = period_stats["month"]["deltas"]["score"]
            author_summaries.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "periodStats": period_stats,
                    "status": "regressing" if month_delta < 0 else "improving",
                    "score": period_stats["month"]["score"],
                    "scoreDelta": month_delta,
                    "yearScoreDelta": year_delta,
                }
            )

        return {
            "scoreSettings": score_settings,
            "periods": period_ranges,
            "authors": sorted(author_summaries, key=lambda item: item["displayName"].lower()),
        }

    def calendar_summary(self, year: int) -> dict[str, Any]:
        self._ensure_calendar_reasons()
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        reasons = self.calendar_reasons()
        reasons_by_id = {item["id"]: item for item in reasons}
        authors = self.author_profiles()
        authors_by_raw = {item["rawAuthor"]: item for item in authors}
        marks = []
        stats_by_author: dict[str, dict[str, Any]] = {
            author["rawAuthor"]: {
                "rawAuthor": author["rawAuthor"],
                "displayName": author["displayName"],
                "authorColor": author["authorColor"],
                "totalMarkedDays": 0,
                "byReason": {reason["id"]: 0 for reason in reasons},
                "latestMarks": [],
            }
            for author in authors
        }

        query = {"date": {"$gte": start_date, "$lte": end_date}}

        for mark in self.db.calendar_marks.find(query, {"_id": 0}).sort("date", DESCENDING):
            author = authors_by_raw.get(mark.get("rawAuthor"), {})
            reason = reasons_by_id.get(mark.get("reasonId"), {"id": mark.get("reasonId"), "label": mark.get("reasonId")})
            item = {
                **mark,
                "displayName": author.get("displayName", mark.get("rawAuthor")),
                "authorColor": author.get("authorColor", _author_color(mark.get("rawAuthor"))),
                "reasonLabel": reason.get("label", mark.get("reasonId")),
            }
            marks.append(item)
            stats = stats_by_author.setdefault(
                mark.get("rawAuthor"),
                {
                    "rawAuthor": mark.get("rawAuthor"),
                    "displayName": author.get("displayName", mark.get("rawAuthor")),
                    "authorColor": author.get("authorColor", _author_color(mark.get("rawAuthor"))),
                    "totalMarkedDays": 0,
                    "byReason": {reason_item["id"]: 0 for reason_item in reasons},
                    "latestMarks": [],
                },
            )
            stats["totalMarkedDays"] += 1
            stats["byReason"][mark.get("reasonId")] = int(stats["byReason"].get(mark.get("reasonId"), 0)) + 1

            if len(stats["latestMarks"]) < 5:
                stats["latestMarks"].append(item)

        return {
            "year": year,
            "authors": authors,
            "reasons": reasons,
            "marks": sorted(marks, key=lambda item: (item["date"], item["rawAuthor"])),
            "stats": sorted(stats_by_author.values(), key=lambda item: item["displayName"].lower()),
        }

    def calendar_reasons(self) -> list[dict[str, Any]]:
        self._ensure_calendar_reasons()
        return list(self.db.calendar_reasons.find({}, {"_id": 0}).sort("label", ASCENDING))

    def upsert_calendar_reason(self, reason_id: str, label: str) -> dict[str, Any]:
        normalized_id = _slug(reason_id or label)
        normalized_label = (label or "").strip()

        if not normalized_id or not normalized_label:
            return {"ok": False, "error": "Reason id and label are required"}

        now = dt.datetime.now(dt.UTC)
        reason = {"id": normalized_id, "label": normalized_label, "updatedAt": now}
        self.db.calendar_reasons.update_one(
            {"id": normalized_id},
            {"$set": reason, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
        return {"ok": True, "reason": {"id": normalized_id, "label": normalized_label}}

    def delete_calendar_reason(self, reason_id: str) -> dict[str, Any]:
        if self.db.calendar_marks.find_one({"reasonId": reason_id}, {"_id": 1}):
            return {"ok": False, "error": "Reason is used by calendar marks"}

        self.db.calendar_reasons.delete_one({"id": reason_id})
        return {"ok": True}

    def upsert_calendar_marks(self, authors: list[str], dates: list[str], reason_id: str, note: str) -> dict[str, Any]:
        normalized_note = (note or "").strip()

        if not authors or not dates or not reason_id or not normalized_note:
            return {"ok": False, "error": "Authors, dates, reason, and note are required"}

        self._ensure_calendar_reasons()
        reason = self.db.calendar_reasons.find_one({"id": reason_id}, {"_id": 1})

        if not reason:
            return {"ok": False, "error": "Unknown reason"}

        now = dt.datetime.now(dt.UTC)
        saved_count = 0

        for raw_author in authors:
            for date in dates:
                _parse_date(date)
                self.db.calendar_marks.update_one(
                    {"rawAuthor": raw_author, "date": date},
                    {
                        "$set": {
                            "rawAuthor": raw_author,
                            "date": date,
                            "reasonId": reason_id,
                            "note": normalized_note,
                            "updatedAt": now,
                        },
                        "$setOnInsert": {"createdAt": now},
                    },
                    upsert=True,
                )
                saved_count += 1

        return {"ok": True, "savedCount": saved_count}

    def delete_calendar_mark(self, raw_author: str, date: str) -> dict[str, Any]:
        self.db.calendar_marks.delete_one({"rawAuthor": raw_author, "date": date})
        return {"ok": True}

    def _ensure_calendar_reasons(self) -> None:
        if self.db.calendar_reasons.count_documents({}):
            return

        now = dt.datetime.now(dt.UTC)

        for reason in DEFAULT_CALENDAR_REASONS:
            self.db.calendar_reasons.update_one(
                {"id": reason["id"]},
                {"$set": {**reason, "updatedAt": now}, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )

    def author_profiles(self) -> list[dict[str, Any]]:
        known_authors = self.list_authors()
        profiles = self._profiles_by_raw_author()
        result = []

        for raw_author in known_authors:
            profile = profiles.get(raw_author, {})
            author_activity = self.db.daily_author_activity.find_one(
                {"author": raw_author, "authorEmail": {"$nin": [None, ""]}},
                {"_id": 0, "authorEmail": 1},
                sort=[("lastReceivedAt", DESCENDING)],
            )
            result.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or (author_activity or {}).get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "pluginEnabled": profile.get("pluginEnabled", True),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                }
            )

        return result

    def update_author_email(self, raw_author: str, author_email: str | None) -> None:
        normalized_email = (author_email or "").strip()

        if not raw_author or not normalized_email:
            return

        if "@" not in normalized_email:
            return

        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$set": {
                    "rawAuthor": raw_author,
                    "authorEmail": normalized_email,
                    "updatedAt": dt.datetime.now(dt.UTC),
                },
                "$setOnInsert": {
                    "displayName": raw_author,
                    "team": "",
                    "pluginEnabled": True,
                },
            },
            upsert=True,
        )

    def upsert_author_profile(
        self,
        raw_author: str,
        display_name: str | None,
        team: str | None,
        telegram_username: str | None,
        plugin_enabled: bool = True,
        author_color: str | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        normalized_telegram = _normalize_telegram_username(telegram_username)
        update = {
            "rawAuthor": raw_author,
            "displayName": (display_name or raw_author).strip(),
            "team": (team or "").strip(),
            "pluginEnabled": plugin_enabled,
            "authorColor": _valid_color(author_color) or _author_color(raw_author),
            "updatedAt": now,
        }

        operation: dict[str, Any] = {"$set": update}

        if normalized_telegram:
            update["telegramUsername"] = normalized_telegram
        else:
            operation["$unset"] = {"telegramUsername": ""}

        self.db.author_profiles.update_one({"rawAuthor": raw_author}, operation, upsert=True)
        return {"ok": True, "profile": {k: v for k, v in update.items() if k != "updatedAt"}}

    def record_break_event(self, telegram_username: str, event_type: str, timestamp: str | None = None) -> dict[str, Any]:
        normalized_telegram = _normalize_telegram_username(telegram_username)
        event_time = _parse_timestamp(timestamp)
        profile = self.db.author_profiles.find_one({"telegramUsername": normalized_telegram})

        if not profile:
            return {"ok": False, "error": "Unknown telegram username"}

        raw_author = profile["rawAuthor"]
        self.db.break_events.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "timestamp": event_time,
            }
        )

        if event_type == "afk":
            self.db.break_sessions.update_one(
                {"telegramUsername": normalized_telegram},
                {"$set": {"telegramUsername": normalized_telegram, "rawAuthor": raw_author, "startedAt": event_time}},
                upsert=True,
            )
            return {"ok": True, "status": "break_started"}

        if event_type == "offline":
            day_date = event_time.date().isoformat()
            day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})

            if not day_state:
                return {"ok": True, "status": "offline_without_online"}

            started_at = day_state["startedAt"]
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"lastOfflineAt": event_time, "daySeconds": day_seconds}},
                upsert=True,
            )
            self.db.daily_author_activity.update_many(
                {"author": raw_author, "date": day_date},
                {"$set": {"dayStartedAt": started_at, "dayEndedAt": event_time, "daySeconds": day_seconds}},
            )
            return {"ok": True, "status": "day_closed", "daySeconds": day_seconds}

        online_date = event_time.date().isoformat()
        self.db.day_sessions.update_one(
            {"rawAuthor": raw_author, "date": online_date},
            {
                "$setOnInsert": {
                    "telegramUsername": normalized_telegram,
                    "rawAuthor": raw_author,
                    "date": online_date,
                    "startedAt": event_time,
                    "daySeconds": 0,
                },
                "$set": {"lastOnlineAt": event_time},
            },
            upsert=True,
        )

        session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

        if not session:
            return {"ok": True, "status": "online_recorded"}

        started_at = session["startedAt"]
        break_seconds = max(0, int((event_time - started_at).total_seconds()))
        break_date = started_at.date().isoformat()
        self.db.break_sessions.delete_one({"telegramUsername": normalized_telegram})
        self.db.break_intervals.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "startedAt": started_at,
                "endedAt": event_time,
                "date": break_date,
                "breakSeconds": break_seconds,
            }
        )
        self.db.daily_author_activity.update_many(
            {"author": raw_author, "date": break_date},
            {"$inc": {"breakSeconds": break_seconds}, "$set": {"updatedAt": dt.datetime.now(dt.UTC)}},
        )
        return {"ok": True, "status": "break_closed", "breakSeconds": break_seconds}

    def _break_buckets_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, int]]]:
        author_dates = {
            (item.get("author") or "Unknown User", item.get("date") or "")
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        min_start = _date_start(dates[0])
        max_end = _date_start(dates[-1]) + dt.timedelta(days=1)
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
            )

        now = dt.datetime.now(dt.UTC)

        for session in self.db.break_sessions.find({"rawAuthor": {"$in": authors}}, {"_id": 0}):
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            _add_break_interval_to_buckets(buckets, session.get("rawAuthor"), started_at, now)

        return buckets

    def _profiles_by_raw_author(self) -> dict[str, dict[str, Any]]:
        return {
            item["rawAuthor"]: item
            for item in self.db.author_profiles.find({}, {"_id": 0})
            if item.get("rawAuthor")
        }

    def rebuild_aggregates_if_needed(self) -> None:
        metadata = self.db.aggregate_metadata.find_one({"kind": "activity"})

        if metadata and metadata.get("version") == self.aggregates_version:
            return

        self.db.report_rows.delete_many({})
        self.db.daily_author_activity.delete_many({})
        self.db.aggregate_session_state.delete_many({})

        snapshots = self.db.activity_snapshots.find({}).sort("receivedAt", ASCENDING)

        for snapshot in snapshots:
            self._apply_snapshot_to_aggregates(snapshot)

        self.db.aggregate_metadata.update_one(
            {"kind": "activity"},
            {"$set": {"kind": "activity", "version": self.aggregates_version, "rebuiltAt": dt.datetime.now(dt.UTC)}},
            upsert=True,
        )

    def _apply_snapshot_to_aggregates(self, snapshot: dict[str, Any]) -> None:
        session_key = _session_key(snapshot)
        previous = self.db.aggregate_session_state.find_one({"_id": session_key}) or {}
        deltas = _build_deltas(snapshot, previous.get("snapshot", {}))
        row = dict(snapshot)
        row.update(deltas)
        row["snapshotKey"] = session_key
        self.db.report_rows.insert_one(row)
        self._update_daily_author_activity(snapshot, deltas)
        self.db.aggregate_session_state.update_one(
            {"_id": session_key},
            {"$set": {"snapshot": _state_snapshot(snapshot), "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC))}},
            upsert=True,
        )

    def _update_daily_author_activity(self, snapshot: dict[str, Any], deltas: dict[str, Any]) -> None:
        key = {
            "source": snapshot.get("source"),
            "author": snapshot.get("author") or "Unknown User",
            "projectId": snapshot.get("projectId") or "",
            "date": snapshot.get("date") or "",
        }
        current = self.db.daily_author_activity.find_one(key, {"_id": 0}) or {}
        hourly_activity = current.get("hourlyActivity") or _empty_hourly_activity()
        _merge_hourly_activity(hourly_activity, deltas.get("hourlyActivityDelta", []))
        activity_counts = _merge_count_list(current.get("activityCounts", []), deltas.get("activityCountDeltas", []), "type", "count")
        saved_prefabs = _merge_count_list(current.get("savedPrefabs", []), deltas.get("savedPrefabDeltas", []), "path", "saveCount")

        self.db.daily_author_activity.update_one(
            key,
            {
                "$set": {
                    **key,
                    "authorEmail": snapshot.get("authorEmail", ""),
                    "pluginVersion": snapshot.get("pluginVersion"),
                    "timeZoneId": snapshot.get("timeZoneId"),
                    "timeZoneDisplayName": snapshot.get("timeZoneDisplayName"),
                    "lastRecordedAt": snapshot.get("recordedAt"),
                    "lastReceivedAt": snapshot.get("receivedAt"),
                    "activityCounts": activity_counts,
                    "savedPrefabs": saved_prefabs,
                    "hourlyActivity": hourly_activity,
                },
                "$inc": {
                    "activeSeconds": deltas["activeDeltaSeconds"],
                    "idleSeconds": deltas["idleDeltaSeconds"],
                    "overtimeActiveSeconds": deltas["overtimeActiveDeltaSeconds"],
                },
            },
            upsert=True,
        )


def _iso(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()

    return value


def _date_query(start_date: str | None, end_date: str | None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    date_filter: dict[str, str] = {}

    if start_date:
        date_filter["$gte"] = start_date

    if end_date:
        date_filter["$lte"] = end_date

    if date_filter:
        query["date"] = date_filter

    return query


def _display_name(raw_author: Any, profile: dict[str, Any]) -> str:
    return str(profile.get("displayName") or raw_author or "Unknown User")


def _with_productivity(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    active_seconds = int(item.get("activeSeconds", 0))
    idle_seconds = int(item.get("idleSeconds", 0))
    break_seconds = int(item.get("breakSeconds", 0))
    penalized_break_seconds = max(0, break_seconds - 3600)
    denominator = active_seconds + idle_seconds + penalized_break_seconds
    item["productivity"] = round((active_seconds / denominator) * 100, 2) if denominator else 0
    return item


def _with_alerts(author: dict[str, Any], send_interval_seconds: int, now: dt.datetime) -> dict[str, Any]:
    item = dict(author)
    alerts = []
    last_received_at = _coerce_datetime(item.get("lastReceivedAt"))
    stale_threshold_seconds = max(0, send_interval_seconds * 2)

    if last_received_at:
        seconds_since_report = max(0, int((now - last_received_at).total_seconds()))

        if seconds_since_report > stale_threshold_seconds:
            alerts.append(
                {
                    "type": "reports_stopped",
                    "severity": "critical",
                    "title": "Reports stopped",
                    "message": "No Unity reports received within the expected auto-report window.",
                    "value": seconds_since_report,
                    "threshold": stale_threshold_seconds,
                }
            )
    else:
        alerts.append(
            {
                "type": "reports_stopped",
                "severity": "critical",
                "title": "Reports stopped",
                "message": "No Unity reports have been received for this author.",
                "value": None,
                "threshold": stale_threshold_seconds,
            }
        )

    productivity = float(item.get("productivity", 0))
    plugin_day_seconds = int(item.get("pluginDaySeconds", 0))

    if plugin_day_seconds > 0 and productivity < LOW_PRODUCTIVITY_THRESHOLD:
        alerts.append(
            {
                "type": "low_productivity",
                "severity": "warning",
                "title": "Low productivity",
                "message": "Productivity is below the expected threshold.",
                "value": productivity,
                "threshold": LOW_PRODUCTIVITY_THRESHOLD,
            }
        )

    break_seconds = int(item.get("breakSeconds", 0))

    if break_seconds > LONG_BREAK_THRESHOLD_SECONDS:
        alerts.append(
            {
                "type": "long_break",
                "severity": "warning",
                "title": "Long break",
                "message": "Break time is longer than expected.",
                "value": break_seconds,
                "threshold": LONG_BREAK_THRESHOLD_SECONDS,
            }
        )

    activity_counts = item.get("activityCounts", [])
    total_activity_events = sum(int(count.get("count", 0)) for count in activity_counts)
    select_events = sum(int(count.get("count", 0)) for count in activity_counts if count.get("type") == "select")
    select_percent = round((select_events / total_activity_events) * 100) if total_activity_events else 0

    if total_activity_events >= SELECT_HEAVY_MIN_EVENTS and select_percent >= SELECT_HEAVY_THRESHOLD_PERCENT:
        alerts.append(
            {
                "type": "select_heavy_activity",
                "severity": "warning",
                "title": "Select-heavy activity",
                "message": "Most activity events are selection changes, which can look like simulated activity.",
                "value": select_percent,
                "threshold": SELECT_HEAVY_THRESHOLD_PERCENT,
            }
        )

    critical_count = sum(1 for alert in alerts if alert["severity"] == "critical")
    warning_count = sum(1 for alert in alerts if alert["severity"] == "warning")
    item["status"] = "stale" if any(alert["type"] == "reports_stopped" for alert in alerts) else "online"
    item["alerts"] = alerts
    item["alertStats"] = {
        "total": len(alerts),
        "critical": critical_count,
        "warning": warning_count,
    }
    item["sendIntervalSeconds"] = send_interval_seconds
    item["staleThresholdSeconds"] = stale_threshold_seconds
    return item


def _normalize_score_settings(settings: dict[str, Any]) -> dict[str, float]:
    normalized = {}

    for key, default_value in DEFAULT_ANALYTICS_SCORE_SETTINGS.items():
        try:
            value = float(settings.get(key, default_value))
        except (TypeError, ValueError):
            value = default_value

        normalized[key] = max(0, value)

    return normalized


def _author_color(raw_author: Any) -> str:
    value = str(raw_author or "")
    index = sum(ord(char) for char in value) % len(AUTHOR_COLORS)
    return AUTHOR_COLORS[index]


def _valid_color(value: str | None) -> str | None:
    normalized = (value or "").strip()

    if len(normalized) == 7 and normalized.startswith("#"):
        try:
            int(normalized[1:], 16)
        except ValueError:
            return None

        return normalized

    return None


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value.strip()).strip("_")


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _analytics_period(period: str) -> dict[str, str | int]:
    normalized_period = period if period in ANALYTICS_PERIOD_DAYS else "7d"
    days = ANALYTICS_PERIOD_DAYS[normalized_period]
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days - 1)
    previous_end_date = start_date - dt.timedelta(days=1)
    previous_start_date = previous_end_date - dt.timedelta(days=days - 1)
    return {
        "preset": normalized_period,
        "days": days,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "previousStartDate": previous_start_date.isoformat(),
        "previousEndDate": previous_end_date.isoformat(),
    }


def _analytics_period_ranges() -> dict[str, dict[str, str]]:
    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    previous_month_end = month_start - dt.timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_year_start = year_start.replace(year=year_start.year - 1)
    previous_year_end = year_start - dt.timedelta(days=1)

    return {
        "day": {
            "label": "Today",
            "startDate": today.isoformat(),
            "endDate": today.isoformat(),
            "previousStartDate": (today - dt.timedelta(days=1)).isoformat(),
            "previousEndDate": (today - dt.timedelta(days=1)).isoformat(),
        },
        "week": {
            "label": "Week",
            "startDate": week_start.isoformat(),
            "endDate": today.isoformat(),
            "previousStartDate": (week_start - dt.timedelta(days=7)).isoformat(),
            "previousEndDate": (week_start - dt.timedelta(days=1)).isoformat(),
        },
        "month": {
            "label": "Month",
            "startDate": month_start.isoformat(),
            "endDate": today.isoformat(),
            "previousStartDate": previous_month_start.isoformat(),
            "previousEndDate": previous_month_end.isoformat(),
        },
        "year": {
            "label": "Year",
            "startDate": year_start.isoformat(),
            "endDate": today.isoformat(),
            "previousStartDate": previous_year_start.isoformat(),
            "previousEndDate": previous_year_end.isoformat(),
        },
    }


def _analytics_period_stat(
    current_docs: list[dict[str, Any]], previous_docs: list[dict[str, Any]], score_settings: dict[str, float]
) -> dict[str, Any]:
    current = _analytics_totals(current_docs, score_settings)
    previous = _analytics_totals(previous_docs, score_settings)
    deltas = {
        "score": round(current["score"] - previous["score"], 2),
        "productivity": round(current["productivity"] - previous["productivity"], 2),
        "activeSeconds": current["activeSeconds"] - previous["activeSeconds"],
        "idleSeconds": current["idleSeconds"] - previous["idleSeconds"],
        "breakSeconds": current["breakSeconds"] - previous["breakSeconds"],
        "pluginDaySeconds": current["pluginDaySeconds"] - previous["pluginDaySeconds"],
        "telegramDaySeconds": current["telegramDaySeconds"] - previous["telegramDaySeconds"],
    }
    return {
        **current,
        "previous": previous,
        "deltas": deltas,
        "insights": _analytics_insights(deltas),
    }


def _analytics_insights(deltas: dict[str, Any]) -> list[dict[str, Any]]:
    insights = []

    if abs(float(deltas["productivity"])) >= 1:
        insights.append(
            {
                "type": "productivity",
                "direction": "up" if deltas["productivity"] > 0 else "down",
                "message": "Productivity grew" if deltas["productivity"] > 0 else "Productivity dropped",
                "value": deltas["productivity"],
                "unit": "percent",
            }
        )

    if abs(int(deltas["breakSeconds"])) >= 300:
        insights.append(
            {
                "type": "break",
                "direction": "down" if deltas["breakSeconds"] > 0 else "up",
                "message": "Breaks got longer" if deltas["breakSeconds"] > 0 else "Breaks got shorter",
                "value": deltas["breakSeconds"],
                "unit": "seconds",
            }
        )

    if abs(int(deltas["pluginDaySeconds"])) >= 300:
        insights.append(
            {
                "type": "workday",
                "direction": "up" if deltas["pluginDaySeconds"] > 0 else "down",
                "message": "Work day got longer" if deltas["pluginDaySeconds"] > 0 else "Work day got shorter",
                "value": deltas["pluginDaySeconds"],
                "unit": "seconds",
            }
        )

    if abs(int(deltas["activeSeconds"])) >= 300:
        insights.append(
            {
                "type": "activity",
                "direction": "up" if deltas["activeSeconds"] > 0 else "down",
                "message": "Activity increased" if deltas["activeSeconds"] > 0 else "Activity dropped",
                "value": deltas["activeSeconds"],
                "unit": "seconds",
            }
        )

    if not insights:
        insights.append(
            {
                "type": "stable",
                "direction": "neutral",
                "message": "No major changes",
                "value": 0,
                "unit": "none",
            }
        )

    return insights


def _date_series(start_date: str, end_date: str) -> list[str]:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    days = (end - start).days + 1
    return [(start + dt.timedelta(days=offset)).isoformat() for offset in range(days)]


def _docs_by_author(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_author: dict[str, list[dict[str, Any]]] = {}

    for item in docs:
        author = str(item.get("author") or "Unknown User")
        by_author.setdefault(author, []).append(item)

    return by_author


def _docs_by_date(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = {}

    for item in docs:
        date = str(item.get("date") or "")

        if date:
            by_date.setdefault(date, []).append(item)

    return by_date


def _analytics_point(date: str, docs: list[dict[str, Any]], score_settings: dict[str, float]) -> dict[str, Any]:
    totals = _analytics_totals(docs, score_settings)
    return {"date": date, **totals}


def _analytics_totals(docs: list[dict[str, Any]], score_settings: dict[str, float]) -> dict[str, Any]:
    active_seconds = sum(int(item.get("activeSeconds", 0)) for item in docs)
    idle_seconds = sum(int(item.get("idleSeconds", 0)) for item in docs)
    break_seconds = sum(int(item.get("breakSeconds", 0)) for item in docs)
    telegram_day_seconds = sum(int(item.get("daySeconds", 0)) for item in docs)
    plugin_day_seconds = active_seconds + idle_seconds
    productivity = _productivity(active_seconds, idle_seconds, break_seconds)
    alert_count = sum(_analytics_alert_count(item) for item in docs)
    stale_count = sum(1 for item in docs if not item.get("lastReceivedAt"))
    score = _analytics_score(
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
        break_seconds=break_seconds,
        productivity=productivity,
        alert_count=alert_count,
        stale_count=stale_count,
        settings=score_settings,
    )
    return {
        "score": round(score, 2),
        "activeSeconds": active_seconds,
        "idleSeconds": idle_seconds,
        "breakSeconds": break_seconds,
        "telegramDaySeconds": telegram_day_seconds,
        "pluginDaySeconds": plugin_day_seconds,
        "productivity": round(productivity, 2),
        "alerts": alert_count,
        "staleReports": stale_count,
    }


def _productivity(active_seconds: int, idle_seconds: int, break_seconds: int) -> float:
    penalized_break_seconds = max(0, break_seconds - LONG_BREAK_THRESHOLD_SECONDS)
    denominator = active_seconds + idle_seconds + penalized_break_seconds
    return (active_seconds / denominator) * 100 if denominator else 0


def _analytics_alert_count(item: dict[str, Any]) -> int:
    alerts = 0
    productivity = _productivity(
        int(item.get("activeSeconds", 0)),
        int(item.get("idleSeconds", 0)),
        int(item.get("breakSeconds", 0)),
    )
    plugin_day_seconds = int(item.get("activeSeconds", 0)) + int(item.get("idleSeconds", 0))

    if plugin_day_seconds > 0 and productivity < LOW_PRODUCTIVITY_THRESHOLD:
        alerts += 1

    if int(item.get("breakSeconds", 0)) > LONG_BREAK_THRESHOLD_SECONDS:
        alerts += 1

    activity_counts = item.get("activityCounts", [])
    total_activity_events = sum(int(count.get("count", 0)) for count in activity_counts)
    select_events = sum(int(count.get("count", 0)) for count in activity_counts if count.get("type") == "select")
    select_percent = round((select_events / total_activity_events) * 100) if total_activity_events else 0

    if total_activity_events >= SELECT_HEAVY_MIN_EVENTS and select_percent >= SELECT_HEAVY_THRESHOLD_PERCENT:
        alerts += 1

    return alerts


def _analytics_score(
    active_seconds: int,
    idle_seconds: int,
    break_seconds: int,
    productivity: float,
    alert_count: int,
    stale_count: int,
    settings: dict[str, float],
) -> float:
    weight_total = sum(settings.values()) or 1
    active_score = min(active_seconds / 21600, 1) * 100
    productivity_score = max(0, min(productivity, 100))
    break_penalty = min(break_seconds / 7200, 1) * 100
    alerts_penalty = min(alert_count / 3, 1) * 100
    stale_penalty = 100 if stale_count else 0
    score = (
        active_score * settings["activeTimeWeight"]
        + productivity_score * settings["productivityWeight"]
        - break_penalty * settings["breakPenaltyWeight"]
        - alerts_penalty * settings["alertsPenaltyWeight"]
        - stale_penalty * settings["staleReportsPenaltyWeight"]
    ) / weight_total
    return max(0, min(score, 100))


def _average(values: list[float | int]) -> float:
    return sum(values) / len(values) if values else 0


def _analytics_leaderboards(authors: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "topPerformers": sorted(authors, key=lambda item: item["score"], reverse=True)[:5],
        "bottomPerformers": sorted(authors, key=lambda item: item["score"])[:5],
        "biggestGrowth": sorted(
            authors, key=lambda item: item["comparisons"]["previousPeriod"]["scoreDelta"], reverse=True
        )[:5],
        "biggestRegression": sorted(authors, key=lambda item: item["comparisons"]["previousPeriod"]["scoreDelta"])[:5],
        "activeTime": sorted(authors, key=lambda item: item["activeSeconds"], reverse=True)[:5],
    }


def _coerce_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)

        return value.astimezone(dt.UTC)

    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.UTC)

        return parsed.astimezone(dt.UTC)

    return None


def _normalize_telegram_username(value: str | None) -> str:
    return (value or "").strip().lstrip("@").lower()


def _parse_timestamp(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.UTC)

    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)

    return parsed.astimezone(dt.UTC)


def _session_key(snapshot: dict[str, Any]) -> str:
    return "|".join(
        [
            str(snapshot.get("source") or ""),
            str(snapshot.get("author") or "Unknown User"),
            str(snapshot.get("projectId") or ""),
            str(snapshot.get("sessionId") or ""),
            str(snapshot.get("date") or ""),
        ]
    )


def _state_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "activeSeconds": int(snapshot.get("activeSeconds", 0)),
        "idleSeconds": int(snapshot.get("idleSeconds", 0)),
        "overtimeActiveSeconds": int(snapshot.get("overtimeActiveSeconds", 0)),
        "activityCounts": snapshot.get("activityCounts", []),
        "savedPrefabs": snapshot.get("savedPrefabs", []),
        "hourlyActivity": snapshot.get("hourlyActivity", []),
    }


def _build_deltas(snapshot: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "activeDeltaSeconds": _delta(snapshot.get("activeSeconds"), previous.get("activeSeconds")),
        "idleDeltaSeconds": _delta(snapshot.get("idleSeconds"), previous.get("idleSeconds")),
        "overtimeActiveDeltaSeconds": _delta(
            snapshot.get("overtimeActiveSeconds"), previous.get("overtimeActiveSeconds")
        ),
        "activityCountDeltas": _count_deltas(snapshot.get("activityCounts", []), previous.get("activityCounts", []), "type", "count"),
        "savedPrefabDeltas": _count_deltas(snapshot.get("savedPrefabs", []), previous.get("savedPrefabs", []), "path", "saveCount"),
        "hourlyActivityDelta": _hourly_deltas(snapshot.get("hourlyActivity", []), previous.get("hourlyActivity", [])),
    }


def _delta(current: Any, previous: Any) -> int:
    return max(0, int(current or 0) - int(previous or 0))


def _count_deltas(current: list[dict[str, Any]], previous: list[dict[str, Any]], key_name: str, count_name: str) -> list[dict[str, Any]]:
    previous_by_key = {item.get(key_name): int(item.get(count_name, 0)) for item in previous if item.get(key_name)}
    deltas = []

    for item in current:
        key = item.get(key_name)

        if not key:
            continue

        count_delta = max(0, int(item.get(count_name, 0)) - previous_by_key.get(key, 0))

        if count_delta:
            delta_item = dict(item)
            delta_item[count_name] = count_delta
            deltas.append(delta_item)

    return deltas


def _empty_hourly_activity() -> list[dict[str, int]]:
    return [{"hour": hour, "activeSeconds": 0, "idleSeconds": 0, "breakSeconds": 0} for hour in range(24)]


def _hourly_deltas(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, int]]:
    previous_by_hour = {int(item.get("hour", 0)): item for item in previous}
    deltas = []

    for item in current:
        hour = int(item.get("hour", 0))
        previous_item = previous_by_hour.get(hour, {})
        active_delta = _delta(item.get("activeSeconds"), previous_item.get("activeSeconds"))
        idle_delta = _delta(item.get("idleSeconds"), previous_item.get("idleSeconds"))

        deltas.append({"hour": hour, "activeSeconds": active_delta, "idleSeconds": idle_delta})

    return deltas


def _merge_hourly_activity(target: list[dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    target_by_hour = {int(item.get("hour", 0)): item for item in target}

    for delta_item in deltas:
        hour = int(delta_item.get("hour", 0))
        target_item = target_by_hour.get(hour)

        if not target_item:
            continue

        target_item["activeSeconds"] = int(target_item.get("activeSeconds", 0)) + int(delta_item.get("activeSeconds", 0))
        target_item["idleSeconds"] = int(target_item.get("idleSeconds", 0)) + int(delta_item.get("idleSeconds", 0))
        target_item["breakSeconds"] = int(target_item.get("breakSeconds", 0)) + int(delta_item.get("breakSeconds", 0))


def _apply_breaks_to_hourly_activity(
    source: list[dict[str, Any]], break_buckets: list[dict[str, Any]]
) -> list[dict[str, int]]:
    source_by_hour = {int(item.get("hour", 0)): item for item in source}
    breaks_by_hour = {int(item.get("hour", 0)): item for item in break_buckets}
    hourly_activity = []

    for hour in range(24):
        source_hour = source_by_hour.get(hour, {})
        break_hour = breaks_by_hour.get(hour, {})
        active_seconds = min(3600, max(0, int(source_hour.get("activeSeconds", 0))))
        raw_idle_seconds = max(0, int(source_hour.get("idleSeconds", 0)))
        requested_break_seconds = max(0, int(break_hour.get("breakSeconds", 0)))
        break_seconds = min(requested_break_seconds, max(0, 3600 - active_seconds))
        idle_seconds = max(0, raw_idle_seconds - break_seconds)
        idle_seconds = min(idle_seconds, max(0, 3600 - active_seconds - break_seconds))
        hourly_activity.append(
            {
                "hour": hour,
                "activeSeconds": active_seconds,
                "idleSeconds": idle_seconds,
                "breakSeconds": break_seconds,
            }
        )

    return hourly_activity


def _add_break_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, int]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
) -> None:
    if not raw_author or not started_at or not ended_at or ended_at <= started_at:
        return

    current = started_at

    while current < ended_at:
        hour_end = current.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, ended_at)
        date = current.date().isoformat()
        key = (str(raw_author), date)
        target = buckets.get(key)

        if target:
            seconds = max(0, int((segment_end - current).total_seconds()))
            target[current.hour]["breakSeconds"] = int(target[current.hour].get("breakSeconds", 0)) + seconds

        current = segment_end


def _date_start(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)


def _merge_count_list(
    current: list[dict[str, Any]], deltas: list[dict[str, Any]], key_name: str, count_name: str
) -> list[dict[str, Any]]:
    by_key = {item.get(key_name): dict(item) for item in current if item.get(key_name)}

    for delta_item in deltas:
        key = delta_item.get(key_name)

        if not key:
            continue

        existing = by_key.get(key)

        if existing:
            existing[count_name] = int(existing.get(count_name, 0)) + int(delta_item.get(count_name, 0))
        else:
            by_key[key] = dict(delta_item)

    return sorted(by_key.values(), key=lambda item: item.get(count_name, 0), reverse=True)
