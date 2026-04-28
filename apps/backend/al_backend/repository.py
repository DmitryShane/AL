from __future__ import annotations

import datetime as dt
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .settings import Settings


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

    def save_report(self, source: str, plugin_version: str, encrypted_packet: str, payload: dict[str, Any]) -> str:
        now = dt.datetime.now(dt.UTC)
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
            }
        )
        snapshot = dict(payload)
        snapshot.update(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        self._apply_snapshot_to_aggregates(snapshot)
        return str(raw_result.inserted_id)

    def list_authors(self) -> list[str]:
        return sorted(author for author in self.db.activity_snapshots.distinct("author") if author)

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
        totals = {"activeSeconds": 0, "idleSeconds": 0, "overtimeActiveSeconds": 0}
        activity_counts: dict[str, int] = {}
        saved_prefabs: dict[str, dict[str, Any]] = {}
        hourly_by_author: dict[str, dict[str, Any]] = {}
        authors_by_raw: dict[str, dict[str, Any]] = {}
        profiles = self._profiles_by_raw_author()

        for item in self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}):
            raw_author = item.get("author") or "Unknown User"
            profile = profiles.get(raw_author, {})
            display_name = _display_name(raw_author, profile)
            day_seconds = int(item.get("daySeconds", 0))
            break_seconds = int(item.get("breakSeconds", 0))
            totals["daySeconds"] = int(totals.get("daySeconds", 0)) + day_seconds
            totals["activeSeconds"] += int(item.get("activeSeconds", 0))
            totals["idleSeconds"] += int(item.get("idleSeconds", 0))
            totals["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            totals["breakSeconds"] = int(totals.get("breakSeconds", 0)) + break_seconds

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
                    "displayName": display_name,
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "source": item.get("source"),
                    "pluginVersion": item.get("pluginVersion"),
                    "lastRecordedAt": item.get("lastRecordedAt"),
                    "lastReceivedAt": item.get("lastReceivedAt"),
                    "daySeconds": 0,
                    "activeSeconds": 0,
                    "idleSeconds": 0,
                    "breakSeconds": 0,
                    "overtimeActiveSeconds": 0,
                }
                authors_by_raw[raw_author] = author_row

            author_row["daySeconds"] += day_seconds
            author_row["activeSeconds"] += int(item.get("activeSeconds", 0))
            author_row["idleSeconds"] += int(item.get("idleSeconds", 0))
            author_row["breakSeconds"] += break_seconds
            author_row["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            author_row["pluginVersion"] = item.get("pluginVersion") or author_row.get("pluginVersion")
            author_row["source"] = item.get("source") or author_row.get("source")

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

            _merge_hourly_activity(current_author["hourlyActivity"], item.get("hourlyActivity", []))

        total_activities = sum(activity_counts.values())
        activity_mix = [
            {
                "type": activity_type,
                "count": count,
                "percent": round((count / total_activities) * 100) if total_activities else 0,
            }
            for activity_type, count in activity_counts.items()
        ]

        return {
            "totals": totals,
            "activityMix": sorted(activity_mix, key=lambda item: item["count"], reverse=True),
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "authors": sorted(
                (_with_productivity(author) for author in authors_by_raw.values()),
                key=lambda item: item["displayName"].lower(),
            ),
            "profiles": self.author_profiles(),
            "hourlyActivityByAuthor": sorted(hourly_by_author.values(), key=lambda item: item["author"]),
        }

    def author_profiles(self) -> list[dict[str, Any]]:
        known_authors = self.list_authors()
        profiles = self._profiles_by_raw_author()
        result = []

        for raw_author in known_authors:
            profile = profiles.get(raw_author, {})
            result.append(
                {
                    "rawAuthor": raw_author,
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                }
            )

        return result

    def upsert_author_profile(
        self,
        raw_author: str,
        display_name: str | None,
        team: str | None,
        telegram_username: str | None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        normalized_telegram = _normalize_telegram_username(telegram_username)
        update = {
            "rawAuthor": raw_author,
            "displayName": (display_name or raw_author).strip(),
            "team": (team or "").strip(),
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
    return [{"hour": hour, "activeSeconds": 0, "idleSeconds": 0} for hour in range(24)]


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
