from __future__ import annotations

from ..activity_math import *
from ..author_avatar_cache import DEFAULT_AVATAR_REFRESH_CADENCE, normalize_avatar_refresh_cadence
from ..backend_composable_host import composed


class AvatarSettingsMixin:
    def get_avatar_refresh_cadence(self) -> str:
        doc = self.db.system_settings.find_one({"kind": "avatars"}, {"_id": 0, "refreshCadence": 1}) or {}
        return normalize_avatar_refresh_cadence(doc.get("refreshCadence") or DEFAULT_AVATAR_REFRESH_CADENCE)

    def upsert_avatar_settings(self, refresh_cadence: str) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        cadence = normalize_avatar_refresh_cadence(refresh_cadence)
        self.db.system_settings.update_one(
            {"kind": "avatars"},
            {
                "$set": {
                    "kind": "avatars",
                    "refreshCadence": cadence,
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return {"ok": True, "avatarRefreshCadence": cadence}
