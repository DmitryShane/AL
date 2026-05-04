"""Tests for GitHub avatar cache staleness rules."""

from __future__ import annotations

import datetime as dt
from typing import cast

import pytest

from pymongo.database import Database

from al_backend.author_avatar_cache import _avatar_cache_stale, ensure_author_avatar_cached


UTC = dt.UTC


def test_avatar_cache_stale_month_same_month_not_stale() -> None:
    ref = dt.datetime(2026, 3, 10, 12, tzinfo=UTC)
    now = dt.datetime(2026, 3, 28, 8, tzinfo=UTC)
    assert _avatar_cache_stale(ref, now, "month") is False


def test_avatar_cache_stale_month_new_month_stale() -> None:
    ref = dt.datetime(2026, 3, 31, 23, tzinfo=UTC)
    now = dt.datetime(2026, 4, 1, 0, 1, tzinfo=UTC)
    assert _avatar_cache_stale(ref, now, "month") is True


def test_avatar_cache_stale_week_same_iso_week_not_stale() -> None:
    ref = dt.datetime(2026, 5, 3, 10, tzinfo=UTC)
    now = dt.datetime(2026, 5, 2, 22, tzinfo=UTC)
    assert ref.isocalendar().week == now.isocalendar().week
    assert _avatar_cache_stale(ref, now, "week") is False


def test_avatar_cache_stale_week_boundary_monday_stale() -> None:
    ref = dt.datetime(2026, 5, 3, 23, tzinfo=UTC)
    now = dt.datetime(2026, 5, 4, 1, tzinfo=UTC)
    assert ref.isocalendar().week != now.isocalendar().week
    assert _avatar_cache_stale(ref, now, "week") is True


def test_avatar_cache_stale_none_ref_is_stale() -> None:
    now = dt.datetime(2026, 5, 3, 12, tzinfo=UTC)
    assert _avatar_cache_stale(None, now, "week") is True
    assert _avatar_cache_stale(None, now, "month") is True


def test_ensure_author_avatar_cached_force_calls_download(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_download(login: str) -> tuple[bytes, str]:
        calls.append((login, True))
        return b"\x89PNG\r\n\x1a\n", "image/png"

    monkeypatch.setattr("al_backend.author_avatar_cache.download_github_avatar", fake_download)

    class FakeProfiles:
        def find_one(self, _filter, _proj=None):
            return {
                "rawAuthor": "Alice Dev",
                "githubUsername": "alice",
                "avatarRefreshedAt": dt.datetime(2020, 1, 1, tzinfo=UTC),
                "avatarMimeType": "image/png",
            }

        def update_one(self, *_args, **_kwargs):
            return None

    class FakeDb:
        author_profiles = FakeProfiles()

    path, mime = ensure_author_avatar_cached(cast(Database, FakeDb()), tmp_path, "Alice Dev", cadence="month", force=True)

    assert path is not None and path.is_file()
    assert mime == "image/png"
    assert calls == [("alice", True)]

