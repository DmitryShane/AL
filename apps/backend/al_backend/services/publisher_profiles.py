from __future__ import annotations

import datetime as dt
import re
from copy import deepcopy
from typing import Any

from ..activity_math import _author_color, _cached_author_avatar_api_url, _coerce_datetime, _display_name, _iso, _normalize_author, _valid_color
from ..author_avatar_cache import author_avatar_cache_file_path
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin
from ..hourly_fill_rules import merge_hourly_activity

PUBLISHER_PROFILE_TYPE = "publisher"
PUBLISHER_AVATAR_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
PUBLISHER_AVATAR_MAX_BYTES = 8_000_000


class PublisherProfileService(MongoComposableMixin):
    def is_publisher_member(self, raw_author: str) -> bool:
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"publisherRawAuthor": 1}) or {}
        return bool(profile.get("publisherRawAuthor"))

    def resolve_activity_author(self, raw_author: str | None) -> str:
        """Group display activity without changing the identity stored in raw data."""
        author = composed(self).resolve_author_alias(raw_author)
        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"publisherRawAuthor": 1}) or {}
        return str(profile.get("publisherRawAuthor") or author)

    def activity_author_keys(self, raw_author: str | None) -> list[str]:
        author = self.resolve_activity_author(raw_author)
        members = {author}
        members.update(str(p["rawAuthor"]) for p in self.db.author_profiles.find({"publisherRawAuthor": author}, {"rawAuthor": 1}))
        keys = set(members)
        for member in members:
            keys.update(composed(self).author_alias_keys(member))
        return sorted(keys)

    def group_activity_buckets(self, buckets: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        result = {}
        for (author, date), hours in buckets.items():
            key = (self.resolve_activity_author(author), date)
            if key not in result:
                result[key] = deepcopy(hours)
            else:
                merge_hourly_activity(result[key], hours)
        return result

    def publisher_author_candidates(self) -> list[dict[str, Any]]:
        return [p for p in composed(self).author_profiles() if p.get("profileType") != PUBLISHER_PROFILE_TYPE]

    def set_publisher_author(self, publisher: str, author: str, linked: bool, *, apply: bool = True) -> dict[str, Any]:
        publisher, author = _normalize_author(publisher), _normalize_author(author)
        target = self.db.author_profiles.find_one({"rawAuthor": publisher, "profileType": PUBLISHER_PROFILE_TYPE})
        profile = self.db.author_profiles.find_one({"rawAuthor": author}) or {}
        if not target or publisher == author or composed(self).resolve_author_alias(publisher) != publisher:
            return {"ok": False, "error": "Publisher profile does not exist or is the source author"}
        if (profile.get("profileType") == PUBLISHER_PROFILE_TYPE
                or re.fullmatch(r"Device\d+", author)
                or self.db.device_report_identities.find_one({"rawAuthor": author})
                or composed(self).resolve_author_alias(author) != author
                or author not in composed(self).list_authors()):
            return {"ok": False, "error": "Select an existing person profile"}
        current = profile.get("publisherRawAuthor")
        if current and current != publisher:
            return {"ok": False, "error": "Unlink the author from their current publisher first"}
        keys = set(self.activity_author_keys(publisher)) | set(composed(self).author_alias_keys(author))
        dates = set()
        for collection, field in ((self.db.daily_author_activity, "author"), (self.db.report_rows, "author"), (self.db.raw_activity_events, "author"), (self.db.activity_snapshots, "author"), (self.db.break_events, "rawAuthor"), (self.db.meeting_events, "rawAuthor"), (self.db.status_events, "rawAuthor")):
            dates.update(str(d) for d in collection.distinct("date", {field: {"$in": sorted(keys)}}) if d)
        if not apply:
            return {"ok": True, "authors": sorted(keys), "dates": sorted(dates)}
        update: dict[str, Any] = {"$set": {"rawAuthor": author, "updatedAt": dt.datetime.now(dt.UTC)}}
        if linked:
            update["$set"]["publisherRawAuthor"] = publisher
        else:
            update["$unset"] = {"publisherRawAuthor": ""}
        self.db.author_profiles.update_one({"rawAuthor": author}, update, upsert=True)
        composed(self).invalidate_activity_summary_cache(sorted(dates), sorted(keys))
        return {"ok": True, "authors": sorted(keys), "dates": sorted(dates)}

    def publisher_profiles(self) -> list[dict[str, Any]]:
        device_profiles = {item["rawDevice"]: item for item in composed(self).device_profiles()}
        devices_by_author: dict[str, list[dict[str, Any]]] = {}

        for alias in self.db.author_aliases.find({}, {"_id": 0, "sourceRawAuthor": 1, "targetRawAuthor": 1}):
            source = str(alias.get("sourceRawAuthor") or "")
            target = str(alias.get("targetRawAuthor") or "")

            if source in device_profiles and target:
                devices_by_author.setdefault(target, []).append(device_profiles[source])

        result = []

        for profile in self.db.author_profiles.find({"profileType": PUBLISHER_PROFILE_TYPE}, {"_id": 0}):
            raw_author = str(profile.get("rawAuthor") or "")
            if not raw_author:
                continue

            result.append(
                {
                    "rawAuthor": raw_author,
                    "displayName": _display_name(raw_author, profile),
                    "team": str(profile.get("team") or ""),
                    "profileType": PUBLISHER_PROFILE_TYPE,
                    "authorColor": str(profile.get("authorColor") or _author_color(raw_author)),
                    "avatarUrl": _cached_author_avatar_api_url(raw_author, "", profile),
                    "authors": [{"rawAuthor": p["rawAuthor"], "displayName": _display_name(p["rawAuthor"], p)}
                                for p in self.db.author_profiles.find({"publisherRawAuthor": raw_author}, {"_id": 0})],
                    "devices": sorted(devices_by_author.get(raw_author, []), key=lambda item: str(item.get("rawDevice") or "")),
                }
            )

        return sorted(result, key=lambda item: item["displayName"].lower())

    def upsert_publisher_profile(self, raw_author: str, display_name: str, team: str | None, author_color: str | None) -> dict[str, Any]:
        raw_author = _normalize_author(raw_author)
        display_name = str(display_name or "").strip()

        if not raw_author or not display_name:
            return {"ok": False, "error": "Publisher profile and display name are required"}

        now = dt.datetime.now(dt.UTC)
        color = _valid_color(author_color) or _author_color(raw_author)
        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {
                "$set": {
                    "rawAuthor": raw_author,
                    "displayName": display_name,
                    "team": str(team or "").strip(),
                    "authorColor": color,
                    "profileType": PUBLISHER_PROFILE_TYPE,
                    "pluginEnabled": True,
                    "updatedAt": now,
                },
                "$unset": {
                    "authorEmail": "",
                    "telegramUsername": "",
                    "telegramPrivateChatId": "",
                    "discordUserId": "",
                    "discordUsername": "",
                    "githubUsername": "",
                },
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "profile": self._publisher_profile(raw_author)}

    def save_publisher_avatar(self, raw_author: str, body: bytes, mime_type: str) -> dict[str, Any]:
        raw_author = _normalize_author(raw_author)
        mime_type = str(mime_type or "").split(";")[0].strip().lower()

        if not raw_author:
            return {"ok": False, "error": "Publisher profile is required"}

        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author, "profileType": PUBLISHER_PROFILE_TYPE}, {"_id": 0})
        if not profile:
            return {"ok": False, "error": "Publisher profile does not exist"}

        if mime_type not in PUBLISHER_AVATAR_MIME_TYPES:
            return {"ok": False, "error": "Upload a PNG, JPEG, or WebP image"}

        if not body or len(body) > PUBLISHER_AVATAR_MAX_BYTES:
            return {"ok": False, "error": "Avatar image is empty or too large"}

        cache_dir = getattr(self, "avatar_cache_dir", None)
        if cache_dir is None:
            return {"ok": False, "error": "Avatar cache is not configured"}

        cache_dir.mkdir(parents=True, exist_ok=True)
        path = author_avatar_cache_file_path(cache_dir, raw_author)
        path.write_bytes(body)
        now = dt.datetime.now(dt.UTC)
        self.db.author_profiles.update_one(
            {"rawAuthor": raw_author},
            {"$set": {"avatarSource": "manual", "avatarMimeType": mime_type, "avatarRefreshedAt": now, "updatedAt": now}},
        )
        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "avatarUrl": _cached_author_avatar_api_url(raw_author, "", {**profile, "avatarSource": "manual", "avatarRefreshedAt": now})}

    def link_publisher_device(self, raw_author: str, raw_device: str) -> dict[str, Any]:
        raw_author = _normalize_author(raw_author)
        raw_device = _normalize_author(raw_device)

        if not self.db.author_profiles.find_one({"rawAuthor": raw_author, "profileType": PUBLISHER_PROFILE_TYPE}, {"_id": 1}):
            return {"ok": False, "error": "Publisher profile does not exist"}

        if not self.db.device_report_identities.find_one({"rawAuthor": raw_device}, {"_id": 1}):
            return {"ok": False, "error": "Device profile does not exist"}

        return composed(self).upsert_device_profile_alias(raw_device, raw_author)

    def unlink_publisher_device(self, raw_author: str, raw_device: str) -> dict[str, Any]:
        raw_author = _normalize_author(raw_author)
        raw_device = _normalize_author(raw_device)
        deleted = self.db.author_aliases.delete_many({"sourceRawAuthor": raw_device, "targetRawAuthor": raw_author}).deleted_count
        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "deleted": deleted}

    def is_publisher_profile(self, raw_author: str, profiles: dict[str, dict[str, Any]]) -> bool:
        return str((profiles.get(str(raw_author or "")) or {}).get("profileType") or "person") == PUBLISHER_PROFILE_TYPE

    def publisher_live_date_in_scope(self, item: dict[str, Any], profiles: dict[str, dict[str, Any]], now: dt.datetime, start_date: str | None, end_date: str | None) -> bool:
        raw_author = self.resolve_activity_author(str(item.get("author") or ""))
        if not self.is_publisher_profile(raw_author, profiles):
            return False

        recorded_at = str(item.get("lastRecordedAt") or item.get("recordedAt") or "")
        match = re.search(r"([+-])(\d{2}):(\d{2})$", recorded_at)
        if not match:
            return False

        sign = 1 if match.group(1) == "+" else -1
        offset = dt.timedelta(hours=int(match.group(2)), minutes=int(match.group(3))) * sign
        tz = dt.timezone(offset)
        local_today = now.astimezone(tz).date().isoformat()
        observer_dates = [value for value in (start_date, end_date) if value]
        if observer_dates and local_today > max(observer_dates):
            return False

        return str(item.get("date") or "") == local_today

    def with_publisher_device_presence(self, author: dict[str, Any], send_interval_seconds: int, now: dt.datetime) -> dict[str, Any]:
        item = dict(author)
        last_received_at = _coerce_datetime(item.get("_lastReportReceivedAt")) or _coerce_datetime(item.get("lastReceivedAt"))
        threshold = max(0, send_interval_seconds * 2)
        is_stale = True

        if last_received_at:
            is_stale = max(0, int((now - last_received_at).total_seconds())) > threshold
            if not item.get("lastReceivedAt"):
                item["lastReceivedAt"] = _iso(last_received_at)
            if not item.get("lastRecordedAt") and item.get("_lastReportRecordedAt"):
                item["lastRecordedAt"] = item.get("_lastReportRecordedAt")

        item["status"] = "stale" if is_stale else "online"
        if is_stale:
            item["stalePresence"] = "device"

        item["sendIntervalSeconds"] = send_interval_seconds
        item["staleThresholdSeconds"] = threshold
        item.pop("_lastReportReceivedAt", None)
        item.pop("_lastReportRecordedAt", None)
        return item

    def _publisher_profile(self, raw_author: str) -> dict[str, Any]:
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0}) or {}
        return {
            "rawAuthor": raw_author,
            "displayName": _display_name(raw_author, profile),
            "team": str(profile.get("team") or ""),
            "profileType": PUBLISHER_PROFILE_TYPE,
            "authorColor": str(profile.get("authorColor") or _author_color(raw_author)),
            "avatarUrl": _cached_author_avatar_api_url(raw_author, "", profile),
        }
