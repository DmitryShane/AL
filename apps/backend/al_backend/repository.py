from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from .settings import Settings
from .auth import hash_password, new_session_token, session_token_hash, verify_password

LOW_PRODUCTIVITY_THRESHOLD = 50
LONG_BREAK_THRESHOLD_SECONDS = 3600
TELEGRAM_DAY_REMINDER_SECONDS = 10 * 3600
TELEGRAM_ONLINE_PROMPT_DELAY_SECONDS = 15 * 60
DEFAULT_PLUGIN_WORK_WINDOW_SECONDS = 32400
MICROSECONDS_PER_SECOND = 1_000_000
MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS = 10
SELECT_HEAVY_THRESHOLD_PERCENT = 90
SELECT_HEAVY_MIN_EVENTS = 20
AFK_IDLE_ARTIFACT_THRESHOLD_SECONDS = 300
REPORT_CHALLENGE_TTL_SECONDS = 120
RAW_ACTIVITY_EVENT_TYPES = {
    "selection",
    "select",
    "scene_saved",
    "asset_saved",
    "prefab_saved",
    "undo_redo",
    "play_mode",
    "focus",
    "scene_changed",
    "file_saved",
    "file_loaded",
    "external",
}
NON_ACTIVITY_EVENT_TYPES = {"heartbeat", "blur"}
DEFAULT_CALENDAR_REASONS = [
    {"id": "vacation", "label": "Vacation"},
    {"id": "day_off", "label": "Day off"},
    {"id": "absence", "label": "Absence"},
]
AUTHOR_COLORS = ["#13a37b", "#5b4dff", "#f59e0b", "#dc2626", "#0ea5e9", "#a855f7", "#14b8a6", "#ef4444"]
AUTHOR_TIME_ZONE_IDS = {
    "Denis Ostrovskiy": "Europe/Kyiv",
    "Евгений Доценко": "Europe/Sofia",
}
WINDOWS_TIME_ZONE_IDS = {
    "FLE Standard Time": "Europe/Sofia",
    "FLE Daylight Time": "Europe/Sofia",
}


