from __future__ import annotations

from ..activity_math import *
from ..backend_composable_host import composed


class AuthorRedirectsMixin:
    def resolve_author_alias(self, raw_author: str | None) -> str:
        normalized_author = _normalize_author(raw_author or "Unknown User")
        alias = self.db.author_aliases.find_one({"sourceRawAuthor": normalized_author}, {"_id": 0, "targetRawAuthor": 1})

        if alias and alias.get("targetRawAuthor"):
            return _normalize_author(alias.get("targetRawAuthor"))

        return normalized_author

    def author_alias_keys(self, raw_author: str | None) -> list[str]:
        canonical = self.resolve_author_alias(raw_author)
        keys = {canonical}

        for alias in self.db.author_aliases.find({"targetRawAuthor": canonical}, {"_id": 0, "sourceRawAuthor": 1}):
            source = _normalize_author(alias.get("sourceRawAuthor"))

            if source:
                keys.add(source)

        keys.discard("")
        return sorted(keys)

    def author_aliases(self) -> list[dict[str, Any]]:
        aliases = list(self.db.author_aliases.find({}, {"_id": 0}).sort("sourceRawAuthor", ASCENDING))

        for alias in aliases:
            source = str(alias.get("sourceRawAuthor") or "")
            device_identity = self.db.device_report_identities.find_one(
                {"rawAuthor": source},
                {"_id": 0, "source": 1, "deviceIdHash": 1},
            )

            if not device_identity:
                continue

            device_source = str(device_identity.get("source") or "")
            latest_device_batch = self.db.raw_event_batches.find_one(
                {"author": source, "source": device_source},
                {"_id": 0, "deviceId": 1},
                sort=[("receivedAt", DESCENDING)],
            )
            latest_device_event = self.db.raw_activity_events.find_one(
                {"author": source, "source": device_source},
                {"_id": 0, "deviceId": 1},
                sort=[("receivedAt", DESCENDING)],
            )
            device_id = str(
                (latest_device_batch or {}).get("deviceId")
                or (latest_device_event or {}).get("deviceId")
                or ""
            )

            alias["sourceDeviceSource"] = device_source
            alias["sourceDeviceId"] = device_id
            alias["sourceDeviceIdHash"] = str(device_identity.get("deviceIdHash") or "")

        return aliases

    def upsert_author_alias(self, source_raw_author: str, target_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)
        target = _normalize_author(target_raw_author)

        if not source or not target:
            return {"ok": False, "error": "Source and target authors are required"}

        if source == target:
            return {"ok": False, "error": "Source and target authors must be different"}

        if target not in composed(self).list_authors():
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
        return {"ok": True, "alias": {"sourceRawAuthor": source, "targetRawAuthor": target}}

    def delete_author_alias(self, source_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(source_raw_author)

        if not source:
            return {"ok": False, "error": "Source author is required"}

        result = self.db.author_aliases.delete_one({"sourceRawAuthor": source})
        composed(self).rebuild_aggregates_for_author_dates([source])
        return {"ok": True, "deleted": getattr(result, "deleted_count", 0)}
