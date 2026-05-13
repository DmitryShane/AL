from __future__ import annotations

import datetime as dt
import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pymongo.database import Database

from .activity_math import _coerce_datetime, _github_username_for_avatar_fetch, _normalize_author

DOWNLOAD_TIMEOUT_SECONDS = 20
DEFAULT_AVATAR_MIME = "image/png"
USER_AGENT = "AL-ActivityLogger-AvatarCache/1.0"
DEFAULT_AVATAR_REFRESH_CADENCE = "month"


def normalize_avatar_refresh_cadence(value: Any) -> str:
    v = str(value or "").strip().lower()

    if v == "week":
        return "week"

    return "month"


def _avatar_cache_stale(refreshed: dt.datetime | None, now: dt.datetime, cadence: str) -> bool:
    if not refreshed:
        return True

    mode = normalize_avatar_refresh_cadence(cadence)

    if mode == "week":
        r_iso = refreshed.isocalendar()
        n_iso = now.isocalendar()
        return (r_iso.year, r_iso.week) != (n_iso.year, n_iso.week)

    return (refreshed.year, refreshed.month) != (now.year, now.month)


def author_avatar_cache_file_path(cache_dir: Path, raw_author: str) -> Path:
    normalized = _normalize_author(raw_author)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.avatar"


def _avatar_refreshed_at_utc(profile: dict[str, Any]) -> dt.datetime | None:
    return _coerce_datetime(profile.get("avatarRefreshedAt"))


def remove_author_avatar_cache_file(cache_dir: Path | None, raw_author: str) -> None:
    if cache_dir is None:
        return

    path = author_avatar_cache_file_path(cache_dir, raw_author)

    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def download_github_avatar(login: str) -> tuple[bytes, str]:
    url = f"https://github.com/{login}.png"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        body = response.read()
        mime = response.headers.get_content_type()

        if not mime:
            mime = DEFAULT_AVATAR_MIME

        if len(body) > 8_000_000:
            raise ValueError("avatar too large")

        return body, mime


def ensure_author_avatar_cached(
    db: Database,
    cache_dir: Path | None,
    raw_author: str,
    *,
    cadence: str = DEFAULT_AVATAR_REFRESH_CADENCE,
    force: bool = False,
) -> tuple[Path | None, str | None]:
    normalized = _normalize_author(raw_author)

    if cache_dir is None:
        return None, None

    profile = db.author_profiles.find_one({"rawAuthor": normalized}, {"_id": 0}) or {}
    if str(profile.get("avatarSource") or "") == "manual":
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = author_avatar_cache_file_path(cache_dir, normalized)

        if path.is_file():
            return path, str(profile.get("avatarMimeType") or "").strip() or DEFAULT_AVATAR_MIME

        return None, None

    github = _github_username_for_avatar_fetch(normalized, profile)

    if not github:
        return None, None

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = author_avatar_cache_file_path(cache_dir, normalized)
    now = dt.datetime.now(dt.UTC)
    refreshed = _avatar_refreshed_at_utc(profile)
    mime_stored = str(profile.get("avatarMimeType") or "").strip() or DEFAULT_AVATAR_MIME
    stale = force or not path.is_file() or _avatar_cache_stale(refreshed, now, cadence)

    if stale:
        try:
            body, mime = download_github_avatar(github)
            path.write_bytes(body)
            db.author_profiles.update_one(
                {"rawAuthor": normalized},
                {"$set": {"avatarRefreshedAt": now, "avatarMimeType": mime}},
            )
            return path, mime
        except (OSError, urllib.error.URLError, TimeoutError, ValueError):
            if path.is_file():
                return path, mime_stored

            return None, None

    return path, mime_stored