class Repository:
    aggregates_version = 18

    def __init__(self, settings: Settings):
        self.client: MongoClient = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
        self.db: Database = self.client[settings.mongo_database]
        self.default_send_interval_seconds = settings.default_send_interval_seconds

    def ensure_indexes(self) -> None:
        self.db.raw_reports.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.raw_event_batches.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.raw_activity_events.create_index("eventId", unique=True)
        self.db.raw_activity_events.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("projectId", ASCENDING), ("sessionId", ASCENDING), ("occurredAtUtc", ASCENDING)]
        )
        self.db.report_challenges.create_index("challengeId", unique=True)
        self.db.report_challenges.create_index("expiresAt")
        self.db.report_security_events.create_index([("createdAt", DESCENDING)])
        self.db.report_security_events.create_index([("author", ASCENDING), ("createdAt", DESCENDING)])
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
        self.db.author_aliases.create_index("sourceRawAuthor", unique=True)
        self.db.author_aliases.create_index("targetRawAuthor")
        self.db.author_profiles.create_index("telegramUsername", unique=True, sparse=True)
        self.db.author_profiles.create_index("discordUserId", unique=True, sparse=True)
        self.db.break_events.create_index([("telegramUsername", ASCENDING), ("timestamp", DESCENDING)])
        self.db.break_sessions.create_index("telegramUsername", unique=True)
        self.db.meeting_events.create_index([("discordUserId", ASCENDING), ("timestamp", DESCENDING)])
        self.db.meeting_sessions.create_index("discordUserId", unique=True)
        self.db.meeting_intervals.create_index([("rawAuthor", ASCENDING), ("startedAt", ASCENDING), ("endedAt", ASCENDING)])
        self.db.telegram_day_reminders.create_index("reminderId", unique=True)
        self.db.telegram_day_reminders.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.telegram_online_prompts.create_index("reminderId", unique=True)
        self.db.telegram_online_prompts.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.interval_settings.create_index("kind", unique=True)
        self.db.interval_settings.create_index("author", unique=True, sparse=True)
        self.db.report_refresh_requests.create_index("author", unique=True)
        self.db.report_refresh_requests.create_index("requestedAt")
        self.db.manual_report_expectations.create_index("author", unique=True)
        self.db.calendar_marks.create_index([("rawAuthor", ASCENDING), ("date", ASCENDING)], unique=True)
        self.db.calendar_marks.create_index("date")
        self.db.calendar_reasons.create_index("id", unique=True)
        self.db.site_users.create_index("email", unique=True)
        self.db.site_sessions.create_index("tokenHash", unique=True)
        self.db.site_sessions.create_index("expiresAt", expireAfterSeconds=0)
        self.rebuild_aggregates_if_needed()

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True

    def ensure_bootstrap_site_admin(self, email: str, password: str) -> None:
        normalized_email = _normalize_email(email)

        if not normalized_email or not password:
            return

        now = dt.datetime.now(dt.UTC)
        existing = self.db.site_users.find_one({"email": normalized_email}, {"_id": 1, "passwordHash": 1})
        update = {
            "email": normalized_email,
            "displayName": normalized_email,
            "role": "admin",
            "active": True,
            "updatedAt": now,
        }

        if existing:
            self.db.site_users.update_one({"email": normalized_email}, {"$set": update})
            return

        update["passwordHash"] = hash_password(password)
        update["createdAt"] = now
        self.db.site_users.update_one({"email": normalized_email}, {"$set": update}, upsert=True)

    def authenticate_site_user(self, email: str, password: str) -> dict[str, Any] | None:
        normalized_email = _normalize_email(email)

        if not normalized_email or not password:
            return None

        user = self.db.site_users.find_one({"email": normalized_email, "active": True})

        if not user or not verify_password(password, user.get("passwordHash", "")):
            return None

        return _public_site_user(user)

    def create_site_session(self, email: str) -> str:
        token = new_session_token()
        now = dt.datetime.now(dt.UTC)
        expires_at = now + dt.timedelta(days=7)
        self.db.site_sessions.insert_one(
            {
                "tokenHash": session_token_hash(token),
                "email": _normalize_email(email),
                "createdAt": now,
                "expiresAt": expires_at,
            }
        )
        return token

    def site_user_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None

        token_hash = session_token_hash(token)
        session = self.db.site_sessions.find_one({"tokenHash": token_hash, "expiresAt": {"$gt": dt.datetime.now(dt.UTC)}})

        if not session:
            return None

        user = self.db.site_users.find_one({"email": session.get("email"), "active": True})

        if not user:
            return None

        return _public_site_user(user)

    def delete_site_session(self, token: str | None) -> None:
        if token:
            self.db.site_sessions.delete_one({"tokenHash": session_token_hash(token)})

    def site_users(self) -> list[dict[str, Any]]:
        return [_public_site_user(user) for user in self.db.site_users.find({}, {"passwordHash": 0}).sort("email", ASCENDING)]

    def upsert_site_user(
        self,
        email: str,
        display_name: str | None,
        role: str,
        active: bool,
        password: str | None = None,
    ) -> dict[str, Any]:
        normalized_email = _normalize_email(email)

        if not normalized_email:
            return {"ok": False, "error": "Email is required"}

        if role not in {"admin", "editor", "viewer"}:
            return {"ok": False, "error": "Invalid role"}

        existing = self.db.site_users.find_one({"email": normalized_email})

        if not existing and not password:
            return {"ok": False, "error": "Password is required for new users"}

        now = dt.datetime.now(dt.UTC)
        update = {
            "email": normalized_email,
            "displayName": (display_name or normalized_email).strip(),
            "role": role,
            "active": active,
            "updatedAt": now,
        }

        if password:
            update["passwordHash"] = hash_password(password)

        operation: dict[str, Any] = {"$set": update}

        if not existing:
            operation["$setOnInsert"] = {"createdAt": now}

        self.db.site_users.update_one({"email": normalized_email}, operation, upsert=True)
        user = self.db.site_users.find_one({"email": normalized_email}) or update
        return {"ok": True, "user": _public_site_user(user)}

    def delete_site_user(self, email: str) -> dict[str, Any]:
        normalized_email = _normalize_email(email)
        result = self.db.site_users.delete_one({"email": normalized_email})
        self.db.site_sessions.delete_many({"email": normalized_email})
        return {"ok": True, "deleted": result.deleted_count}

    def get_interval_for_author(self, author: str) -> int:
        author = _normalize_author(author)
        author_setting = self.db.interval_settings.find_one({"kind": "author", "author": author})

        if author_setting and author_setting.get("sendIntervalSeconds"):
            return int(author_setting["sendIntervalSeconds"])

        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return self.default_send_interval_seconds

    def is_plugin_enabled_for_author(self, author: str) -> bool:
        author = self.resolve_author_alias(_normalize_author(author))
        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"pluginEnabled": 1})

        if profile and profile.get("pluginEnabled") is False:
            return False

        return True

    def resolve_author_alias(self, raw_author: str | None) -> str:
        normalized_author = _normalize_author(raw_author or "Unknown User")
        alias = self.db.author_aliases.find_one({"sourceRawAuthor": normalized_author}, {"_id": 0, "targetRawAuthor": 1})

        if alias and alias.get("targetRawAuthor"):
            return _normalize_author(alias.get("targetRawAuthor"))

        return normalized_author

    def author_aliases(self) -> list[dict[str, Any]]:
        return list(self.db.author_aliases.find({}, {"_id": 0}).sort("sourceRawAuthor", ASCENDING))

    def upsert_author_alias(self, source_raw_author: str, target_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)
        target = _normalize_author(target_raw_author)

        if not source or not target:
            return {"ok": False, "error": "Source and target authors are required"}

        if source == target:
            return {"ok": False, "error": "Source and target authors must be different"}

        if target not in self.list_authors():
            return {"ok": False, "error": "Target profile does not exist"}

        now = dt.datetime.now(dt.UTC)
        self.db.author_profiles.update_one(
            {"rawAuthor": target},
            {
                "$setOnInsert": {
                    "rawAuthor": target,
                    "displayName": target,
                    "team": "",
                    "pluginEnabled": True,
                    "authorColor": _author_color(target),
                    "createdAt": now,
                },
                "$set": {
                    "updatedAt": now,
                },
            },
            upsert=True,
        )
        self.db.author_aliases.update_one(
            {"sourceRawAuthor": source},
            {
                "$set": {
                    "sourceRawAuthor": source,
                    "targetRawAuthor": target,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "createdAt": now,
                },
            },
            upsert=True,
        )
        self.db.author_profiles.delete_many({"rawAuthor": source})
        self.rebuild_aggregates_if_needed(force=True)
        return {"ok": True, "alias": {"sourceRawAuthor": source, "targetRawAuthor": target}}

    def delete_author_alias(self, source_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)

        if not source:
            return {"ok": False, "error": "Source author is required"}

        result = self.db.author_aliases.delete_one({"sourceRawAuthor": source})
        self.rebuild_aggregates_if_needed(force=True)
        return {"ok": True, "deleted": getattr(result, "deleted_count", 0)}

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
            author = _normalize_author(author)
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

    def create_report_challenge(self, challenge_in: Any, keys: Any) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        expires_at = now + dt.timedelta(seconds=REPORT_CHALLENGE_TTL_SECONDS)
        challenge_id = _new_id()
        challenge = {
            "challengeId": challenge_id,
            "source": challenge_in.source,
            "pluginVersion": challenge_in.plugin_version,
            "author": _normalize_author(challenge_in.author),
            "authorEmail": challenge_in.author_email or "",
            "projectId": challenge_in.project_id or "",
            "sessionId": challenge_in.session_id or "",
            "deviceId": challenge_in.device_id or "",
            "privateKeyPem": keys.private_key_pem,
            "publicModulus": keys.public_modulus,
            "publicExponent": keys.public_exponent,
            "createdAt": now,
            "expiresAt": expires_at,
        }
        self.db.report_challenges.insert_one(challenge)
        return {**challenge, "expiresAt": expires_at.isoformat()}

    def claim_report_challenge(self, challenge_id: str, source: str, device_id: str | None) -> dict[str, Any] | None:
        now = dt.datetime.now(dt.UTC)
        query: dict[str, Any] = {
            "challengeId": challenge_id,
            "source": source,
            "expiresAt": {"$gt": now},
            "consumedAt": {"$exists": False},
        }

        if device_id:
            query["deviceId"] = {"$in": [device_id, ""]}

        return self.db.report_challenges.find_one_and_update(
            query,
            {"$set": {"consumedAt": now}},
            return_document=ReturnDocument.AFTER,
        )

    def log_report_security_event(
        self,
        event_type: str,
        source: str,
        plugin_version: str | None = None,
        author: str | None = None,
        author_email: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        device_id: str | None = None,
        challenge_id: str | None = None,
        message: str | None = None,
    ) -> None:
        self.db.report_security_events.insert_one(
            {
                "type": event_type,
                "severity": "critical",
                "source": source,
                "pluginVersion": plugin_version or "",
                "author": author or "Unknown User",
                "authorEmail": author_email or "",
                "projectId": project_id or "",
                "sessionId": session_id or "",
                "deviceId": device_id or "",
                "challengeId": challenge_id or "",
                "message": message or "Suspicious report submission detected.",
                "createdAt": dt.datetime.now(dt.UTC),
            }
        )

    def save_report(
        self,
        source: str,
        plugin_version: str,
        encrypted_packet: str,
        payload: dict[str, Any],
        challenge_id: str,
        device_id: str | None = None,
    ) -> str:
        now = dt.datetime.now(dt.UTC)
        payload = dict(payload)
        original_author = _normalize_author(payload.get("author") or "Unknown User")
        payload["author"] = self.resolve_author_alias(original_author)
        normalized_time_zone = _author_configured_time_zone_id(payload["author"]) or _valid_time_zone_id(payload.get("timeZoneId"))

        if normalized_time_zone:
            payload["timeZoneId"] = normalized_time_zone
            payload["timeZoneDisplayName"] = str(payload.get("timeZoneDisplayName") or "").strip() or normalized_time_zone

        self.update_author_time_zone(payload.get("author"), payload.get("timeZoneId"), payload.get("timeZoneDisplayName"))
        report_type = self.consume_expected_report_type(payload.get("author"))
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "challengeId": challenge_id,
                "deviceId": device_id or payload.get("deviceId", ""),
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
                "reportType": report_type,
            }
        )

        if isinstance(payload.get("events"), list):
            self._save_event_batch(
                source=source,
                plugin_version=plugin_version,
                payload=payload,
                raw_report_id=raw_result.inserted_id,
                report_type=report_type,
                received_at=now,
                challenge_id=challenge_id,
                device_id=device_id,
            )
            return str(raw_result.inserted_id)

        snapshot = dict(payload)
        snapshot.update(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
                "reportType": report_type,
                "challengeId": challenge_id,
                "deviceId": device_id or payload.get("deviceId", ""),
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        self._apply_snapshot_to_aggregates(snapshot)
        return str(raw_result.inserted_id)

    def list_authors(self) -> list[str]:
        alias_sources = {item.get("sourceRawAuthor") for item in self.author_aliases()}
        authors = set()

        for author in self.db.activity_snapshots.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.daily_author_activity.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.raw_activity_events.distinct("author"):
            if author:
                authors.add(self.resolve_author_alias(author))

        for author in self.db.author_profiles.distinct("rawAuthor"):
            if author and author not in alias_sources:
                authors.add(author)

        return sorted(authors)

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

    def latest_reports(
        self,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        reports = []
        projection = {
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

        profiles = self._profiles_by_raw_author()
        now = dt.datetime.now(dt.UTC)
        query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for item in self.db.report_rows.find(query, projection).sort("receivedAt", DESCENDING):
            if date_mode == "authorLocalToday" and not _is_author_local_today(
                item.get("date"),
                item.get("author") or "Unknown User",
                profiles,
                item.get("timeZoneId"),
                now,
            ):
                continue

            if self._is_empty_plugin_report_without_signal(item) or self._is_idle_report_during_meeting(item):
                continue

            profile = profiles.get(item.get("author") or "Unknown User", {})
            item["displayName"] = _display_name(item.get("author"), profile)
            item["team"] = profile.get("team", "")
            item["receivedAt"] = _iso(item.get("receivedAt"))
            reports.append(item)

            if limit is not None and len(reports) >= limit:
                break

        return reports

    def _is_idle_report_during_meeting(self, report_row: dict[str, Any]) -> bool:
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

    def _is_empty_plugin_report_without_signal(self, report_row: dict[str, Any]) -> bool:
        if report_row.get("source") in {"discord", "telegram"} or report_row.get("reportType") in {"meeting", "telegram"}:
            return False

        if _has_time_delta(report_row):
            return False

        return not (
            report_row.get("activityCountDeltas")
            or report_row.get("savedPrefabDeltas")
            or report_row.get("overtimeActivityCountDeltas")
            or report_row.get("overtimeSavedPrefabDeltas")
        )

    def activity_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        date_mode: str | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
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
        meeting_consumed_by_author_date_hour: dict[tuple[str, str], list[dict[str, int]]] = {}
        profiles = self._profiles_by_raw_author()
        now = now or dt.datetime.now(dt.UTC)
        daily_items = sorted(
            self.db.daily_author_activity.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0}),
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("lastRecordedAt") or item.get("lastReceivedAt") or ""),
                str(item.get("source") or ""),
            ),
        )
        if date_mode == "authorLocalToday":
            daily_items = [
                item
                for item in daily_items
                if _is_author_local_today(
                    item.get("date"),
                    item.get("author") or "Unknown User",
                    profiles,
                    item.get("timeZoneId"),
                    now,
                )
            ]
        break_buckets = self._break_buckets_for_daily_items(daily_items)
        meeting_buckets = self._meeting_buckets_for_daily_items(daily_items, now)
        telegram_gaps = self._telegram_gaps_for_daily_items(daily_items)
        telegram_gap_counted: set[tuple[str, str]] = set()

        for item in daily_items:
            raw_author = item.get("author") or "Unknown User"
            item_date = item.get("date") or ""
            author_date_key = (raw_author, item_date)
            profile = profiles.get(raw_author, {})
            display_name = _display_name(raw_author, profile)
            hourly_activity = _apply_breaks_to_hourly_activity(
                item.get("hourlyActivity", []),
                break_buckets.get(author_date_key, []),
            )
            hourly_activity = _apply_meetings_to_hourly_activity(
                hourly_activity,
                meeting_buckets.get(author_date_key, []),
                meeting_consumed_by_author_date_hour.setdefault(author_date_key, _empty_hourly_activity()),
            )
            telegram_gap = telegram_gaps.get(author_date_key, {})
            telegram_gap_hours = telegram_gap.get("hourlyActivity", [])
            can_apply_telegram_gap = item.get("source") not in {"telegram", "discord"}
            telegram_gap_seconds = (
                int(telegram_gap.get("seconds", 0))
                if can_apply_telegram_gap and author_date_key not in telegram_gap_counted
                else 0
            )

            if telegram_gap_seconds:
                _merge_hourly_activity(hourly_activity, telegram_gap_hours)
                telegram_gap_counted.add(author_date_key)

            report_active_seconds = int(item.get("activeSeconds", 0))
            report_idle_seconds = sum(int(hour.get("idleSeconds", 0)) for hour in hourly_activity)
            raw_plugin_day_seconds = max(0, int(item.get("activeSeconds", 0)) + int(item.get("idleSeconds", 0)) + telegram_gap_seconds)
            effective_break_seconds = sum(int(hour.get("breakSeconds", 0)) for hour in hourly_activity)
            effective_meeting_seconds = sum(int(hour.get("meetingSeconds", 0)) for hour in hourly_activity)
            telegram_day_seconds = int(item.get("daySeconds", 0))
            telegram_to_first_activity_seconds = telegram_gap_seconds
            work_window_seconds = int(item.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
            normal_consumed = normal_consumed_by_author_date.get(author_date_key, 0)
            normal_available = max(0, work_window_seconds - normal_consumed)
            plugin_day_seconds = min(max(0, report_active_seconds + report_idle_seconds), normal_available)
            effective_active_seconds = min(report_active_seconds, plugin_day_seconds)
            effective_idle_seconds = min(
                max(0, report_idle_seconds),
                max(0, plugin_day_seconds - effective_active_seconds),
            )
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
            totals["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            totals["breakSeconds"] += effective_break_seconds

            for count in item.get("activityCounts", []):
                activity_type = count.get("type")

                if activity_type:
                    activity_counts[activity_type] = activity_counts.get(activity_type, 0) + int(count.get("count", 0))

            for count in item.get("overtimeActivityCounts", []):
                activity_type = count.get("type")

                if activity_type:
                    overtime_activity_counts[activity_type] = overtime_activity_counts.get(activity_type, 0) + int(count.get("count", 0))

            for prefab in item.get("savedPrefabs", []):
                path = prefab.get("path")

                if not path:
                    continue

                existing = saved_prefabs.get(path)

                if existing:
                    existing["saveCount"] += int(prefab.get("saveCount", 0))
                else:
                    saved_prefabs[path] = dict(prefab)

            for prefab in item.get("overtimeSavedPrefabs", []):
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
                author_row = {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or item.get("authorEmail", ""),
                    "displayName": display_name,
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "discordUserId": profile.get("discordUserId", ""),
                    "discordUsername": profile.get("discordUsername", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "source": item.get("source"),
                    "pluginVersion": item.get("pluginVersion"),
                    "timeZoneId": profile.get("timeZoneId") or item.get("timeZoneId"),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName") or item.get("timeZoneDisplayName"),
                    "lastRecordedAt": item.get("lastRecordedAt"),
                    "lastReceivedAt": item.get("lastReceivedAt"),
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
            author_row["overtimeActiveSeconds"] += int(item.get("overtimeActiveSeconds", 0))
            author_row["authorEmail"] = profile.get("authorEmail") or item.get("authorEmail") or author_row.get("authorEmail", "")
            author_row["pluginVersion"] = item.get("pluginVersion") or author_row.get("pluginVersion")
            author_row["source"] = item.get("source") or author_row.get("source")
            author_row["timeZoneId"] = profile.get("timeZoneId") or item.get("timeZoneId") or author_row.get("timeZoneId")
            author_row["timeZoneDisplayName"] = (
                profile.get("timeZoneDisplayName") or item.get("timeZoneDisplayName") or author_row.get("timeZoneDisplayName")
            )
            author_row["activityCounts"] = _merge_count_list(
                author_row.get("activityCounts", []), item.get("activityCounts", []), "type", "count"
            )
            author_row["savedPrefabs"] = _merge_count_list(
                author_row.get("savedPrefabs", []), item.get("savedPrefabs", []), "path", "saveCount"
            )
            author_row["overtimeActivityCounts"] = _merge_count_list(
                author_row.get("overtimeActivityCounts", []), item.get("overtimeActivityCounts", []), "type", "count"
            )
            author_row["overtimeSavedPrefabs"] = _merge_count_list(
                author_row.get("overtimeSavedPrefabs", []), item.get("overtimeSavedPrefabs", []), "path", "saveCount"
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

        activity_mix = _activity_mix_from_counts(activity_counts)
        overtime_activity_mix = _activity_mix_from_counts(overtime_activity_counts)

        self._apply_live_telegram_summary(
            authors_by_raw,
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
        _merge_meeting_buckets_into_hourly_author_rows(hourly_by_author, meeting_buckets, profiles)
        security_alerts_by_author = self._security_alerts_by_author(start_date, end_date)

        for raw_author, alerts in security_alerts_by_author.items():
            author_row = authors_by_raw.get(raw_author)

            if not author_row:
                profile = profiles.get(raw_author, {})
                author_row = {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or alerts[0].get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "discordUserId": profile.get("discordUserId", ""),
                    "discordUsername": profile.get("discordUsername", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "source": alerts[0].get("source"),
                    "pluginVersion": alerts[0].get("pluginVersion"),
                    "lastRecordedAt": "",
                    "lastReceivedAt": alerts[0].get("createdAt"),
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
                }
                authors_by_raw[raw_author] = author_row

            author_row["securityAlerts"] = alerts

        for raw_author, author_row in authors_by_raw.items():
            if raw_author in hourly_by_author:
                continue

            hourly_by_author[raw_author] = {
                "author": author_row["displayName"],
                "rawAuthor": raw_author,
                "timeZoneId": profiles.get(raw_author, {}).get("timeZoneId"),
                "timeZoneDisplayName": profiles.get(raw_author, {}).get("timeZoneDisplayName"),
                "hourlyActivity": _empty_hourly_activity(),
            }

        presence_overrides = self._author_presence_overrides(authors_by_raw.keys())
        author_rows = [
            _with_alerts(
                _with_activity_mix(_with_productivity(author)),
                self.get_interval_for_author(author["rawAuthor"]),
                now,
                presence_overrides.get(author["rawAuthor"]),
            )
            for author in authors_by_raw.values()
        ]
        hourly_author_rows = [
            {**item, "hourlyActivity": _public_hourly_activity(item.get("hourlyActivity", []))}
            for item in hourly_by_author.values()
        ]

        return {
            "totals": totals,
            "activityMix": sorted(activity_mix, key=lambda item: item["count"], reverse=True),
            "savedPrefabs": sorted(saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "overtimeActivityMix": sorted(overtime_activity_mix, key=lambda item: item["count"], reverse=True),
            "overtimeSavedPrefabs": sorted(overtime_saved_prefabs.values(), key=lambda item: item.get("saveCount", 0), reverse=True),
            "authors": sorted(author_rows, key=lambda item: item["displayName"].lower()),
            "profiles": self.author_profiles(),
            "authorAliases": self.author_aliases(),
            "hourlyActivityByAuthor": sorted(hourly_author_rows, key=lambda item: item["author"]),
        }

    def _author_presence_overrides(self, raw_authors: Any) -> dict[str, dict[str, Any]]:
        authors = [str(author or "") for author in raw_authors if str(author or "")]

        if not authors:
            return {}

        latest_break_by_author: dict[str, dict[str, Any]] = {}

        for event in self.db.break_events.find({"rawAuthor": {"$in": authors}, "eventType": {"$in": ["online", "offline"]}}, {"_id": 0}):
            raw_author = str(event.get("rawAuthor") or "")
            timestamp = _coerce_datetime(event.get("timestamp"))

            if not raw_author or not timestamp:
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

    def analytics_summary(self, period: str = "7d") -> dict[str, Any]:
        year = dt.date.today().year
        profiles = self._profiles_by_raw_author()
        start_date = dt.date(year - 1, 12, 1).isoformat()
        end_date = dt.date(year, 12, 31).isoformat()
        docs = list(self.db.daily_author_activity.find(_date_query(start_date, end_date), {"_id": 0}))
        authors = set(self.list_authors())

        for item in docs:
            if item.get("author"):
                authors.add(str(item.get("author")))

        author_summaries = []

        for raw_author in sorted(authors):
            profile = profiles.get(raw_author, {})
            author_docs = [item for item in docs if item.get("author") == raw_author]
            author_summaries.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "months": _analytics_year_months(author_docs, year),
                }
            )

        return {
            "year": year,
            "authors": sorted(author_summaries, key=lambda item: item["displayName"].lower()),
        }

    def _security_alerts_by_author(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, list[dict[str, Any]]]:
        query: dict[str, Any] = {}
        created_filter: dict[str, dt.datetime] = {}

        if start_date:
            created_filter["$gte"] = _date_start(start_date)

        if end_date:
            created_filter["$lt"] = _date_start(end_date) + dt.timedelta(days=1)

        if created_filter:
            query["createdAt"] = created_filter

        by_author: dict[str, list[dict[str, Any]]] = {}

        events = list(self.db.report_security_events.find(query, {"_id": 0}).sort("createdAt", DESCENDING).limit(100))

        if query and not events:
            events = list(self.db.report_security_events.find({}, {"_id": 0}).sort("createdAt", DESCENDING).limit(100))

        for index, event in enumerate(events):
            raw_author = self.resolve_author_alias(event.get("author") or "Unknown User")
            created_at = _iso(event.get("createdAt"))
            challenge_id = event.get("challengeId")
            device_id = event.get("deviceId")
            alert = {
                "id": f"security:{raw_author}:{challenge_id or device_id or created_at or index}",
                "type": "report_forgery_attempt",
                "severity": "critical",
                "title": "Report forgery attempt",
                "message": event.get("message") or "A suspicious report submission was rejected.",
                "value": None,
                "threshold": None,
                "source": event.get("source"),
                "pluginVersion": event.get("pluginVersion"),
                "authorEmail": event.get("authorEmail"),
                "deviceId": device_id,
                "challengeId": challenge_id,
                "createdAt": created_at,
            }
            by_author.setdefault(raw_author, []).append(alert)

        return by_author

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
                {"author": raw_author},
                {"_id": 0, "authorEmail": 1, "timeZoneId": 1, "timeZoneDisplayName": 1},
                sort=[("lastReceivedAt", DESCENDING)],
            )
            result.append(
                {
                    "rawAuthor": raw_author,
                    "authorEmail": profile.get("authorEmail") or (author_activity or {}).get("authorEmail", ""),
                    "displayName": _display_name(raw_author, profile),
                    "team": profile.get("team", ""),
                    "telegramUsername": profile.get("telegramUsername", ""),
                    "discordUserId": profile.get("discordUserId", ""),
                    "discordUsername": profile.get("discordUsername", ""),
                    "pluginEnabled": profile.get("pluginEnabled", True),
                    "authorColor": profile.get("authorColor") or _author_color(raw_author),
                    "timeZoneId": profile.get("timeZoneId") or (author_activity or {}).get("timeZoneId", ""),
                    "timeZoneDisplayName": profile.get("timeZoneDisplayName")
                    or (author_activity or {}).get("timeZoneDisplayName", ""),
                }
            )

        return result

    def update_author_email(self, raw_author: str, author_email: str | None) -> None:
        raw_author = _normalize_author(raw_author)
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

    def update_author_time_zone(
        self, raw_author: str, time_zone_id: Any, time_zone_display_name: Any | None = None
    ) -> None:
        raw_author = _normalize_author(raw_author)
        normalized_time_zone = _author_configured_time_zone_id(raw_author) or _valid_time_zone_id(time_zone_id)

        if not raw_author or not normalized_time_zone:
            return

        display_name = str(time_zone_display_name or "").strip() or normalized_time_zone
        current = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author}, {"_id": 0, "timeZoneId": 1, "timeZoneDisplayName": 1}
        ) or {}
        previous_time_zone = _valid_time_zone_id(current.get("timeZoneId"))
        previous_display_name = str(current.get("timeZoneDisplayName") or "").strip()
        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$set": {
                    "rawAuthor": raw_author,
                    "timeZoneId": normalized_time_zone,
                    "timeZoneDisplayName": display_name,
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

        if previous_time_zone != normalized_time_zone or previous_display_name != display_name:
            self._rebucket_author_telegram_time_zone(raw_author, normalized_time_zone, display_name)

    def _rebucket_author_telegram_time_zone(self, raw_author: str, time_zone_id: str, time_zone_display_name: str) -> None:
        for event in self.db.break_events.find({"rawAuthor": raw_author}, {"_id": 1, "timestamp": 1}):
            event_time = _coerce_datetime(event.get("timestamp"))

            if event_time:
                self.db.break_events.update_one(
                    _document_identity_query(event),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )

        for collection_name, time_field in (
            ("day_sessions", "startedAt"),
            ("break_sessions", "startedAt"),
            ("break_intervals", "startedAt"),
        ):
            collection = getattr(self.db, collection_name)

            for item in collection.find({"rawAuthor": raw_author}, {"_id": 1, time_field: 1}):
                event_time = _coerce_datetime(item.get(time_field))

                if event_time:
                    collection.update_one(
                        _document_identity_query(item),
                        {
                            "$set": {
                                "date": _telegram_event_date(event_time, time_zone_id),
                                "timeZoneId": time_zone_id,
                                "timeZoneDisplayName": time_zone_display_name,
                            }
                        },
                    )

        for row in self.db.report_rows.find({"source": "telegram", "author": raw_author}, {"_id": 1, "recordedAt": 1}):
            event_time = _coerce_datetime(row.get("recordedAt"))

            if event_time:
                self.db.report_rows.update_one(
                    _document_identity_query(row),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )

    def upsert_author_profile(
        self,
        raw_author: str,
        display_name: str | None,
        team: str | None,
        telegram_username: str | None,
        discord_user_id: str | None = None,
        discord_username: str | None = None,
        plugin_enabled: bool = True,
        author_color: str | None = None,
        time_zone_id: str | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        raw_author = _normalize_author(raw_author)
        normalized_telegram = _normalize_telegram_username(telegram_username)
        normalized_discord_user_id = _normalize_discord_user_id(discord_user_id)
        normalized_discord_username = str(discord_username or "").strip()
        update = {
            "rawAuthor": raw_author,
            "displayName": (display_name or raw_author).strip(),
            "team": (team or "").strip(),
            "pluginEnabled": plugin_enabled,
            "authorColor": _valid_color(author_color) or _author_color(raw_author),
            "updatedAt": now,
        }
        normalized_time_zone = _valid_time_zone_id(time_zone_id)

        if normalized_time_zone:
            update["timeZoneId"] = normalized_time_zone

        operation: dict[str, Any] = {"$set": update}

        if normalized_telegram:
            update["telegramUsername"] = normalized_telegram
        else:
            operation["$unset"] = {"telegramUsername": ""}

        if normalized_discord_user_id:
            update["discordUserId"] = normalized_discord_user_id
        else:
            operation.setdefault("$unset", {})["discordUserId"] = ""

        if normalized_discord_username:
            update["discordUsername"] = normalized_discord_username
        else:
            operation.setdefault("$unset", {})["discordUsername"] = ""

        self.db.author_profiles.update_one({"rawAuthor": raw_author}, operation, upsert=True)
        return {"ok": True, "profile": {k: v for k, v in update.items() if k != "updatedAt"}}

    def delete_author_data(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0, "telegramUsername": 1}) or {}
        raw_report_ids = set()

        for snapshot in self.db.activity_snapshots.find({"author": normalized_author}, {"rawReportId": 1}):
            if snapshot.get("rawReportId"):
                raw_report_ids.add(snapshot["rawReportId"])

        for batch in self.db.raw_event_batches.find({"author": normalized_author}, {"rawReportId": 1}):
            if batch.get("rawReportId"):
                raw_report_ids.add(batch["rawReportId"])

        state_key_pattern = f"(^|\\|){re.escape(normalized_author)}\\|"
        counts = {
            "rawReports": self.db.raw_reports.delete_many({"_id": {"$in": list(raw_report_ids)}}).deleted_count if raw_report_ids else 0,
            "activitySnapshots": self.db.activity_snapshots.delete_many({"author": normalized_author}).deleted_count,
            "rawEventBatches": self.db.raw_event_batches.delete_many({"author": normalized_author}).deleted_count,
            "rawActivityEvents": self.db.raw_activity_events.delete_many({"author": normalized_author}).deleted_count,
            "reportRows": self.db.report_rows.delete_many({"author": normalized_author}).deleted_count,
            "dailyAuthorActivity": self.db.daily_author_activity.delete_many({"author": normalized_author}).deleted_count,
            "aggregateSessionState": self.db.aggregate_session_state.delete_many({"_id": {"$regex": state_key_pattern}}).deleted_count,
            "reportSecurityEvents": self.db.report_security_events.delete_many({"author": normalized_author}).deleted_count,
            "reportRefreshRequests": self.db.report_refresh_requests.delete_many({"author": normalized_author}).deleted_count,
            "manualReportExpectations": self.db.manual_report_expectations.delete_many({"author": normalized_author}).deleted_count,
            "breakEvents": self.db.break_events.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "breakSessions": self.db.break_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "breakIntervals": self.db.break_intervals.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "daySessions": self.db.day_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "telegramDayReminders": self.db.telegram_day_reminders.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "telegramOnlinePrompts": self.db.telegram_online_prompts.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingEvents": self.db.meeting_events.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingSessions": self.db.meeting_sessions.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "meetingIntervals": self.db.meeting_intervals.delete_many({"rawAuthor": normalized_author}).deleted_count,
            "reportChallenges": self.db.report_challenges.delete_many({"author": normalized_author}).deleted_count,
        }

        telegram_username = profile.get("telegramUsername")

        if telegram_username:
            counts["breakEvents"] += self.db.break_events.delete_many({"telegramUsername": telegram_username}).deleted_count
            counts["breakSessions"] += self.db.break_sessions.delete_many({"telegramUsername": telegram_username}).deleted_count

        return {"ok": True, "author": normalized_author, "deleted": counts}

    def delete_author_profile(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)

        if not normalized_author:
            return {"ok": False, "error": "Author is required"}

        data_result = self.delete_author_data(normalized_author)
        counts = dict(data_result.get("deleted", {}))
        counts["authorProfiles"] = self.db.author_profiles.delete_many({"rawAuthor": normalized_author}).deleted_count
        counts["intervalSettings"] = self.db.interval_settings.delete_many({"kind": "author", "author": normalized_author}).deleted_count
        counts["calendarMarks"] = self.db.calendar_marks.delete_many({"rawAuthor": normalized_author}).deleted_count

        return {"ok": True, "author": normalized_author, "deleted": counts}

    def record_break_event(self, telegram_username: str, event_type: str, timestamp: str | None = None) -> dict[str, Any]:
        normalized_telegram = _normalize_telegram_username(telegram_username)
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"telegramUsername": normalized_telegram})

        if not profile:
            return {"ok": False, "error": "Unknown telegram username"}

        raw_author = profile["rawAuthor"]
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)
        self.db.break_events.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
            }
        )

        if event_type == "afk":
            session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

            if session:
                self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_already_started")
                return {"ok": True, "status": "break_already_started"}

            self.db.break_sessions.update_one(
                {"telegramUsername": normalized_telegram},
                {
                    "$set": {
                        "telegramUsername": normalized_telegram,
                        "rawAuthor": raw_author,
                        "startedAt": event_time,
                        "date": event_date,
                        "timeZoneId": time_zone_id,
                    }
                },
                upsert=True,
            )
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_started")
            return {"ok": True, "status": "break_started"}

        if event_type == "offline":
            break_result = self._close_break_session(normalized_telegram, raw_author, event_time)
            day_date = event_date
            day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})

            if not day_state:
                self._insert_telegram_report_row(
                    raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "offline_without_online", break_result
                )
                return {"ok": True, "status": "offline_without_online", **break_result}

            started_at = _coerce_datetime(day_state["startedAt"]) or event_time
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"lastOfflineAt": event_time, "daySeconds": day_seconds}},
                upsert=True,
            )
            self._upsert_telegram_day_activity(raw_author, normalized_telegram, day_date, time_zone_id, started_at, event_time, received_at, day_seconds)
            self._insert_telegram_report_row(
                raw_author,
                normalized_telegram,
                event_type,
                event_time,
                event_date,
                time_zone_id,
                received_at,
                "day_closed",
                {"daySeconds": day_seconds, **break_result},
            )
            return {"ok": True, "status": "day_closed", "daySeconds": day_seconds, **break_result}

        online_date = event_date
        self.db.day_sessions.update_one(
            {"rawAuthor": raw_author, "date": online_date},
            {
                "$setOnInsert": {
                    "telegramUsername": normalized_telegram,
                    "rawAuthor": raw_author,
                    "date": online_date,
                    "startedAt": event_time,
                    "daySeconds": 0,
                    "timeZoneId": time_zone_id,
                },
                "$set": {"lastOnlineAt": event_time},
            },
            upsert=True,
        )

        self._invalidate_telegram_online_prompts_for_online_day(raw_author, online_date)

        break_result = self._close_break_session(normalized_telegram, raw_author, event_time)

        if not break_result:
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "online_recorded")
            return {"ok": True, "status": "online_recorded"}

        self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_closed", break_result)
        return {"ok": True, "status": "break_closed", **break_result}

    def record_discord_voice_event(
        self,
        discord_user_id: str,
        discord_username: str | None,
        event_type: str,
        guild_id: str | None = None,
        channel_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        normalized_discord_user_id = _normalize_discord_user_id(discord_user_id)
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"discordUserId": normalized_discord_user_id})

        if not profile:
            return {"ok": False, "error": "Unknown Discord user"}

        raw_author = profile["rawAuthor"]
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)
        normalized_discord_username = str(discord_username or profile.get("discordUsername") or "").strip()
        event_type = event_type if event_type in {"join", "leave", "reconcile"} else "reconcile"

        self.db.meeting_events.insert_one(
            {
                "discordUserId": normalized_discord_user_id,
                "discordUsername": normalized_discord_username,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "guildId": str(guild_id or ""),
                "channelId": str(channel_id or ""),
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
            }
        )

        if normalized_discord_username and normalized_discord_username != profile.get("discordUsername"):
            self.db.author_profiles.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"discordUsername": normalized_discord_username, "updatedAt": received_at}},
            )

        if event_type in {"join", "reconcile"}:
            session = self.db.meeting_sessions.find_one({"discordUserId": normalized_discord_user_id})

            if session:
                self._insert_discord_meeting_report_row(
                    raw_author,
                    normalized_discord_user_id,
                    normalized_discord_username,
                    event_type,
                    event_time,
                    event_date,
                    time_zone_id,
                    received_at,
                    "meeting_already_started",
                    guild_id,
                    channel_id,
                )
                return {"ok": True, "status": "meeting_already_started"}

            self.db.meeting_sessions.update_one(
                {"discordUserId": normalized_discord_user_id},
                {
                    "$set": {
                        "discordUserId": normalized_discord_user_id,
                        "discordUsername": normalized_discord_username,
                        "rawAuthor": raw_author,
                        "guildId": str(guild_id or ""),
                        "channelId": str(channel_id or ""),
                        "startedAt": event_time,
                        "date": event_date,
                        "timeZoneId": time_zone_id,
                    }
                },
                upsert=True,
            )
            self._insert_discord_meeting_report_row(
                raw_author,
                normalized_discord_user_id,
                normalized_discord_username,
                event_type,
                event_time,
                event_date,
                time_zone_id,
                received_at,
                "meeting_started",
                guild_id,
                channel_id,
            )
            return {"ok": True, "status": "meeting_started"}

        meeting_result = self._close_meeting_session(
            normalized_discord_user_id,
            raw_author,
            normalized_discord_username,
            event_time,
            received_at,
            guild_id,
            channel_id,
        )
        status = "meeting_closed" if meeting_result else "meeting_leave_without_join"
        self._insert_discord_meeting_report_row(
            raw_author,
            normalized_discord_user_id,
            normalized_discord_username,
            event_type,
            event_time,
            event_date,
            time_zone_id,
            received_at,
            status,
            guild_id,
            channel_id,
            meeting_result,
        )
        return {"ok": True, "status": status, **meeting_result}

    def _close_meeting_session(
        self,
        normalized_discord_user_id: str,
        raw_author: str,
        discord_username: str,
        event_time: dt.datetime,
        received_at: dt.datetime,
        guild_id: str | None,
        channel_id: str | None,
    ) -> dict[str, Any]:
        session = self.db.meeting_sessions.find_one({"discordUserId": normalized_discord_user_id})

        if not session:
            return {}

        started_at = _coerce_datetime(session["startedAt"]) or event_time
        meeting_seconds = max(0, int((event_time - started_at).total_seconds()))
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        meeting_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
        self.db.meeting_sessions.delete_one({"discordUserId": normalized_discord_user_id})
        self.db.meeting_intervals.insert_one(
            {
                "discordUserId": normalized_discord_user_id,
                "discordUsername": discord_username or session.get("discordUsername", ""),
                "rawAuthor": raw_author,
                "guildId": str(guild_id or session.get("guildId") or ""),
                "channelId": str(channel_id or session.get("channelId") or ""),
                "startedAt": started_at,
                "endedAt": event_time,
                "date": meeting_date,
                "timeZoneId": time_zone_id,
                "meetingSeconds": meeting_seconds,
                "createdAt": received_at,
            }
        )
        return {"meetingSeconds": meeting_seconds}

    def _insert_discord_meeting_report_row(
        self,
        raw_author: str,
        discord_user_id: str,
        discord_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        guild_id: str | None,
        channel_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        deltas = _empty_event_deltas()
        self.db.report_rows.insert_one(
            {
                "source": "discord",
                "pluginVersion": "discord-bot",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "discord",
                "sessionId": discord_user_id,
                "deviceId": "",
                "date": event_date,
                "recordedAt": event_time.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": event_time.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "meeting",
                "activityType": f"meeting_{event_type}",
                "discordEventType": event_type,
                "discordStatus": status,
                "discordUserId": discord_user_id,
                "discordUsername": discord_username,
                "metadata": {"guildId": str(guild_id or ""), "channelId": str(channel_id or ""), **(metadata or {})},
                **deltas,
            }
        )

    def claim_due_telegram_day_reminders(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        reminders: list[dict[str, Any]] = []
        profiles = self._profiles_by_raw_author()

        for session in self.db.day_sessions.find({}, {"_id": 0}):
            if session.get("lastOfflineAt"):
                continue

            raw_author = str(session.get("rawAuthor") or "")
            telegram_username = _normalize_telegram_username(session.get("telegramUsername") or profiles.get(raw_author, {}).get("telegramUsername"))
            day_date = str(session.get("date") or "")
            started_at = _coerce_datetime(session.get("startedAt"))

            if not raw_author or not telegram_username or not day_date or not started_at:
                continue

            elapsed_seconds = max(0, int((now - started_at).total_seconds()))

            if elapsed_seconds < TELEGRAM_DAY_REMINDER_SECONDS:
                continue

            reminder_key = {"rawAuthor": raw_author, "date": day_date}
            current = self.db.telegram_day_reminders.find_one(reminder_key, {"_id": 0}) or {}

            if current.get("status") in {"sent", "closed"}:
                continue

            reminder_id = str(current.get("reminderId") or _new_id())
            self.db.telegram_day_reminders.update_one(
                reminder_key,
                {
                    "$set": {
                        **reminder_key,
                        "reminderId": reminder_id,
                        "telegramUsername": telegram_username,
                        "startedAt": started_at,
                        "elapsedSeconds": elapsed_seconds,
                        "status": "claimed",
                        "lastClaimedAt": now,
                    }
                },
                upsert=True,
            )
            reminders.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": telegram_username,
                    "date": day_date,
                    "startedAt": started_at.isoformat(),
                    "elapsedSeconds": elapsed_seconds,
                }
            )

        return reminders

    def mark_telegram_day_reminder_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_day_reminders.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def close_telegram_day_from_reminder(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"offline", "overtime"} else "offline"
        reminder = self.db.telegram_day_reminders.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": f"reminder_{reminder.get('closeAction') or action}_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        raw_author = str(reminder.get("rawAuthor") or "")
        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        day_date = str(reminder.get("date") or "")
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0}) or {}
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or _valid_time_zone_id(reminder.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)

        if not raw_author or not telegram_username or not day_date:
            return {"ok": False, "error": "Reminder is missing author data"}

        self.db.break_events.insert_one(
            {
                "telegramUsername": telegram_username,
                "rawAuthor": raw_author,
                "eventType": "offline",
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
                "source": "telegram_reminder",
                "reminderAction": action,
            }
        )
        break_result = self._close_break_session(telegram_username, raw_author, event_time)
        day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})
        metadata: dict[str, Any] = {"reminderAction": action, **break_result}

        if not day_state:
            self._insert_telegram_report_row(
                raw_author,
                telegram_username,
                "offline",
                event_time,
                event_date,
                time_zone_id,
                received_at,
                f"reminder_{action}_without_online",
                metadata,
            )
        else:
            started_at = _coerce_datetime(day_state.get("startedAt")) or event_time
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            metadata["daySeconds"] = day_seconds
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"date": event_date, "lastOfflineAt": event_time, "daySeconds": day_seconds, "reminderAction": action}},
                upsert=True,
            )
            self._upsert_telegram_day_activity(raw_author, telegram_username, event_date, time_zone_id, started_at, event_time, received_at, day_seconds)
            self._insert_telegram_report_row(
                raw_author,
                telegram_username,
                "offline",
                event_time,
                event_date,
                time_zone_id,
                received_at,
                f"reminder_{action}",
                metadata,
            )

        self.db.telegram_day_reminders.update_one(
            {"reminderId": reminder_id},
            {"$set": {"status": "closed", "closedAt": received_at, "closeAction": action, "updatedAt": received_at}},
        )
        return {"ok": True, "status": f"reminder_{action}", **metadata}

    def _schedule_telegram_online_prompt_if_needed(
        self, raw_author: str, day_date: str, source: str, received_at: dt.datetime
    ) -> None:
        if source != "ual" or not raw_author or not day_date:
            return

        if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
            return

        if self.db.telegram_online_prompts.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
            return

        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0, "telegramUsername": 1}) or {}
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))

        if not telegram_username:
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.insert_one(
            {
                "reminderId": _new_id(),
                "rawAuthor": raw_author,
                "date": day_date,
                "telegramUsername": telegram_username,
                "firstReportReceivedAt": received_at,
                "status": "pending",
                "createdAt": now,
                "updatedAt": now,
            }
        )

    def _invalidate_telegram_online_prompts_for_online_day(self, raw_author: str, day_date: str) -> None:
        if not raw_author or not day_date:
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.update_many(
            {
                "rawAuthor": raw_author,
                "date": day_date,
                "status": {"$in": ["pending", "claimed", "sent"]},
            },
            {
                "$set": {
                    "status": "closed",
                    "closedAt": now,
                    "closeAction": "cancelled_by_online",
                    "updatedAt": now,
                }
            },
        )

    def claim_due_telegram_online_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        due: list[dict[str, Any]] = []

        for doc in list(self.db.telegram_online_prompts.find({"status": "pending"}, {"_id": 0})):
            raw_author = str(doc.get("rawAuthor") or "")
            day_date = str(doc.get("date") or "")
            anchor = _coerce_datetime(doc.get("firstReportReceivedAt"))

            if not raw_author or not day_date or not anchor:
                continue

            if (now - anchor).total_seconds() < TELEGRAM_ONLINE_PROMPT_DELAY_SECONDS:
                continue

            if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
                self.db.telegram_online_prompts.update_one(
                    {"reminderId": doc.get("reminderId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closedAt": now,
                            "closeAction": "superseded_day_session",
                            "updatedAt": now,
                        }
                    },
                )
                continue

            reminder_id = str(doc.get("reminderId") or "")
            self.db.telegram_online_prompts.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            due.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": str(doc.get("telegramUsername") or ""),
                    "date": day_date,
                    "firstReportReceivedAt": anchor.isoformat(),
                }
            )

        return due

    def mark_telegram_online_prompt_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def close_telegram_online_prompt(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"confirm_online", "dismiss"} else "dismiss"
        reminder = self.db.telegram_online_prompts.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder", "status": "unknown_reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": "online_prompt_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        raw_author = str(reminder.get("rawAuthor") or "")
        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)
        received_at = dt.datetime.now(dt.UTC)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        if action == "dismiss":
            self.db.telegram_online_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "dismiss",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "online_prompt_dismissed"}

        break_result = self.record_break_event(telegram_username, "online", timestamp)
        if not break_result.get("ok"):
            return {**break_result, "status": str(break_result.get("status") or "online_failed")}

        self.db.telegram_online_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": received_at,
                    "closeAction": "confirm_online",
                    "updatedAt": received_at,
                }
            },
        )
        return {"ok": True, "status": "online_prompt_confirmed_online", **break_result}

    def _upsert_telegram_day_activity(
        self,
        raw_author: str,
        telegram_username: str,
        event_date: str,
        time_zone_id: str,
        started_at: dt.datetime,
        ended_at: dt.datetime,
        received_at: dt.datetime,
        day_seconds: int,
    ) -> None:
        key = {"source": "telegram", "author": raw_author, "projectId": "telegram", "date": event_date}
        self.db.daily_author_activity.update_one(
            key,
            {
                "$setOnInsert": {
                    **key,
                    "authorEmail": "",
                    "pluginVersion": "telegram-bot",
                    "workWindowSeconds": DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
                    "activityCounts": [],
                    "savedPrefabs": [],
                    "overtimeActivityCounts": [],
                    "overtimeSavedPrefabs": [],
                    "hourlyActivity": _empty_hourly_activity(),
                    "activeSeconds": 0,
                    "activeMicroseconds": 0,
                    "idleSeconds": 0,
                    "idleMicroseconds": 0,
                    "overtimeActiveSeconds": 0,
                    "overtimeActiveMicroseconds": 0,
                },
                "$set": {
                    "sessionId": telegram_username,
                    "timeZoneId": time_zone_id,
                    "timeZoneDisplayName": time_zone_id,
                    "lastRecordedAt": ended_at.isoformat(),
                    "lastReceivedAt": received_at,
                    "dayStartedAt": started_at,
                    "dayEndedAt": ended_at,
                    "daySeconds": day_seconds,
                },
            },
            upsert=True,
        )

    def _insert_telegram_report_row(
        self,
        raw_author: str,
        telegram_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        deltas = _empty_event_deltas()
        self.db.report_rows.insert_one(
            {
                "source": "telegram",
                "pluginVersion": "telegram-bot",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "telegram",
                "sessionId": telegram_username,
                "deviceId": "",
                "date": event_date,
                "recordedAt": event_time.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": event_time.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "telegram",
                "activityType": f"telegram_{event_type}",
                "telegramEventType": event_type,
                "telegramStatus": status,
                "telegramUsername": telegram_username,
                "metadata": metadata or {},
                **deltas,
            }
        )

    def _close_break_session(self, normalized_telegram: str, raw_author: str, event_time: dt.datetime) -> dict[str, Any]:
        session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

        if not session:
            return {}

        started_at = _coerce_datetime(session["startedAt"]) or event_time
        break_seconds = max(0, int((event_time - started_at).total_seconds()))
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        break_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
        self.db.break_sessions.delete_one({"telegramUsername": normalized_telegram})
        self.db.break_intervals.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "startedAt": started_at,
                "endedAt": event_time,
                "date": break_date,
                "timeZoneId": time_zone_id,
                "breakSeconds": break_seconds,
            }
        )
        self.db.daily_author_activity.update_many(
            {"author": raw_author, "date": break_date},
            {"$inc": {"breakSeconds": break_seconds}, "$set": {"updatedAt": dt.datetime.now(dt.UTC)}},
        )
        return {"breakSeconds": break_seconds}

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
            (item.get("author") or "Unknown User", item.get("date") or "")
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

        for interval in self.db.meeting_intervals.find(interval_query, {"_id": 0}):
            _add_meeting_interval_to_buckets(
                buckets,
                interval.get("rawAuthor"),
                _coerce_datetime(interval.get("startedAt")),
                _coerce_datetime(interval.get("endedAt")),
                _author_time_zone_id(interval.get("rawAuthor"), profiles, interval.get("timeZoneId")),
            )

        now = now or dt.datetime.now(dt.UTC)

        for session in self.db.meeting_sessions.find({"rawAuthor": {"$in": authors}}, {"_id": 0}):
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            _add_meeting_interval_to_buckets(
                buckets,
                session.get("rawAuthor"),
                started_at,
                now,
                _author_time_zone_id(session.get("rawAuthor"), profiles, session.get("timeZoneId")),
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
        first_activity_by_key: dict[tuple[str, str], dt.datetime] = {}

        for event in self.db.break_events.find(
            {"rawAuthor": {"$in": authors}, "date": {"$in": dates}, "eventType": "online"},
            {"_id": 0},
        ):
            key = (str(event.get("rawAuthor") or "Unknown User"), str(event.get("date") or ""))

            if key not in author_dates:
                continue

            timestamp = _coerce_datetime(event.get("timestamp"))

            if timestamp and (key not in first_online_by_key or timestamp < first_online_by_key[key]["timestamp"]):
                first_online_by_key[key] = {
                    "timestamp": timestamp,
                    "timeZoneId": str(event.get("timeZoneId") or "UTC"),
                }

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

            occurred_at = (
                _coerce_datetime(row.get("recordedAt"))
                or _coerce_datetime(row.get("lastRecordedAt"))
                or _coerce_datetime(row.get("receivedAt"))
            )

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
            gaps[key] = {
                "seconds": gap_seconds,
                "hourlyActivity": hourly_activity,
            }

        return gaps

    def _apply_live_telegram_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
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
            raw_author = session.get("rawAuthor") or "Unknown User"
            day_date = session.get("date") or ""
            started_at = _coerce_datetime(session.get("startedAt"))

            if not day_date or not started_at:
                continue

            ended_at = _coerce_datetime(session.get("lastOfflineAt"))

            if ended_at and not _date_in_summary_scope(day_date, raw_author, profiles, None, now, start_date, end_date, date_mode):
                continue

            live_day_seconds = int(session.get("daySeconds", 0))

            is_open_day_over_cap = False

            if not ended_at:
                uncapped_live_day_seconds = max(0, int((now - started_at).total_seconds()))
                is_open_day_over_cap = uncapped_live_day_seconds > TELEGRAM_DAY_REMINDER_SECONDS
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

            if is_open_day_over_cap:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row.setdefault("telegramAlerts", []).append(
                    {
                        "id": f"telegram_day_open:{raw_author}:{day_date}",
                        "type": "telegram_day_open",
                        "severity": "warning",
                        "title": "Telegram day still open",
                        "message": "Telegram day was not closed after 10 hours and is capped on the dashboard.",
                        "value": uncapped_live_day_seconds,
                        "threshold": TELEGRAM_DAY_REMINDER_SECONDS,
                    }
                )

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

        meeting_query = _report_date_query(start_date, end_date, date_mode, profiles, now)

        for interval in self.db.meeting_intervals.find(meeting_query, {"_id": 0}):
            raw_author = interval.get("rawAuthor") or "Unknown User"
            meeting_date = interval.get("date") or ""

            if not _date_in_summary_scope(meeting_date, raw_author, profiles, interval.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            meeting_seconds = int(interval.get("meetingSeconds", 0))
            existing_meeting_seconds = meeting_seconds_by_author_date.get((raw_author, meeting_date), 0)
            meeting_delta_seconds = max(0, meeting_seconds - existing_meeting_seconds)

            if meeting_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["meetingSeconds"] += meeting_delta_seconds
                totals["meetingSeconds"] += meeting_delta_seconds
                meeting_seconds_by_author_date[(raw_author, meeting_date)] = existing_meeting_seconds + meeting_delta_seconds

        for session in self.db.meeting_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            meeting_date = str(session.get("date") or _telegram_event_date(started_at, _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))))

            if not _date_in_summary_scope(meeting_date, raw_author, profiles, session.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            live_meeting_seconds = max(0, int((now - started_at).total_seconds()))
            existing_meeting_seconds = meeting_seconds_by_author_date.get((raw_author, meeting_date), 0)
            meeting_delta_seconds = max(0, live_meeting_seconds - existing_meeting_seconds)

            if meeting_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["meetingSeconds"] += meeting_delta_seconds
                totals["meetingSeconds"] += meeting_delta_seconds

            key = (raw_author, meeting_date)
            if key not in meeting_buckets:
                meeting_buckets[key] = _empty_hourly_activity()
                _add_meeting_interval_to_buckets(
                    meeting_buckets,
                    raw_author,
                    started_at,
                    now,
                    _author_time_zone_id(raw_author, profiles, session.get("timeZoneId")),
                )

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
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [],
            "activityMix": [],
            "savedPrefabs": [],
            "telegramAlerts": [],
        }
        authors_by_raw[raw_author] = author_row
        return author_row

    def _profiles_by_raw_author(self) -> dict[str, dict[str, Any]]:
        return {
            item["rawAuthor"]: item
            for item in self.db.author_profiles.find({}, {"_id": 0})
            if item.get("rawAuthor")
        }

    def rebuild_aggregates_if_needed(self, force: bool = False) -> None:
        metadata = self.db.aggregate_metadata.find_one({"kind": "activity"})

        if not force and metadata and metadata.get("version") == self.aggregates_version:
            return

        self.db.report_rows.delete_many({})
        self.db.daily_author_activity.delete_many({})
        self.db.aggregate_session_state.delete_many({})

        snapshots = self.db.activity_snapshots.find({}).sort("receivedAt", ASCENDING)

        for snapshot in snapshots:
            self._apply_snapshot_to_aggregates(snapshot)

        raw_events = self.db.raw_activity_events.find({}).sort("occurredAtUtc", ASCENDING)

        for event in raw_events:
            deltas = self._apply_raw_event_to_aggregates(event)
            if not _has_time_delta(deltas):
                continue
            resolved_author = self.resolve_author_alias(event.get("author") or "Unknown User")

            self.db.report_rows.insert_one(
                {
                    "source": event.get("source"),
                    "pluginVersion": event.get("pluginVersion"),
                    "author": resolved_author,
                    "authorEmail": event.get("authorEmail", ""),
                    "projectId": event.get("projectId") or "",
                    "sessionId": event.get("sessionId") or "",
                    "deviceId": event.get("deviceId") or "",
                    "date": event.get("date"),
                    "recordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
                    "receivedAt": event.get("receivedAt"),
                    "lastRecordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
                    "lastReceivedAt": event.get("receivedAt"),
                    "timeZoneId": event.get("timeZoneId"),
                    "timeZoneDisplayName": event.get("timeZoneDisplayName"),
                    "rawReportId": event.get("rawReportId"),
                    "batchId": event.get("batchId"),
                    "reportType": event.get("reportType", "auto"),
                    **deltas,
                }
            )

        for event in self.db.break_events.find({}).sort("timestamp", ASCENDING):
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            self._insert_telegram_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("telegramUsername") or ""),
                str(event.get("eventType") or "telegram"),
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("eventType") or "telegram"),
            )

        for event in self.db.meeting_events.find({}).sort("timestamp", ASCENDING):
            event_time = _coerce_datetime(event.get("timestamp"))

            if not event_time:
                continue

            received_at = _coerce_datetime(event.get("createdAt")) or event_time
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            self._insert_discord_meeting_report_row(
                str(event.get("rawAuthor") or "Unknown User"),
                str(event.get("discordUserId") or ""),
                str(event.get("discordUsername") or ""),
                str(event.get("eventType") or "reconcile"),
                event_time,
                str(event.get("date") or _telegram_event_date(event_time, time_zone_id)),
                time_zone_id,
                received_at,
                str(event.get("eventType") or "meeting"),
                str(event.get("guildId") or ""),
                str(event.get("channelId") or ""),
            )

        self.db.aggregate_metadata.update_one(
            {"kind": "activity"},
            {"$set": {"kind": "activity", "version": self.aggregates_version, "rebuiltAt": dt.datetime.now(dt.UTC)}},
            upsert=True,
        )

    def _apply_snapshot_to_aggregates(self, snapshot: dict[str, Any]) -> None:
        snapshot = dict(snapshot)
        snapshot["author"] = self.resolve_author_alias(snapshot.get("author") or "Unknown User")
        self.update_author_time_zone(snapshot.get("author") or "Unknown User", snapshot.get("timeZoneId"), snapshot.get("timeZoneDisplayName"))
        session_key = _session_key(snapshot)
        previous = self.db.aggregate_session_state.find_one({"_id": session_key}) or {}
        deltas = _build_deltas(snapshot, previous.get("snapshot", {}))
        if self._should_suppress_post_offline_plugin_deltas(snapshot, deltas):
            self.db.aggregate_session_state.update_one(
                {"_id": session_key},
                {"$set": {"snapshot": _state_snapshot(snapshot), "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC))}},
                upsert=True,
            )
            return

        row = dict(snapshot)
        row.update(deltas)
        row["snapshotKey"] = session_key
        self.db.report_rows.insert_one(row)
        self._update_daily_author_activity(snapshot, deltas)
        received_at = _coerce_datetime(snapshot.get("receivedAt")) or dt.datetime.now(dt.UTC)
        if _has_time_delta(deltas) and str(snapshot.get("source") or "") == "ual":
            self._schedule_telegram_online_prompt_if_needed(
                str(snapshot.get("author") or "Unknown User"),
                str(snapshot.get("date") or ""),
                "ual",
                received_at,
            )
        self.db.aggregate_session_state.update_one(
            {"_id": session_key},
            {"$set": {"snapshot": _state_snapshot(snapshot), "updatedAt": snapshot.get("receivedAt", dt.datetime.now(dt.UTC))}},
            upsert=True,
        )

    def _should_suppress_post_offline_plugin_deltas(self, item: dict[str, Any], deltas: dict[str, Any]) -> bool:
        if item.get("source") in {"telegram", "discord"} or item.get("reportType") in {"telegram", "meeting"}:
            return False

        if not _has_time_delta(deltas):
            return False

        if _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0:
            return False

        received_at = _coerce_datetime(item.get("receivedAt") or item.get("lastReceivedAt"))

        if not received_at:
            return False

        return self._is_author_offline_after_latest_telegram_state(
            str(item.get("author") or "Unknown User"),
            str(item.get("date") or ""),
            received_at,
        )

    def _is_author_offline_after_latest_telegram_state(self, raw_author: str, day_date: str, at: dt.datetime) -> bool:
        latest_event_type = ""
        latest_timestamp: dt.datetime | None = None

        for event in self.db.break_events.find(
            {"rawAuthor": raw_author, "date": day_date, "eventType": {"$in": ["online", "offline"]}},
            {"_id": 0, "eventType": 1, "timestamp": 1},
        ):
            timestamp = _coerce_datetime(event.get("timestamp"))

            if not timestamp or timestamp > at:
                continue

            if not latest_timestamp or timestamp > latest_timestamp:
                latest_timestamp = timestamp
                latest_event_type = str(event.get("eventType") or "")

        return latest_event_type == "offline"

    def _save_event_batch(
        self,
        source: str,
        plugin_version: str,
        payload: dict[str, Any],
        raw_report_id: Any,
        report_type: str,
        received_at: dt.datetime,
        challenge_id: str,
        device_id: str | None,
    ) -> None:
        author = self.resolve_author_alias(str(payload.get("author") or "Unknown User"))
        author_email = str(payload.get("authorEmail") or "")
        project_id = str(payload.get("projectId") or "")
        session_id = str(payload.get("sessionId") or "")
        resolved_device_id = str(device_id or payload.get("deviceId") or "")
        batch_id = _new_id()
        batch_deltas = _empty_batch_deltas()
        last_event: dict[str, Any] | None = None

        self.update_author_email(author, author_email)
        self.db.raw_event_batches.insert_one(
            {
                "batchId": batch_id,
                "rawReportId": raw_report_id,
                "challengeId": challenge_id,
                "source": source,
                "pluginVersion": plugin_version,
                "author": author,
                "authorEmail": author_email,
                "projectId": project_id,
                "sessionId": session_id,
                "deviceId": resolved_device_id,
                "receivedAt": received_at,
                "sentAt": payload.get("sentAt"),
                "eventCount": len(payload.get("events") or []),
                "reportType": report_type,
            }
        )

        events = sorted(payload.get("events") or [], key=lambda item: str(item.get("occurredAtUtc") or item.get("occurredAtLocal") or ""))

        for raw_event in events:
            event = _normalize_raw_event(
                raw_event,
                source=source,
                plugin_version=plugin_version,
                author=author,
                author_email=author_email,
                project_id=project_id,
                session_id=session_id,
                device_id=resolved_device_id,
                batch_id=batch_id,
                raw_report_id=raw_report_id,
                received_at=received_at,
                report_type=report_type,
                time_zone_id=payload.get("timeZoneId"),
                time_zone_display_name=payload.get("timeZoneDisplayName"),
            )

            if not event:
                continue

            try:
                self.db.raw_activity_events.insert_one(event)
            except DuplicateKeyError:
                self.log_report_security_event(
                    event_type="duplicate_event",
                    source=source,
                    plugin_version=plugin_version,
                    author=author,
                    author_email=author_email,
                    project_id=project_id,
                    session_id=session_id,
                    device_id=resolved_device_id,
                    challenge_id=challenge_id,
                    message="Raw event id was submitted more than once.",
                )
                continue

            deltas = self._apply_raw_event_to_aggregates(event)
            _merge_batch_deltas(batch_deltas, deltas)
            last_event = event

        if not last_event:
            return

        if not _has_time_delta(batch_deltas):
            return

        row = {
            "source": source,
            "pluginVersion": plugin_version,
            "author": author,
            "authorEmail": author_email,
            "projectId": project_id,
            "sessionId": session_id,
            "deviceId": resolved_device_id,
            "date": last_event.get("date"),
            "recordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
            "receivedAt": received_at,
            "lastRecordedAt": last_event.get("occurredAtLocal") or last_event.get("occurredAtUtc"),
            "lastReceivedAt": received_at,
            "timeZoneId": payload.get("timeZoneId"),
            "timeZoneDisplayName": payload.get("timeZoneDisplayName"),
            "rawReportId": raw_report_id,
            "batchId": batch_id,
            "challengeId": challenge_id,
            "reportType": report_type,
            **batch_deltas,
        }
        self.db.report_rows.insert_one(row)
        self._schedule_telegram_online_prompt_if_needed(author, str(last_event.get("date") or ""), source, received_at)

    def _apply_raw_event_to_aggregates(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        event["author"] = self.resolve_author_alias(event.get("author") or "Unknown User")
        self.update_author_time_zone(event.get("author") or "Unknown User", event.get("timeZoneId"), event.get("timeZoneDisplayName"))
        state_key = _raw_event_session_key(event)
        previous = self.db.aggregate_session_state.find_one({"_id": state_key}) or {}
        state = dict(previous.get("state", {}))
        event_type = str(event.get("eventType") or "")
        occurred_at = _coerce_datetime(event.get("occurredAtUtc")) or event.get("occurredAt")
        occurred_at = occurred_at if isinstance(occurred_at, dt.datetime) else dt.datetime.now(dt.UTC)
        occurred_local_at = _parse_local_datetime(event.get("occurredAtLocal")) or occurred_at
        first_activity_at = _coerce_datetime(state.get("firstActivityAt"))
        last_activity_at = _coerce_datetime(state.get("lastActivityAt"))
        last_accounting_at = _coerce_datetime(state.get("lastAccountingAt"))
        last_activity_local_at = _parse_local_datetime(state.get("lastActivityLocalAt"))
        last_accounting_local_at = _parse_local_datetime(state.get("lastAccountingLocalAt"))
        last_activity_source = str(state.get("lastActivitySource") or "")
        last_accounting_source = str(state.get("lastAccountingSource") or "")
        current_source = str(event.get("source") or "")
        deltas = _empty_event_deltas()
        is_activity = _is_activity_event(event)
        consumed_normal_microseconds = self._normal_microseconds_consumed_for_event(event)
        idle_threshold_seconds = self.get_interval_for_author(str(event.get("author") or "Unknown User"))

        if is_activity:
            if not first_activity_at:
                first_activity_at = occurred_at
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source
            elif last_activity_at and last_accounting_at and occurred_at > last_activity_at:
                interval_is_active = (occurred_at - last_activity_at).total_seconds() < idle_threshold_seconds
                interval_deltas = _interval_deltas(
                    last_accounting_at,
                    occurred_at,
                    last_accounting_local_at or last_accounting_at,
                    occurred_local_at,
                    interval_is_active,
                    consumed_normal_microseconds,
                )
                _merge_batch_deltas(deltas, interval_deltas)
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source

            last_activity_at = occurred_at
            last_activity_local_at = occurred_local_at
            last_activity_source = current_source
        elif (
            event_type == "heartbeat"
            and first_activity_at
            and last_activity_at
            and last_accounting_at
            and occurred_at > last_accounting_at
            and (not last_activity_source or current_source == last_activity_source)
        ):
            if (occurred_at - last_activity_at).total_seconds() >= idle_threshold_seconds:
                interval_seconds = int((occurred_at - last_accounting_at).total_seconds())

                if interval_seconds < MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS:
                    return deltas

                interval_deltas = _interval_deltas(
                    last_accounting_at,
                    occurred_at,
                    last_accounting_local_at or last_accounting_at,
                    occurred_local_at,
                    False,
                    consumed_normal_microseconds,
                )
                _merge_batch_deltas(deltas, interval_deltas)
                last_accounting_at = occurred_at
                last_accounting_local_at = occurred_local_at
                last_accounting_source = current_source

        if is_activity:
            activity_type = _activity_count_type(event_type)
            activity_delta_key = "activityCountDeltas"

            if _is_overtime_event_delta(consumed_normal_microseconds, deltas):
                activity_delta_key = "overtimeActivityCountDeltas"

            deltas[activity_delta_key].append({"type": activity_type, "count": 1})

        saved_prefab = _saved_prefab_delta(event)

        if saved_prefab:
            saved_prefab_delta_key = "savedPrefabDeltas"

            if _is_overtime_event_delta(consumed_normal_microseconds, deltas):
                saved_prefab_delta_key = "overtimeSavedPrefabDeltas"

            deltas[saved_prefab_delta_key].append(saved_prefab)

        snapshot = {
            "source": event.get("source"),
            "author": event.get("author") or "Unknown User",
            "authorEmail": event.get("authorEmail", ""),
            "pluginVersion": event.get("pluginVersion"),
            "projectId": event.get("projectId") or "",
            "sessionId": event.get("sessionId") or "",
            "deviceId": event.get("deviceId") or "",
            "date": event.get("date") or occurred_at.date().isoformat(),
            "receivedAt": event.get("receivedAt"),
            "recordedAt": event.get("occurredAtLocal") or event.get("occurredAtUtc"),
            "timeZoneId": event.get("timeZoneId"),
            "timeZoneDisplayName": event.get("timeZoneDisplayName"),
            "workWindowSeconds": DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
        }
        suppress_deltas = self._should_suppress_post_offline_plugin_deltas(event, deltas)

        if not suppress_deltas:
            self._update_daily_author_activity(snapshot, deltas)

        self.db.aggregate_session_state.update_one(
            {"_id": state_key},
            {
                "$set": {
                    "state": {
                        "firstActivityAt": first_activity_at.isoformat() if first_activity_at else None,
                        "lastActivityAt": last_activity_at.isoformat() if last_activity_at else None,
                        "lastAccountingAt": last_accounting_at.isoformat() if last_accounting_at else None,
                        "lastActivityLocalAt": last_activity_local_at.isoformat() if last_activity_local_at else None,
                        "lastAccountingLocalAt": last_accounting_local_at.isoformat() if last_accounting_local_at else None,
                        "lastActivitySource": last_activity_source or None,
                        "lastAccountingSource": last_accounting_source or None,
                    },
                    "updatedAt": event.get("receivedAt", dt.datetime.now(dt.UTC)),
                }
            },
            upsert=True,
        )
        return _empty_event_deltas() if suppress_deltas else deltas

    def _normal_microseconds_consumed_for_event(self, event: dict[str, Any]) -> int:
        consumed_microseconds = 0

        for current in self.db.daily_author_activity.find(
            {
                "author": event.get("author") or "Unknown User",
                "date": event.get("date") or "",
            },
            {"_id": 0, "activeSeconds": 1, "idleSeconds": 1, "activeMicroseconds": 1, "idleMicroseconds": 1},
        ):
            consumed_microseconds += _time_microseconds(current, "activeSeconds", "activeMicroseconds")
            consumed_microseconds += _time_microseconds(current, "idleSeconds", "idleMicroseconds")

        return min(DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND, max(0, consumed_microseconds))

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
        overtime_activity_counts = _merge_count_list(
            current.get("overtimeActivityCounts", []), deltas.get("overtimeActivityCountDeltas", []), "type", "count"
        )
        overtime_saved_prefabs = _merge_count_list(
            current.get("overtimeSavedPrefabs", []), deltas.get("overtimeSavedPrefabDeltas", []), "path", "saveCount"
        )
        active_microseconds = _time_microseconds(current, "activeSeconds", "activeMicroseconds") + _time_microseconds(
            deltas, "activeDeltaSeconds", "activeDeltaMicroseconds"
        )
        idle_microseconds = _time_microseconds(current, "idleSeconds", "idleMicroseconds") + _time_microseconds(
            deltas, "idleDeltaSeconds", "idleDeltaMicroseconds"
        )
        overtime_active_microseconds = _time_microseconds(
            current, "overtimeActiveSeconds", "overtimeActiveMicroseconds"
        ) + _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds")

        self.db.daily_author_activity.update_one(
            key,
            {
                "$set": {
                    **key,
                    "authorEmail": snapshot.get("authorEmail", ""),
                    "pluginVersion": snapshot.get("pluginVersion"),
                    "timeZoneId": snapshot.get("timeZoneId"),
                    "timeZoneDisplayName": snapshot.get("timeZoneDisplayName"),
                    "workWindowSeconds": snapshot.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
                    "lastRecordedAt": snapshot.get("recordedAt"),
                    "lastReceivedAt": snapshot.get("receivedAt"),
                    "activityCounts": activity_counts,
                    "savedPrefabs": saved_prefabs,
                    "overtimeActivityCounts": overtime_activity_counts,
                    "overtimeSavedPrefabs": overtime_saved_prefabs,
                    "hourlyActivity": hourly_activity,
                    "activeMicroseconds": active_microseconds,
                    "idleMicroseconds": idle_microseconds,
                    "overtimeActiveMicroseconds": overtime_active_microseconds,
                    "activeSeconds": _seconds_from_microseconds(active_microseconds),
                    "idleSeconds": _seconds_from_microseconds(idle_microseconds),
                    "overtimeActiveSeconds": _seconds_from_microseconds(overtime_active_microseconds),
                },
            },
            upsert=True,
        )


def _new_id() -> str:
    return uuid.uuid4().hex


def _empty_event_deltas() -> dict[str, Any]:
    return {
        "activeDeltaSeconds": 0,
        "idleDeltaSeconds": 0,
        "overtimeActiveDeltaSeconds": 0,
        "activeDeltaMicroseconds": 0,
        "idleDeltaMicroseconds": 0,
        "overtimeActiveDeltaMicroseconds": 0,
        "activityCountDeltas": [],
        "savedPrefabDeltas": [],
        "overtimeActivityCountDeltas": [],
        "overtimeSavedPrefabDeltas": [],
        "hourlyActivityDelta": _empty_hourly_activity(),
    }


def _empty_batch_deltas() -> dict[str, Any]:
    return _empty_event_deltas()


def _merge_batch_deltas(target: dict[str, Any], source: dict[str, Any]) -> None:
    active_microseconds = _time_microseconds(target, "activeDeltaSeconds", "activeDeltaMicroseconds") + _time_microseconds(
        source, "activeDeltaSeconds", "activeDeltaMicroseconds"
    )
    idle_microseconds = _time_microseconds(target, "idleDeltaSeconds", "idleDeltaMicroseconds") + _time_microseconds(
        source, "idleDeltaSeconds", "idleDeltaMicroseconds"
    )
    overtime_active_microseconds = _time_microseconds(
        target, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"
    ) + _time_microseconds(source, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds")
    target["activeDeltaMicroseconds"] = active_microseconds
    target["idleDeltaMicroseconds"] = idle_microseconds
    target["overtimeActiveDeltaMicroseconds"] = overtime_active_microseconds
    target["activeDeltaSeconds"] = _seconds_from_microseconds(active_microseconds)
    target["idleDeltaSeconds"] = _seconds_from_microseconds(idle_microseconds)
    target["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(overtime_active_microseconds)
    _merge_hourly_activity(target["hourlyActivityDelta"], source.get("hourlyActivityDelta", []))
    target["activityCountDeltas"] = _merge_count_list(
        target.get("activityCountDeltas", []), source.get("activityCountDeltas", []), "type", "count"
    )
    target["savedPrefabDeltas"] = _merge_count_list(
        target.get("savedPrefabDeltas", []), source.get("savedPrefabDeltas", []), "path", "saveCount"
    )
    target["overtimeActivityCountDeltas"] = _merge_count_list(
        target.get("overtimeActivityCountDeltas", []), source.get("overtimeActivityCountDeltas", []), "type", "count"
    )
    target["overtimeSavedPrefabDeltas"] = _merge_count_list(
        target.get("overtimeSavedPrefabDeltas", []), source.get("overtimeSavedPrefabDeltas", []), "path", "saveCount"
    )


def _has_time_delta(deltas: dict[str, Any]) -> bool:
    return (
        _time_microseconds(deltas, "activeDeltaSeconds", "activeDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "idleDeltaSeconds", "idleDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0
    )


def _normalize_raw_event(
    raw_event: dict[str, Any],
    source: str,
    plugin_version: str,
    author: str,
    author_email: str,
    project_id: str,
    session_id: str,
    device_id: str,
    batch_id: str,
    raw_report_id: Any,
    received_at: dt.datetime,
    report_type: str,
    time_zone_id: Any,
    time_zone_display_name: Any,
) -> dict[str, Any] | None:
    event_type = str(raw_event.get("eventType") or "").strip()

    if not event_type:
        return None

    occurred_at = _coerce_datetime(raw_event.get("occurredAtUtc") or raw_event.get("occurredAtLocal"))

    if not occurred_at:
        occurred_at = received_at

    occurred_local = str(raw_event.get("occurredAtLocal") or occurred_at.isoformat())
    event_id = str(raw_event.get("eventId") or _new_id())
    date = str(raw_event.get("date") or occurred_local[:10] or occurred_at.date().isoformat())
    return {
        "eventId": event_id,
        "eventType": event_type,
        "source": source,
        "pluginVersion": plugin_version,
        "author": author,
        "authorEmail": author_email,
        "projectId": project_id,
        "sessionId": session_id,
        "deviceId": device_id,
        "batchId": batch_id,
        "rawReportId": raw_report_id,
        "reportType": report_type,
        "occurredAtUtc": occurred_at,
        "occurredAtLocal": occurred_local,
        "date": date,
        "metadata": raw_event.get("metadata") or {},
        "timeZoneId": time_zone_id,
        "timeZoneDisplayName": time_zone_display_name,
        "receivedAt": received_at,
    }


def _raw_event_session_key(event: dict[str, Any]) -> str:
    return "|".join(
        [
            "author_day_v1",
            str(event.get("author") or "Unknown User"),
            str(event.get("date") or ""),
        ]
    )


def _is_activity_event(event: dict[str, Any] | str) -> bool:
    if isinstance(event, dict):
        event_type = str(event.get("eventType") or "")

        if event.get("source") == "bal" and event_type == "scene_changed":
            metadata = event.get("metadata") or {}
            return bool(metadata.get("inputType") or metadata.get("changeType") == "object_update")
    else:
        event_type = event

    return event_type in RAW_ACTIVITY_EVENT_TYPES and event_type not in NON_ACTIVITY_EVENT_TYPES


def _activity_count_type(event_type: str) -> str:
    if event_type == "selection":
        return "select"

    return event_type


def _saved_prefab_delta(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("eventType") or "")

    if event_type not in {"prefab_saved", "asset_saved", "file_saved"}:
        return None

    metadata = event.get("metadata") or {}
    path = str(metadata.get("path") or "")

    if not path:
        return None

    lower_path = path.lower()

    if event_type in {"prefab_saved", "asset_saved"} and not lower_path.endswith(".prefab"):
        return None

    if event_type == "file_saved" and event.get("source") == "fch":
        if "figma.com/" not in lower_path and not metadata.get("fileKey"):
            return None
    elif event_type == "file_saved" and event.get("source") == "vsc":
        pass
    elif event_type == "file_saved" and not lower_path.endswith(".blend"):
        return None

    name = str(metadata.get("name") or path.rsplit("/", 1)[-1])
    return {"path": path, "name": name, "saveCount": 1}


def _is_overtime_event_delta(consumed_normal_microseconds: int, deltas: dict[str, Any]) -> bool:
    work_window_microseconds = DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND
    return (
        consumed_normal_microseconds >= work_window_microseconds
        or _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0
    )


def _interval_deltas(
    start: dt.datetime,
    end: dt.datetime,
    local_start: dt.datetime,
    local_end: dt.datetime,
    is_active: bool,
    consumed_normal_microseconds: int,
) -> dict[str, Any]:
    deltas = _empty_event_deltas()

    if end <= start:
        return deltas

    interval_microseconds = _duration_microseconds(start, end)
    remaining_normal_microseconds = max(
        0,
        (DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND) - consumed_normal_microseconds,
    )
    normal_microseconds = min(interval_microseconds, remaining_normal_microseconds)

    if normal_microseconds > 0:
        local_normal_end = local_start + dt.timedelta(microseconds=normal_microseconds)

        if is_active:
            deltas["activeDeltaMicroseconds"] += normal_microseconds
            deltas["activeDeltaSeconds"] = _seconds_from_microseconds(deltas["activeDeltaMicroseconds"])
            _add_interval_to_hourly(deltas["hourlyActivityDelta"], local_start, local_normal_end, "active")
        else:
            deltas["idleDeltaMicroseconds"] += normal_microseconds
            deltas["idleDeltaSeconds"] = _seconds_from_microseconds(deltas["idleDeltaMicroseconds"])
            _add_interval_to_hourly(deltas["hourlyActivityDelta"], local_start, local_normal_end, "idle")

    if is_active and interval_microseconds > normal_microseconds:
        local_overtime_start = local_start + dt.timedelta(microseconds=normal_microseconds)
        overtime_microseconds = interval_microseconds - normal_microseconds
        deltas["overtimeActiveDeltaMicroseconds"] += overtime_microseconds
        deltas["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(deltas["overtimeActiveDeltaMicroseconds"])
        _add_interval_to_hourly(deltas["hourlyActivityDelta"], local_overtime_start, local_end, "overtime")

    return deltas


def _add_interval_to_hourly(target: list[dict[str, Any]], start: dt.datetime, end: dt.datetime, bucket: str) -> None:
    target_by_hour = {int(item.get("hour", 0)): item for item in target}
    cursor = start

    while cursor < end:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, end)
        microseconds = _duration_microseconds(cursor, segment_end)
        item = target_by_hour.get(cursor.hour)

        if item:
            if bucket == "active":
                active_microseconds = _time_microseconds(item, "activeSeconds", "activeMicroseconds") + microseconds
                item["activeMicroseconds"] = active_microseconds
                item["activeSeconds"] = _seconds_from_microseconds(active_microseconds)
            elif bucket == "idle":
                idle_microseconds = _time_microseconds(item, "idleSeconds", "idleMicroseconds") + microseconds
                item["idleMicroseconds"] = idle_microseconds
                item["idleSeconds"] = _seconds_from_microseconds(idle_microseconds)
            elif bucket == "overtime":
                overtime_active_microseconds = (
                    _time_microseconds(item, "overtimeActiveSeconds", "overtimeActiveMicroseconds") + microseconds
                )
                item["overtimeActiveMicroseconds"] = overtime_active_microseconds
                item["overtimeActiveSeconds"] = _seconds_from_microseconds(overtime_active_microseconds)

        cursor = segment_end


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


def _report_date_query(
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
    profiles: dict[str, dict[str, Any]],
    now: dt.datetime,
) -> dict[str, Any]:
    if date_mode != "authorLocalToday":
        return _date_query(start_date, end_date)

    dates = {now.astimezone(dt.UTC).date().isoformat()}

    for profile in profiles.values():
        dates.add(_local_date_for_time_zone(now, _author_time_zone_id(profile.get("rawAuthor"), profiles)))

    return {"date": {"$in": sorted(dates)}}


def _document_identity_query(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("_id") is not None:
        return {"_id": item["_id"]}

    return dict(item)


def _is_author_local_today(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
) -> bool:
    if not value:
        return False

    return str(value) == _local_date_for_time_zone(
        now, _author_time_zone_id(raw_author, profiles, fallback_time_zone_id)
    )


def _date_in_summary_scope(
    value: str,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
) -> bool:
    if date_mode == "authorLocalToday":
        return _is_author_local_today(value, raw_author, profiles, fallback_time_zone_id, now)

    return _date_in_range(value, start_date, end_date)


def _author_time_zone_id(
    raw_author: Any, profiles: dict[str, dict[str, Any]], fallback_time_zone_id: Any = None
) -> str:
    profile = profiles.get(str(raw_author or ""))
    profile_time_zone = _valid_time_zone_id((profile or {}).get("timeZoneId"))

    if profile_time_zone:
        return profile_time_zone

    return _valid_time_zone_id(fallback_time_zone_id) or "UTC"


def _local_date_for_time_zone(value: dt.datetime, time_zone_id: str) -> str:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return value.astimezone(zone).date().isoformat()


def _date_in_range(value: str, start_date: str | None, end_date: str | None) -> bool:
    if start_date and value < start_date:
        return False

    if end_date and value > end_date:
        return False

    return True


def _display_name(raw_author: Any, profile: dict[str, Any]) -> str:
    return str(profile.get("displayName") or raw_author or "Unknown User")


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _public_site_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": user.get("email", ""),
        "displayName": user.get("displayName") or user.get("email", ""),
        "role": user.get("role", "viewer"),
        "active": user.get("active", True),
    }


def _with_productivity(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    active_seconds = int(item.get("activeSeconds", 0))
    idle_seconds = int(item.get("idleSeconds", 0))
    break_seconds = int(item.get("breakSeconds", 0))
    overtime_seconds = int(item.get("overtimeActiveSeconds", 0))
    item["productivity"] = round(_productivity(active_seconds, idle_seconds, break_seconds, overtime_seconds), 2)
    return item


def _with_activity_mix(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    item["activityMix"] = _activity_mix_from_list(item.get("activityCounts", []))
    item["overtimeActivityMix"] = _activity_mix_from_list(item.get("overtimeActivityCounts", []))
    return item


def _activity_mix_from_counts(activity_counts: dict[str, int]) -> list[dict[str, Any]]:
    return _activity_mix_from_list(
        [{"type": activity_type, "count": count} for activity_type, count in activity_counts.items()]
    )


def _activity_mix_from_list(activity_counts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total_activities = sum(int(count.get("count", 0)) for count in activity_counts)
    return [
        {
            "type": count.get("type"),
            "count": int(count.get("count", 0)),
            "percent": round((int(count.get("count", 0)) / total_activities) * 100) if total_activities else 0,
        }
        for count in activity_counts
        if count.get("type")
    ]


def _with_alerts(
    author: dict[str, Any],
    send_interval_seconds: int,
    now: dt.datetime,
    presence_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(author)
    alerts = list(item.get("securityAlerts") or [])
    alerts.extend(item.get("telegramAlerts") or [])
    last_received_at = _coerce_datetime(item.get("lastReceivedAt"))
    stale_threshold_seconds = max(0, send_interval_seconds * 2)
    forced_offline = False

    if last_received_at:
        seconds_since_report = max(0, int((now - last_received_at).total_seconds()))

        if seconds_since_report > stale_threshold_seconds:
            alerts.append(
                {
                    "id": f"reports_stopped:{item.get('rawAuthor') or item.get('displayName') or 'unknown'}",
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
                "id": f"reports_stopped:{item.get('rawAuthor') or item.get('displayName') or 'unknown'}",
                "type": "reports_stopped",
                "severity": "critical",
                "title": "Reports stopped",
                "message": "No Unity reports have been received for this author.",
                "value": None,
                "threshold": stale_threshold_seconds,
            }
        )

    if presence_override and presence_override.get("offlineAt"):
        overtime_received_at = _coerce_datetime(presence_override.get("overtimeReceivedAt"))
        forced_offline = True

        if overtime_received_at:
            seconds_since_overtime = max(0, int((now - overtime_received_at).total_seconds()))
            forced_offline = seconds_since_overtime > stale_threshold_seconds

    productivity = float(item.get("productivity", 0))
    plugin_day_seconds = int(item.get("pluginDaySeconds", 0))

    if plugin_day_seconds > 0 and productivity < LOW_PRODUCTIVITY_THRESHOLD:
        alerts.append(
            {
                "id": f"low_productivity:{item.get('rawAuthor') or item.get('displayName') or 'unknown'}",
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
                "id": f"long_break:{item.get('rawAuthor') or item.get('displayName') or 'unknown'}",
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
                "id": f"select_heavy_activity:{item.get('rawAuthor') or item.get('displayName') or 'unknown'}",
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
    has_reports_stopped = any(alert["type"] == "reports_stopped" for alert in alerts)
    is_stale = forced_offline or has_reports_stopped
    item["status"] = "stale" if is_stale else "online"

    if is_stale:
        if has_reports_stopped and forced_offline:
            item["stalePresence"] = "both"
        elif has_reports_stopped:
            item["stalePresence"] = "reports"
        else:
            item["stalePresence"] = "telegram"

    item["alerts"] = alerts
    item["alertStats"] = {
        "total": len(alerts),
        "critical": critical_count,
        "warning": warning_count,
    }
    item["sendIntervalSeconds"] = send_interval_seconds
    item["staleThresholdSeconds"] = stale_threshold_seconds
    return item


def _author_color(raw_author: Any) -> str:
    value = _normalize_author(raw_author)
    index = sum(ord(char) for char in value) % len(AUTHOR_COLORS)
    return AUTHOR_COLORS[index]


def _normalize_author(value: Any) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).strip()
    return normalized or "Unknown User"


def _author_configured_time_zone_id(raw_author: str) -> str | None:
    return _valid_time_zone_id(AUTHOR_TIME_ZONE_IDS.get(raw_author))


def _valid_time_zone_id(value: Any) -> str | None:
    normalized = str(value or "").strip()

    if not normalized:
        return None

    normalized = WINDOWS_TIME_ZONE_IDS.get(normalized, normalized)

    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return None

    return normalized


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


def _analytics_year_months(docs: list[dict[str, Any]], year: int) -> list[dict[str, Any]]:
    docs_by_date = {str(item.get("date") or ""): item for item in docs if item.get("date")}
    months = []
    today = dt.date.today()
    if year < today.year:
        last_month = 12
    elif year == today.year:
        last_month = today.month
    else:
        last_month = 0

    for month in range(last_month, 0, -1):
        month_start = dt.date(year, month, 1)
        if month == 12:
            month_end = dt.date(year, 12, 31)
        else:
            month_end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

        month_docs = _docs_for_range(docs_by_date, month_start, month_end)
        previous_month_end = month_start - dt.timedelta(days=1)
        previous_month_start = previous_month_end.replace(day=1)
        previous_month_docs = _docs_for_range(docs_by_date, previous_month_start, previous_month_end)
        weeks = _analytics_month_weeks(docs_by_date, month_start, month_end)
        totals = _analytics_totals(month_docs)
        previous = _analytics_totals(previous_month_docs)
        months.append(
            {
                "month": month,
                "label": month_start.strftime("%B"),
                "startDate": month_start.isoformat(),
                "endDate": month_end.isoformat(),
                "totals": totals,
                "previousMonthDeltas": _analytics_deltas(totals, previous),
                "weeks": weeks,
            }
        )

    return months


def _analytics_month_weeks(
    docs_by_date: dict[str, dict[str, Any]], month_start: dt.date, month_end: dt.date
) -> list[dict[str, Any]]:
    weeks = []
    cursor = month_start - dt.timedelta(days=month_start.weekday())

    while cursor <= month_end:
        week_start = cursor
        week_end = week_start + dt.timedelta(days=6)
        days = []
        week_docs = []

        for offset in range(7):
            day = week_start + dt.timedelta(days=offset)
            doc = docs_by_date.get(day.isoformat())
            day_totals = _analytics_totals([doc] if doc else [])
            days.append(
                {
                    "date": day.isoformat(),
                    "label": day.strftime("%a %d"),
                    "inMonth": month_start <= day <= month_end,
                    "totals": day_totals,
                }
            )

            if month_start <= day <= month_end and doc:
                week_docs.append(doc)

        previous_week_start = week_start - dt.timedelta(days=7)
        previous_week_end = week_start - dt.timedelta(days=1)
        previous_week_docs = _docs_for_range(docs_by_date, previous_week_start, previous_week_end)
        totals = _analytics_totals(week_docs)
        previous = _analytics_totals(previous_week_docs)
        weeks.append(
            {
                "week": len(weeks) + 1,
                "label": f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}",
                "startDate": week_start.isoformat(),
                "endDate": week_end.isoformat(),
                "totals": totals,
                "previousWeekDeltas": _analytics_deltas(totals, previous),
                "days": days,
            }
        )
        cursor += dt.timedelta(days=7)

    return weeks


def _docs_for_range(docs_by_date: dict[str, dict[str, Any]], start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    docs = []
    cursor = start

    while cursor <= end:
        doc = docs_by_date.get(cursor.isoformat())

        if doc:
            docs.append(doc)

        cursor += dt.timedelta(days=1)

    return docs


def _analytics_totals(docs: list[dict[str, Any]]) -> dict[str, Any]:
    active_seconds = sum(int(item.get("activeSeconds", 0)) for item in docs)
    idle_seconds = sum(int(item.get("idleSeconds", 0)) for item in docs)
    break_seconds = sum(int(item.get("breakSeconds", 0)) for item in docs)
    overtime_active_seconds = sum(int(item.get("overtimeActiveSeconds", 0)) for item in docs)
    telegram_day_seconds = sum(int(item.get("daySeconds", 0)) for item in docs)
    plugin_day_seconds = sum(_plugin_day_seconds(item) for item in docs)
    productivity = _productivity(active_seconds, idle_seconds, break_seconds, overtime_active_seconds)
    return {
        "daySeconds": telegram_day_seconds,
        "activeSeconds": active_seconds,
        "idleSeconds": idle_seconds,
        "breakSeconds": break_seconds,
        "overtimeActiveSeconds": overtime_active_seconds,
        "telegramDaySeconds": telegram_day_seconds,
        "pluginDaySeconds": plugin_day_seconds,
        "productivity": round(productivity, 2),
    }


def _analytics_deltas(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "activeSeconds": current["activeSeconds"] - previous["activeSeconds"],
        "idleSeconds": current["idleSeconds"] - previous["idleSeconds"],
        "breakSeconds": current["breakSeconds"] - previous["breakSeconds"],
        "overtimeActiveSeconds": current["overtimeActiveSeconds"] - previous["overtimeActiveSeconds"],
        "telegramDaySeconds": current["telegramDaySeconds"] - previous["telegramDaySeconds"],
        "pluginDaySeconds": current["pluginDaySeconds"] - previous["pluginDaySeconds"],
        "productivity": round(float(current["productivity"]) - float(previous["productivity"]), 2),
    }


def _productivity(
    active_seconds: int, idle_seconds: int, break_seconds: int, overtime_seconds: int = 0
) -> float:
    penalized_break_seconds = max(0, break_seconds - LONG_BREAK_THRESHOLD_SECONDS)
    denominator = active_seconds + idle_seconds + penalized_break_seconds
    numerator = active_seconds + overtime_seconds
    return (numerator / denominator) * 100 if denominator else 0


def _plugin_day_seconds(item: dict[str, Any], active_seconds: int | None = None, idle_seconds: int | None = None) -> int:
    active = int(item.get("activeSeconds", 0) if active_seconds is None else active_seconds)
    idle = int(item.get("idleSeconds", 0) if idle_seconds is None else idle_seconds)
    work_window_seconds = int(item.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
    return min(max(0, work_window_seconds), max(0, active + idle))


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


def _normalize_discord_user_id(value: str | None) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.UTC)

    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)

    return parsed.astimezone(dt.UTC)


def _telegram_event_date(event_time: dt.datetime, time_zone_id: str) -> str:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return event_time.astimezone(zone).date().isoformat()


def _parse_local_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value

    if isinstance(value, str) and value:
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return None


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
    active_delta = _delta(snapshot.get("activeSeconds"), previous.get("activeSeconds"))
    idle_delta = _delta(snapshot.get("idleSeconds"), previous.get("idleSeconds"))
    overtime_delta = _delta(snapshot.get("overtimeActiveSeconds"), previous.get("overtimeActiveSeconds"))
    work_window_seconds = int(snapshot.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
    consumed_normal_seconds = min(
        work_window_seconds,
        max(0, int(previous.get("activeSeconds", 0)) + int(previous.get("idleSeconds", 0))),
    )
    remaining_normal_seconds = max(0, work_window_seconds - consumed_normal_seconds)
    normal_active_delta = min(active_delta, remaining_normal_seconds)
    overtime_delta += max(0, active_delta - normal_active_delta)
    remaining_normal_seconds = max(0, remaining_normal_seconds - normal_active_delta)
    normal_idle_delta = min(idle_delta, remaining_normal_seconds)

    return {
        "activeDeltaSeconds": normal_active_delta,
        "idleDeltaSeconds": normal_idle_delta,
        "overtimeActiveDeltaSeconds": overtime_delta,
        "activityCountDeltas": _count_deltas(snapshot.get("activityCounts", []), previous.get("activityCounts", []), "type", "count"),
        "savedPrefabDeltas": _count_deltas(snapshot.get("savedPrefabs", []), previous.get("savedPrefabs", []), "path", "saveCount"),
        "hourlyActivityDelta": _hourly_deltas(snapshot.get("hourlyActivity", []), previous.get("hourlyActivity", [])),
    }


def _delta(current: Any, previous: Any) -> int:
    return max(0, int(current or 0) - int(previous or 0))


def _duration_microseconds(start: dt.datetime, end: dt.datetime) -> int:
    delta = end - start

    return max(
        0,
        ((delta.days * 24 * 60 * 60) + delta.seconds) * MICROSECONDS_PER_SECOND + delta.microseconds,
    )


def _seconds_from_microseconds(value: Any) -> int:
    microseconds = max(0, int(value or 0))

    return int((microseconds + (MICROSECONDS_PER_SECOND // 2)) // MICROSECONDS_PER_SECOND)


def _time_microseconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    if microseconds_key in item:
        return max(0, int(item.get(microseconds_key) or 0))

    return max(0, int(item.get(seconds_key) or 0)) * MICROSECONDS_PER_SECOND


def _time_seconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    if microseconds_key in item:
        return _seconds_from_microseconds(item.get(microseconds_key))

    return max(0, int(item.get(seconds_key) or 0))


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
    return [
        {"hour": hour, "activeSeconds": 0, "idleSeconds": 0, "breakSeconds": 0, "meetingSeconds": 0, "overtimeActiveSeconds": 0}
        for hour in range(24)
    ]


def _public_hourly_activity(source: list[dict[str, Any]]) -> list[dict[str, int]]:
    return [
        {
            "hour": int(item.get("hour", 0)),
            "activeSeconds": int(item.get("activeSeconds", 0)),
            "idleSeconds": int(item.get("idleSeconds", 0)),
            "breakSeconds": int(item.get("breakSeconds", 0)),
            "meetingSeconds": int(item.get("meetingSeconds", 0)),
            "overtimeActiveSeconds": int(item.get("overtimeActiveSeconds", 0)),
        }
        for item in source
    ]


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

        active_microseconds = _time_microseconds(target_item, "activeSeconds", "activeMicroseconds") + _time_microseconds(
            delta_item, "activeSeconds", "activeMicroseconds"
        )
        idle_microseconds = _time_microseconds(target_item, "idleSeconds", "idleMicroseconds") + _time_microseconds(
            delta_item, "idleSeconds", "idleMicroseconds"
        )
        overtime_active_microseconds = _time_microseconds(
            target_item, "overtimeActiveSeconds", "overtimeActiveMicroseconds"
        ) + _time_microseconds(delta_item, "overtimeActiveSeconds", "overtimeActiveMicroseconds")
        target_item["activeMicroseconds"] = active_microseconds
        target_item["idleMicroseconds"] = idle_microseconds
        target_item["overtimeActiveMicroseconds"] = overtime_active_microseconds
        target_item["activeSeconds"] = _seconds_from_microseconds(active_microseconds)
        target_item["idleSeconds"] = _seconds_from_microseconds(idle_microseconds)
        target_item["breakSeconds"] = int(target_item.get("breakSeconds", 0)) + int(delta_item.get("breakSeconds", 0))
        target_item["meetingSeconds"] = int(target_item.get("meetingSeconds", 0)) + int(delta_item.get("meetingSeconds", 0))
        target_item["overtimeActiveSeconds"] = _seconds_from_microseconds(overtime_active_microseconds)


def _apply_breaks_to_hourly_activity(
    source: list[dict[str, Any]],
    break_buckets: list[dict[str, Any]],
) -> list[dict[str, int]]:
    source_by_hour = {int(item.get("hour", 0)): item for item in source}
    breaks_by_hour = {int(item.get("hour", 0)): item for item in break_buckets}
    hourly_activity = []

    for hour in range(24):
        source_hour = source_by_hour.get(hour, {})
        break_hour = breaks_by_hour.get(hour, {})
        active_seconds = min(3600, _time_seconds(source_hour, "activeSeconds", "activeMicroseconds"))
        overtime_active_seconds = min(3600, _time_seconds(source_hour, "overtimeActiveSeconds", "overtimeActiveMicroseconds"))
        raw_idle_seconds = _time_seconds(source_hour, "idleSeconds", "idleMicroseconds")
        requested_break_seconds = max(0, int(break_hour.get("breakSeconds", 0)))
        break_seconds = min(requested_break_seconds, max(0, 3600 - active_seconds - overtime_active_seconds))
        idle_seconds = max(0, raw_idle_seconds - break_seconds)
        idle_seconds = min(idle_seconds, max(0, 3600 - active_seconds - overtime_active_seconds - break_seconds))

        if break_seconds and idle_seconds < AFK_IDLE_ARTIFACT_THRESHOLD_SECONDS:
            break_seconds = min(
                break_seconds + idle_seconds,
                max(0, 3600 - active_seconds - overtime_active_seconds),
            )
            idle_seconds = 0

        hourly_activity.append(
            {
                "hour": hour,
                "activeSeconds": active_seconds,
                "idleSeconds": idle_seconds,
                "breakSeconds": break_seconds,
                "meetingSeconds": int(source_hour.get("meetingSeconds", 0)),
                "overtimeActiveSeconds": overtime_active_seconds,
            }
        )

    return hourly_activity


def _apply_meetings_to_hourly_activity(
    source: list[dict[str, Any]],
    meeting_buckets: list[dict[str, Any]],
    consumed_buckets: list[dict[str, Any]],
) -> list[dict[str, int]]:
    source_by_hour = {int(item.get("hour", 0)): item for item in source}
    meeting_by_hour = {int(item.get("hour", 0)): item for item in meeting_buckets}
    consumed_by_hour = {int(item.get("hour", 0)): item for item in consumed_buckets}
    hourly_activity = []

    for hour in range(24):
        source_hour = source_by_hour.get(hour, {})
        consumed_hour = consumed_by_hour.get(hour, {})
        active_seconds = int(source_hour.get("activeSeconds", 0))
        overtime_active_seconds = int(source_hour.get("overtimeActiveSeconds", 0))
        break_seconds = int(source_hour.get("breakSeconds", 0))
        idle_seconds = int(source_hour.get("idleSeconds", 0))
        requested_meeting_seconds = max(0, int((meeting_by_hour.get(hour, {}) or {}).get("meetingSeconds", 0)))
        consumed_meeting_seconds = max(0, int(consumed_hour.get("meetingSeconds", 0)))
        available_meeting_seconds = max(0, requested_meeting_seconds - consumed_meeting_seconds)
        available_hour_seconds = max(0, 3600 - active_seconds - overtime_active_seconds - break_seconds)
        meeting_seconds = min(available_meeting_seconds, available_hour_seconds)
        idle_seconds = max(0, min(idle_seconds, available_hour_seconds) - meeting_seconds)
        consumed_hour["meetingSeconds"] = consumed_meeting_seconds + meeting_seconds

        hourly_activity.append(
            {
                "hour": hour,
                "activeSeconds": active_seconds,
                "idleSeconds": idle_seconds,
                "breakSeconds": break_seconds,
                "meetingSeconds": meeting_seconds,
                "overtimeActiveSeconds": overtime_active_seconds,
            }
        )

    return hourly_activity


def _merge_meeting_buckets_into_hourly_author_rows(
    hourly_by_author: dict[str, dict[str, Any]],
    meeting_buckets: dict[tuple[str, str], list[dict[str, int]]],
    profiles: dict[str, dict[str, Any]],
) -> None:
    for (raw_author, _date), meeting_hours in meeting_buckets.items():
        author_row = hourly_by_author.get(raw_author)

        if not author_row:
            profile = profiles.get(raw_author, {})
            author_row = {
                "author": _display_name(raw_author, profile),
                "rawAuthor": raw_author,
                "timeZoneId": profile.get("timeZoneId"),
                "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                "hourlyActivity": _empty_hourly_activity(),
            }
            hourly_by_author[raw_author] = author_row

        current_by_hour = {int(item.get("hour", 0)): item for item in author_row.get("hourlyActivity", [])}

        for meeting_hour in meeting_hours:
            hour = int(meeting_hour.get("hour", 0))
            target_hour = current_by_hour.get(hour)

            if not target_hour:
                continue

            meeting_seconds = int(meeting_hour.get("meetingSeconds", 0))
            current_meeting_seconds = int(target_hour.get("meetingSeconds", 0))

            if meeting_seconds > current_meeting_seconds:
                target_hour["meetingSeconds"] = meeting_seconds


def _add_break_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, int]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    if not raw_author or not started_at or not ended_at or ended_at <= started_at:
        return

    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    current = started_at.astimezone(zone)
    local_end = ended_at.astimezone(zone)

    while current < local_end:
        hour_end = current.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, local_end)
        date = current.date().isoformat()
        key = (str(raw_author), date)
        target = buckets.get(key)

        if target:
            seconds = max(0, int((segment_end - current).total_seconds()))
            target[current.hour]["breakSeconds"] = int(target[current.hour].get("breakSeconds", 0)) + seconds

        current = segment_end


def _add_idle_interval_to_buckets(
    buckets: list[dict[str, int]],
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    if not started_at or not ended_at or ended_at <= started_at:
        return

    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    current = started_at.astimezone(zone)
    local_end = ended_at.astimezone(zone)

    while current < local_end:
        hour_end = current.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, local_end)
        seconds = max(0, int((segment_end - current).total_seconds()))

        if 0 <= current.hour < len(buckets):
            buckets[current.hour]["idleSeconds"] = int(buckets[current.hour].get("idleSeconds", 0)) + seconds

        current = segment_end


def _add_meeting_interval_to_buckets(
    buckets: dict[tuple[str, str], list[dict[str, int]]],
    raw_author: Any,
    started_at: dt.datetime | None,
    ended_at: dt.datetime | None,
    time_zone_id: str,
) -> None:
    if not raw_author or not started_at or not ended_at or ended_at <= started_at:
        return

    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    current = started_at.astimezone(zone)
    local_end = ended_at.astimezone(zone)

    while current < local_end:
        hour_end = current.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        segment_end = min(hour_end, local_end)
        date = current.date().isoformat()
        key = (str(raw_author), date)
        target = buckets.get(key)

        if target:
            seconds = max(0, int((segment_end - current).total_seconds()))
            target[current.hour]["meetingSeconds"] = int(target[current.hour].get("meetingSeconds", 0)) + seconds

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
