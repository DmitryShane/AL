import datetime as dt

import al_backend.repositories.settings as settings_repo
from tests.fakes import fake_repository


def test_openai_stats_totals_sync_bootstraps_organization_totals(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    calls: list[tuple[str, int, int]] = []

    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))

    def fake_spend(api_key: str, start_time: int, end_time: int) -> tuple[float, str]:
        assert api_key == "usage-key"
        calls.append(("spend", start_time, end_time))
        if start_time == int(dt.datetime(2026, 5, 1, tzinfo=dt.UTC).timestamp()):
            return 12.34, "usd"
        assert start_time == int(settings_repo.OPENAI_STATS_HISTORY_START.timestamp())
        return 98.76, "usd"

    def fake_usage(api_key: str, endpoint: str, start_time: int, end_time: int) -> dict[str, int]:
        assert api_key == "usage-key"
        calls.append((endpoint, start_time, end_time))
        return {"tokens": 1000 if endpoint == "completions" else 50, "requests": 10 if endpoint == "completions" else 2}

    monkeypatch.setattr(settings_repo, "_fetch_openai_spend", fake_spend)
    monkeypatch.setattr(settings_repo, "_fetch_openai_usage", fake_usage)

    monkeypatch.setattr(
        settings_repo.dt,
        "datetime",
        fixed_datetime_class(dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)),
    )

    repo._run_openai_stats_totals_sync()

    stats = repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_RESPONSE_CACHE_KIND})["stats"]
    assert stats["totalSpend"] == 98.76
    assert stats["monthSpend"] == 12.34
    expected_chunk_count = len(settings_repo._openai_daily_chunks(settings_repo.OPENAI_STATS_HISTORY_START, now))
    assert stats["totalTokens"] == expected_chunk_count * 1050
    assert stats["totalRequests"] == expected_chunk_count * 12
    assert "projectId" not in stats
    assert any(call[0] == "completions" for call in calls)
    assert repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND})["totalSpend"] == 98.76


def test_openai_stats_month_refresh_uses_incremental_watermark(monkeypatch) -> None:
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

    def fake_spend(_api_key: str, start_time: int, end_time: int) -> tuple[float, str]:
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

    stats = repo.get_openai_stats(refresh="month")

    assert stats["totalSpend"] == 21.5
    assert stats["monthSpend"] == 5.0
    assert stats["totalTokens"] == 2200
    assert stats["totalRequests"] == 22
    assert (int(previous_through.timestamp()), int(now.timestamp())) in spend_ranges
    assert {item[0] for item in usage_ranges} == {"completions", "audio_transcriptions"}


def test_openai_stats_historical_usage_is_chunked_to_31_days(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 3, 5, 12, 0, tzinfo=dt.UTC)
    start = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo, "OPENAI_STATS_HISTORY_START", start)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    usage_ranges: list[tuple[str, int, int]] = []

    monkeypatch.setattr(settings_repo, "_fetch_openai_spend", lambda *_args: (1.0, "usd"))

    def fake_usage(_api_key: str, endpoint: str, start_time: int, end_time: int) -> dict[str, int]:
        usage_ranges.append((endpoint, start_time, end_time))
        assert end_time - start_time <= 31 * 24 * 3600
        return {"tokens": 1, "requests": 1}

    monkeypatch.setattr(settings_repo, "_fetch_openai_usage", fake_usage)

    repo._run_openai_stats_totals_sync()

    completions_ranges = [item for item in usage_ranges if item[0] == "completions"]
    transcriptions_ranges = [item for item in usage_ranges if item[0] == "audio_transcriptions"]
    assert len(completions_ranges) == 3
    assert len(transcriptions_ranges) == 3


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

    monkeypatch.setattr(settings_repo, "_fetch_openai_month_only_stats", fake_fetch)

    cached = repo.get_openai_stats()
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
            "totalSpend": 1,
            "totalTokens": 1,
            "totalRequests": 1,
            "currency": "usd",
            "totalsCalculatedThrough": now.isoformat(),
            "bootstrapCompletedAt": now.isoformat(),
        }
    )
    refreshed = repo.get_openai_stats(refresh="month")

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
            "bootstrapCompletedAt": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
        }
    )

    monkeypatch.setattr(settings_repo, "_fetch_openai_month_only_stats", lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")))

    stats = repo.get_openai_stats(refresh=True)

    assert stats["cached"] is True
    assert stats["totalSpend"] == 20.0
    assert stats["monthSpend"] == 4.0
    assert stats["totalTokens"] == 2000
    assert stats["totalRequests"] == 20
    assert stats["error"] == "boom"


def test_openai_stats_month_refresh_without_accumulator_does_not_bootstrap(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    monkeypatch.setattr(
        settings_repo,
        "_fetch_openai_stats",
        lambda *_args: (_ for _ in ()).throw(AssertionError("bootstrap must not run in request")),
    )
    background_tasks = FakeBackgroundTasks()

    stats = repo.get_openai_stats(refresh="month", background_tasks=background_tasks)

    assert stats["syncStatus"] == "totalsMissing"
    assert stats["cached"] is True
    assert stats["totalSpend"] == 0
    assert len(background_tasks.tasks) == 0
    assert repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND}) is None


