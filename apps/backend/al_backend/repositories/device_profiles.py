from __future__ import annotations

from typing import Any

from ..activity_math import ASCENDING, DESCENDING, _display_name, _normalize_author
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class DeviceProfileRepository(MongoComposableMixin):
    def device_profiles(self) -> list[dict[str, Any]]:
        result = []

        for identity in self.db.device_report_identities.find({}, {"_id": 0}):
            raw_author = str(identity.get("rawAuthor") or "")
            source = str(identity.get("source") or "")
            query = {"author": raw_author, "source": source}
            latest_batch = self.db.raw_event_batches.find_one(
                query,
                {"_id": 0},
                sort=[("receivedAt", DESCENDING)],
            )
            latest_event = self.db.raw_activity_events.find_one(
                query,
                {"_id": 0},
                sort=[("receivedAt", DESCENDING)],
            )
            latest = latest_batch or latest_event or {}
            latest_event_metadata = latest_event.get("metadata") if isinstance((latest_event or {}).get("metadata"), dict) else {}
            latest_batch_metadata = latest_batch.get("metadata") if isinstance((latest_batch or {}).get("metadata"), dict) else {}
            identity_metadata = identity.get("lastMetadata") if isinstance(identity.get("lastMetadata"), dict) else {}
            latest_metadata = identity_metadata or latest_event_metadata or latest_batch_metadata
            alias = self.db.author_aliases.find_one({"sourceRawAuthor": raw_author}, {"_id": 0, "targetRawAuthor": 1}) or {}
            linked_author = str(alias.get("targetRawAuthor") or "")
            linked_profile = self.db.author_profiles.find_one({"rawAuthor": linked_author}, {"_id": 0}) if linked_author else None
            platform = str(
                latest_event_metadata.get("platform")
                or latest_metadata.get("platform")
                or latest_event_metadata.get("runtimePlatform")
                or latest_metadata.get("runtimePlatform")
                or ""
            )
            platform_key = platform.lower()
            advertising_id = str(latest_metadata.get("deviceAdvertisingId") or "")

            result.append(
                {
                    "rawDevice": raw_author,
                    "source": source,
                    "runtime": _device_runtime_label(platform),
                    "linkedAuthor": linked_author,
                    "linkedAuthorDisplayName": _display_name(linked_author, linked_profile or {}) if linked_author else "",
                    "idfa": advertising_id if "iphone" in platform_key or "ios" in platform_key else "",
                    "gaid": advertising_id if "android" in platform_key else "",
                    "projectId": str(identity.get("lastProjectId") or latest.get("projectId") or ""),
                    "pluginVersion": str(identity.get("lastPluginVersion") or latest.get("pluginVersion") or ""),
                    "trackingAuthorizationStatus": str(latest_metadata.get("trackingAuthorizationStatus") or ""),
                    "createdAt": identity.get("createdAt"),
                    "lastSeenAt": identity.get("lastSeenAt") or latest.get("receivedAt") or latest.get("recordedAt") or latest.get("occurredAtUtc") or latest.get("sentAt"),
                }
            )

        return sorted(
            result,
            key=lambda item: (
                str(item.get("source") or ""),
                _natural_device_key(str(item.get("rawDevice") or "")),
            ),
        )

    def upsert_device_profile_alias(self, raw_device: str, target_raw_author: str) -> dict[str, Any]:
        source = _normalize_author(raw_device)
        target = _normalize_author(target_raw_author)

        if not source or not target:
            return {"ok": False, "error": "Device and target author are required"}

        if not self.db.device_report_identities.find_one({"rawAuthor": source}, {"_id": 1}):
            return {"ok": False, "error": "Device profile does not exist"}

        return composed(self).upsert_author_alias(source, target)

    def migrate_device_author_profiles(self) -> dict[str, Any]:
        raw_devices = [
            str(item.get("rawAuthor") or "")
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1}).sort("rawAuthor", ASCENDING)
            if item.get("rawAuthor")
        ]
        deleted = 0

        for raw_device in raw_devices:
            deleted += self.db.author_profiles.delete_many({"rawAuthor": raw_device}).deleted_count

        return {
            "ok": True,
            "rawDevices": raw_devices,
            "rawDeviceCount": len(raw_devices),
            "deletedAuthorProfiles": deleted,
        }

    def delete_device_profile(self, raw_device: str) -> dict[str, Any]:
        normalized = _normalize_author(raw_device)

        if not normalized:
            return {"ok": False, "error": "Device profile is required"}

        identity = self.db.device_report_identities.find_one({"rawAuthor": normalized}, {"_id": 0})

        if not identity:
            return {"ok": False, "error": "Device profile does not exist"}

        counts = {
            "deviceReportIdentities": self.db.device_report_identities.delete_many({"rawAuthor": normalized}).deleted_count,
            "authorAliases": self.db.author_aliases.delete_many({"sourceRawAuthor": normalized}).deleted_count,
            "authorProfiles": self.db.author_profiles.delete_many({"rawAuthor": normalized}).deleted_count,
        }
        return {"ok": True, "deleted": counts}

    def delete_all_device_profiles(self) -> dict[str, Any]:
        raw_devices = [
            str(item.get("rawAuthor") or "")
            for item in self.db.device_report_identities.find({}, {"_id": 0, "rawAuthor": 1})
            if item.get("rawAuthor")
        ]

        if not raw_devices:
            return {
                "ok": True,
                "rawDeviceCount": 0,
                "deleted": {
                    "deviceReportIdentities": 0,
                    "authorAliases": 0,
                    "authorProfiles": 0,
                },
                "rawDevices": [],
            }

        counts = {
            "deviceReportIdentities": self.db.device_report_identities.delete_many({"rawAuthor": {"$in": raw_devices}}).deleted_count,
            "authorAliases": self.db.author_aliases.delete_many({"sourceRawAuthor": {"$in": raw_devices}}).deleted_count,
            "authorProfiles": self.db.author_profiles.delete_many({"rawAuthor": {"$in": raw_devices}}).deleted_count,
        }
        return {"ok": True, "rawDeviceCount": len(raw_devices), "rawDevices": raw_devices, "deleted": counts}


def _natural_device_key(value: str) -> tuple[str, int, str]:
    prefix = value.rstrip("0123456789")
    suffix = value[len(prefix):]
    return (prefix.lower(), int(suffix) if suffix else -1, value.lower())


def _device_runtime_label(value: str) -> str:
    normalized = value.strip()
    key = normalized.lower()

    if not key:
        return ""

    if "editor" in key:
        return "Editor"

    if "iphone" in key or "ios" in key:
        return "iOS"

    if "android" in key:
        return "Android"

    if "windows" in key or "osx" in key or "mac" in key or "linux" in key:
        return "Desktop"

    return normalized
