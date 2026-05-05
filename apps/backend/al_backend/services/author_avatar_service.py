from __future__ import annotations

from typing import Any

from ..activity_math import _github_username_for_avatar_fetch, _normalize_author
from ..author_avatar_cache import ensure_author_avatar_cached
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


class AuthorAvatarService(MongoComposableMixin):
    def refresh_author_github_avatar(self, raw_author: str) -> dict[str, Any]:
        resolved = composed(self).resolve_author_alias(_normalize_author(raw_author))
        profile = self.db.author_profiles.find_one(
            {"rawAuthor": resolved}, {"_id": 0, "githubUsername": 1, "github_username": 1}
        ) or {}

        if not _github_username_for_avatar_fetch(resolved, profile):
            return {"ok": False, "error": "GitHub username is not set for this profile"}

        cadence = composed(self).get_avatar_refresh_cadence()
        path, _ = ensure_author_avatar_cached(
            self.db,
            getattr(self, "avatar_cache_dir", None),
            resolved,
            cadence=cadence,
            force=True,
        )

        if path is not None and path.is_file():
            return {"ok": True, "rawAuthor": resolved}

        return {"ok": False, "error": "Avatar could not be downloaded"}

    def refresh_all_author_github_avatars(self) -> dict[str, Any]:
        cache_dir = getattr(self, "avatar_cache_dir", None)
        cadence = composed(self).get_avatar_refresh_cadence()
        attempted = 0
        refreshed = 0
        failed: list[str] = []

        for doc in self.db.author_profiles.find({}, {"rawAuthor": 1, "githubUsername": 1, "github_username": 1}):
            raw_author = doc.get("rawAuthor")

            if not raw_author or not _github_username_for_avatar_fetch(raw_author, doc):
                continue

            resolved = composed(self).resolve_author_alias(_normalize_author(raw_author))
            attempted += 1
            path, _ = ensure_author_avatar_cached(
                self.db,
                cache_dir,
                resolved,
                cadence=cadence,
                force=True,
            )

            if path is not None and path.is_file():
                refreshed += 1
            else:
                failed.append(resolved)

        return {"ok": True, "attempted": attempted, "refreshed": refreshed, "failedRawAuthors": failed}