def test_openai_stats_uses_legacy_cache_when_new_accumulator_is_missing(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_LEGACY_CACHE_KIND,
            "cachedAt": now.isoformat(),
            "stats": {
                "configured": True,
                "totalSpend": 12.5,
                "monthSpend": 3.5,
                "totalTokens": 123,
                "totalRequests": 4,
                "projectId": "old-project",
            },
        }
    )

    stats = repo.get_openai_stats(refresh="month")

    assert stats["totalSpend"] == 12.5
    assert stats["monthSpend"] == 3.5
    assert stats["syncStatus"] == "totalsMissing"
    assert "projectId" not in stats


def test_openai_stats_totals_refresh_queues_background_sync(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    background_tasks = FakeBackgroundTasks()

    stats = repo.get_openai_stats(refresh="totals", background_tasks=background_tasks)

    assert stats["syncStatus"] == "syncingTotals"
    assert stats["cached"] is True
    assert len(background_tasks.tasks) == 1
    accumulator = repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND})
    assert accumulator["syncStatus"] == "syncingTotals"
    assert accumulator["syncStartedAt"] == now


def test_openai_stats_totals_refresh_does_not_queue_duplicate_sync(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
            "syncStatus": "syncingTotals",
            "syncStartedAt": now.isoformat(),
        }
    )
    background_tasks = FakeBackgroundTasks()

    stats = repo.get_openai_stats(refresh="totals", background_tasks=background_tasks)

    assert stats["syncStatus"] == "syncingTotals"
    assert len(background_tasks.tasks) == 0


def test_openai_stats_background_totals_sync_updates_accumulator_and_cache(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))

    def fake_fetch(_api_key: str, fetch_now: dt.datetime, _accumulator: dict | None, progress_callback=None) -> dict:
        return {
            "stats": {
                "configured": True,
                "cached": False,
                "generatedAt": fetch_now.isoformat(),
                "periodStart": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
                "periodEnd": fetch_now.isoformat(),
                "totalSpend": 42,
                "monthSpend": 7,
                "currency": "USD",
                "totalTokens": 4000,
                "totalRequests": 40,
            },
            "accumulator": {
                "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
                "totalSpend": 42,
                "monthSpend": 7,
                "currency": "usd",
                "totalTokens": 4000,
                "totalRequests": 40,
                "totalsCalculatedThrough": fetch_now,
                "bootstrapStartedAt": fetch_now,
                "bootstrapCompletedAt": fetch_now,
                "lastIncrementalSyncAt": fetch_now,
            },
        }

    monkeypatch.setattr(settings_repo, "_fetch_openai_stats", fake_fetch)

    repo._run_openai_stats_totals_sync()

    accumulator = repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND})
    cache = repo.db.system_settings.find_one({"kind": settings_repo.OPENAI_STATS_RESPONSE_CACHE_KIND})
    assert accumulator["syncStatus"] == "ready"
    assert accumulator["totalSpend"] == 42
    assert cache["stats"]["syncStatus"] == "ready"
    assert cache["stats"]["totalTokens"] == 4000


def test_openai_stats_get_queues_lazy_month_refresh_when_due(monkeypatch) -> None:
    repo = fake_repository()
    repo.openai_usage_api_key = "usage-key"
    now = dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC)
    stale_month = now - dt.timedelta(hours=7)
    monkeypatch.setattr(settings_repo.dt, "datetime", fixed_datetime_class(now))
    repo.db.system_settings.insert_one(
        {
            "kind": settings_repo.OPENAI_STATS_ACCUMULATOR_KIND,
            "totalSpend": 20.0,
            "monthSpend": 4.0,
            "totalTokens": 2000,
            "totalRequests": 20,
            "currency": "usd",
            "generatedAt": stale_month.isoformat(),
            "periodStart": dt.datetime(2026, 5, 1, tzinfo=dt.UTC).isoformat(),
            "periodEnd": stale_month.isoformat(),
            "totalsCalculatedThrough": stale_month.isoformat(),
            "lastMonthRefreshAt": stale_month.isoformat(),
            "bootstrapCompletedAt": stale_month.isoformat(),
        }
    )
    background_tasks = FakeBackgroundTasks()

    stats = repo.get_openai_stats(background_tasks=background_tasks)

    assert stats["syncStatus"] == "syncingMonth"
    assert stats["cached"] is True
    assert len(background_tasks.tasks) == 1


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


def fixed_datetime_class(fixed_now: dt.datetime):
    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    return FixedDateTime
