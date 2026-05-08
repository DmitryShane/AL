import datetime as dt

import al_backend.repositories.settings as settings_repo
from tests.fakes import fake_repository


def test_openai_stats_bootstraps_organization_totals(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    calls: list[tuple[str, int, int]] = []

    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))

    def fake_spend(api_key: str, start_time: int, end_time: int, limit: int) -> tuple[float, str]:
        assert api_key == "usage-key"
        calls.append(("spend", start_time, end_time))
        if start_time == int(dt.datetime(2026, 5, 1, tzinfo=dt.UTC).timestamp()):
            return 12.34, "usd"
        assert start_time == int(settings_repo.OPENAI_STATS_HISTORY_START.timestamp())
        return 98.76, "usd"

    def fake_usage(api_key: str, endpoint: str, start_time: int, end_time: int) -> dict[str, int]:
        assert api_key == "usage-key"
        calls.append((endpoint, start_time, end_time))
        assert start_time == int(settings_repo.OPENAI_STATS_HISTORY_START.timestamp())
        return {"tokens": 1000 if endpoint == "completions" else 50, "requests": 10 if endpoint == "completions" else 2}

    monkeypatch.setattr(settings_repo, "_fetch_openai_spend", fake_spend)
    monkeypatch.setattr(settings_repo, "_fetch_openai_usage", fake_usage)

    stats = repo.get_openai_stats(refresh=True)

    assert stats["totalSpend"] == 98.76
    assert stats["monthSpend"] == 12.34
    assert stats["totalTokens"] == 1050
    assert stats["totalRequests"] == 12
    assert "projectId" not in stats
    assert any(call[0] == "completions" for call in calls)
    assert repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND})["totalSpend"] == 98.76


def test_openai_stats_refresh_uses_incremental_watermark(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    previous_through = dt.datetime(2026, 5, 8, 10, 0, tzinfo=dt.UTC)
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
            "totalSpend": 20.0,
            "totalTokens": 2000,
            "totalRequests": 20,
            "currency": "usd",
            "totalsCalculatedThrough": previous_through.isoformat(),
            "bootstrapStartedAt": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
            "bootstrapCompletedAt": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
        }
    )

    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    spend_ranges: list[tuple[int, int]] = []
    usage_ranges: list[tuple[str, int, int]] = []

    def fake_spend(_api_key: str, start_time: int, end_time: int, _limit: int) -> tuple[float, str]:
        spend_ranges.append((start_time, end_time))
        month_start = int(dt.datetime(2026, 5, 1, tzinfo=dt.UTC).timestamp())
        if start_time == month_start:
            return 5.0, "usd"
        assert start_time == int(previous_through.timestamp())
        return 1.5, "usd"

    def fake_usage(_api_key: str, endpoint: str, start_time: int, end_time: int) -> dict[str, int]:
        usage_ranges.append((endpoint, start_time, end_time))
        assert start_time == int(previous_through.timestamp())
        return {"tokens": 100, "requests": 1}

    monkeypatch.setattr(settings_repo, "_fetch_openai_spend", fake_spend)
    monkeypatch.setattr(settings_repo, "_fetch_openai_usage", fake_usage)

    stats = repo.get_openai_stats(refresh=True)

    assert stats["totalSpend"] == 21.5
    assert stats["monthSpend"] == 5.0
    assert stats["totalTokens"] == 2200
    assert stats["totalRequests"] == 22
    assert (int(previous_through.timestamp()), int(now.timestamp())) in spend_ranges
    assert {item[0] for item in usage_ranges} == {"completions", "audio_transcriptions"}


def test_openai_stats_refresh_bypasses_response_cache(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_RESPONSE_CACHE_KIND,
            "cachedAt": now.isoformat(),
            "stats": {"configured": True, "cached": False, "totalSpend": 1, "monthSpend": 1},
        }
    )
    calls = 0

    def fake_fetch(api_key: str, fetch_now: dt.datetime, accumulator: dict | None) -> dict:
        nonlocal calls
        calls += 1
        return {
            "stats": {
                "configured": True,
                "cached": False,
                "generatedAt": fetch_now.isoformat(),
                "periodStart": fetch_now.isoformat(),
                "periodEnd": fetch_now.isoformat(),
                "totalSpend": 2,
                "monthSpend": 2,
                "currency": "USD",
                "totalTokens": 2,
                "totalRequests": 2,
            },
            "accumulator": {"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND, "totalSpend": 2},
        }

    monkeypatch.setattr(settings_repo, "_fetch_openai_stats", fake_fetch)

    cached = repo.get_openai_stats()
    refreshed = repo.get_openai_stats(refresh=True)

    assert cached["totalSpend"] == 1
    assert cached["cached"] is True
    assert refreshed["totalSpend"] == 2
    assert calls == 1


def test_openai_stats_incremental_error_returns_stored_totals(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
            "totalSpend": 20.0,
            "monthSpend": 4.0,
            "totalTokens": 2000,
            "totalRequests": 20,
            "currency": "usd",
            "generatedAt": dt.datetime(2026, 5, 8, 10, 0, tzinfo=dt.UTC).isoformat(),
            "periodStart": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
            "periodEnd": dt.datetime(2026, 5, 8, 10, 0, tzinfo=dt.UTC).isoformat(),
        }
    )

    monkeypatch.setattr(settings_repo, "_fetch_openai_stats", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))

    stats = repo.get_openai_stats(refresh=True)

    assert stats["cached"] is True
    assert stats["totalSpend"] == 20.0
    assert stats["monthSpend"] == 4.0
    assert stats["totalTokens"] == 2000
    assert stats["totalRequests"] == 20
    assert stats["error"] == "boom"


def fixed_datetime_class(fixed_now: dt.datetime):
    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    return FixedDateTime
