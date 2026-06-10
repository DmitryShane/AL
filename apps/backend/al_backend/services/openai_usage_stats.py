from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ..activity_math import *
from ..backend_composable_host import composed


OPENAI_STATS_CACHE_TTL_SECONDS = 300
OPENAI_STATS_USAGE_ENDPOINTS = ("completions", "audio_transcriptions")
OPENAI_STATS_HISTORY_START = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
OPENAI_STATS_ACCUMULATOR_KIND = "openai_stats_accumulator_v1"
OPENAI_STATS_RESPONSE_CACHE_KIND = "openai_stats_response_cache_v1"
OPENAI_STATS_LEGACY_CACHE_KIND = "openai_stats_cache"
OPENAI_STATS_SYNC_STALE_SECONDS = 3600
OPENAI_STATS_MONTH_REFRESH_SECONDS = 6 * 3600
OPENAI_STATS_MAX_DAILY_BUCKETS = 31

ProgressCallback = Callable[[int, int, str], None]


def _fetch_openai_stats(
    api_key: str,
    now: dt.datetime,
    accumulator: dict[str, Any] | None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    start = dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC)
    start_time = int(start.timestamp())
    end_time = int(now.timestamp())
    month_spend, month_currency = _fetch_openai_spend(api_key, start_time, end_time)
    next_accumulator = _refresh_openai_accumulator(api_key, now, accumulator, month_spend, month_currency, progress_callback)

    stats = {
        "configured": True,
        "cached": False,
        "generatedAt": now.isoformat(),
        "periodStart": start.isoformat(),
        "periodEnd": now.isoformat(),
        "totalSpend": round(float(next_accumulator.get("totalSpend") or 0), 6),
        "monthSpend": round(month_spend, 6),
        "currency": str(next_accumulator.get("currency") or month_currency or "usd").upper(),
        "totalTokens": int(next_accumulator.get("totalTokens") or 0),
        "totalRequests": int(next_accumulator.get("totalRequests") or 0),
        "totalsCalculatedThrough": _coerce_datetime(next_accumulator.get("totalsCalculatedThrough")).isoformat()
        if _coerce_datetime(next_accumulator.get("totalsCalculatedThrough"))
        else None,
        "lastIncrementalSyncAt": _coerce_datetime(next_accumulator.get("lastIncrementalSyncAt")).isoformat()
        if _coerce_datetime(next_accumulator.get("lastIncrementalSyncAt"))
        else None,
        "lastMonthRefreshAt": _coerce_datetime(next_accumulator.get("lastMonthRefreshAt")).isoformat()
        if _coerce_datetime(next_accumulator.get("lastMonthRefreshAt"))
        else None,
        "lastRefreshedAt": _coerce_datetime(next_accumulator.get("lastRefreshedAt")).isoformat()
        if _coerce_datetime(next_accumulator.get("lastRefreshedAt"))
        else None,
        "syncStatus": str(next_accumulator.get("syncStatus") or "ready"),
    }
    return {"stats": stats, "accumulator": next_accumulator}


def _fetch_openai_month_only_stats(api_key: str, now: dt.datetime, accumulator: dict[str, Any]) -> dict[str, Any]:
    start = dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC)
    start_time = int(start.timestamp())
    end_time = int(now.timestamp())
    month_spend, month_currency = _fetch_openai_spend(api_key, start_time, end_time)
    previous = dict(accumulator)
    through = _coerce_datetime(previous.get("totalsCalculatedThrough"))
    total_spend = float(previous.get("totalSpend") or 0)
    total_tokens = int(previous.get("totalTokens") or 0)
    total_requests = int(previous.get("totalRequests") or 0)

    if through and through < now and through.astimezone(dt.UTC).date() < now.astimezone(dt.UTC).date():
        spend_delta, currency = _fetch_openai_spend(api_key, int(through.timestamp()), end_time)
        total_spend += spend_delta

        for endpoint in OPENAI_STATS_USAGE_ENDPOINTS:
            for chunk_start, chunk_end in _openai_daily_chunks(through, now):
                usage = _fetch_openai_usage(api_key, endpoint, int(chunk_start.timestamp()), int(chunk_end.timestamp()))
                total_tokens += usage["tokens"]
                total_requests += usage["requests"]

        previous["totalsCalculatedThrough"] = now
        previous["lastIncrementalSyncAt"] = now
        previous["currency"] = str(currency or previous.get("currency") or month_currency or "usd").lower()

    previous.update(
        {
            "kind": OPENAI_STATS_ACCUMULATOR_KIND,
            "totalSpend": round(total_spend, 6),
            "totalTokens": total_tokens,
            "totalRequests": total_requests,
            "monthSpend": round(month_spend, 6),
            "periodStart": start,
            "periodEnd": now,
            "generatedAt": now,
            "lastMonthRefreshAt": now,
            "lastRefreshedAt": now,
            "syncStatus": "ready",
            "syncUpdatedAt": now,
            "currency": str(previous.get("currency") or month_currency or "usd").lower(),
        }
    )

    stats = _openai_stats_from_accumulator(previous)
    stats["cached"] = False
    stats["syncStatus"] = "ready"
    return {"stats": stats, "accumulator": previous}


def _refresh_openai_accumulator(
    api_key: str,
    now: dt.datetime,
    accumulator: dict[str, Any] | None,
    month_spend: float,
    month_currency: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    previous = accumulator or {}
    through = _coerce_datetime(previous.get("totalsCalculatedThrough"))
    bootstrap_complete = bool(previous.get("bootstrapCompletedAt"))

    if not through or not bootstrap_complete:
        range_start = OPENAI_STATS_HISTORY_START
        bootstrap_started_at = now
        previous_spend = 0.0
        previous_tokens = 0
        previous_requests = 0
    else:
        range_start = through
        bootstrap_started_at = _coerce_datetime(previous.get("bootstrapStartedAt")) or now
        previous_spend = float(previous.get("totalSpend") or 0)
        previous_tokens = int(previous.get("totalTokens") or 0)
        previous_requests = int(previous.get("totalRequests") or 0)

    if range_start >= now or range_start.astimezone(dt.UTC).date() >= now.astimezone(dt.UTC).date():
        return {
            **previous,
            "kind": OPENAI_STATS_ACCUMULATOR_KIND,
            "monthSpend": round(month_spend, 6),
            "periodStart": dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC),
            "periodEnd": now,
            "generatedAt": now,
            "currency": str(previous.get("currency") or month_currency or "usd").lower(),
        }

    end_time = int(now.timestamp())
    chunks = _openai_daily_chunks(range_start, now)
    total_steps = 1 + (len(chunks) * len(OPENAI_STATS_USAGE_ENDPOINTS))
    current_step = 0

    if progress_callback:
        progress_callback(current_step, total_steps, "Syncing costs")

    spend_delta, currency = _fetch_openai_spend(api_key, int(range_start.timestamp()), end_time)
    current_step += 1

    if progress_callback:
        progress_callback(current_step, total_steps, "Synced costs")

    tokens_delta = 0
    requests_delta = 0

    for endpoint in OPENAI_STATS_USAGE_ENDPOINTS:
        for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
            label = f"Syncing usage {endpoint} {index}/{len(chunks)}"
            if progress_callback:
                progress_callback(current_step, total_steps, label)

            usage = _fetch_openai_usage(api_key, endpoint, int(chunk_start.timestamp()), int(chunk_end.timestamp()))
            tokens_delta += usage["tokens"]
            requests_delta += usage["requests"]
            current_step += 1

            if progress_callback:
                progress_callback(current_step, total_steps, label)

    return {
        "kind": OPENAI_STATS_ACCUMULATOR_KIND,
        "totalSpend": round(previous_spend + spend_delta, 6),
        "totalTokens": previous_tokens + tokens_delta,
        "totalRequests": previous_requests + requests_delta,
        "currency": str(currency or previous.get("currency") or month_currency or "usd").lower(),
        "totalsCalculatedThrough": now,
        "bootstrapStartedAt": bootstrap_started_at,
        "bootstrapCompletedAt": _coerce_datetime(previous.get("bootstrapCompletedAt")) or now,
        "lastIncrementalSyncAt": now,
        "lastMonthRefreshAt": now,
        "lastRefreshedAt": now,
        "monthSpend": round(month_spend, 6),
        "periodStart": dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC),
        "periodEnd": now,
        "generatedAt": now,
    }


def _fetch_openai_spend(api_key: str, start_time: int, end_time: int) -> tuple[float, str]:
    if _openai_daily_bucket_range_is_empty(start_time, end_time):
        return 0.0, "usd"

    params: dict[str, int | str | list[str]] = {
        "start_time": start_time,
        "end_time": end_time,
        "bucket_width": "1d",
        "limit": OPENAI_STATS_MAX_DAILY_BUCKETS,
    }

    spend = 0.0
    currency = "usd"
    page: str | None = None

    while True:
        params["limit"] = OPENAI_STATS_MAX_DAILY_BUCKETS

        if page:
            params["page"] = page
        elif "page" in params:
            del params["page"]

        payload = _openai_get(api_key, "/v1/organization/costs", params)

        for bucket in payload.get("data", []):
            for result in bucket.get("results", bucket.get("result", [])):
                amount = result.get("amount") or {}
                spend += float(amount.get("value") or 0)
                currency = amount.get("currency") or currency

        page = payload.get("next_page")
        if not page:
            break

    return spend, currency


def _fetch_openai_usage(api_key: str, endpoint: str, start_time: int, end_time: int) -> dict[str, int]:
    if _openai_daily_bucket_range_is_empty(start_time, end_time):
        return {"tokens": 0, "requests": 0}

    params: dict[str, int | str | list[str]] = {
        "start_time": start_time,
        "end_time": end_time,
        "bucket_width": "1d",
        "limit": OPENAI_STATS_MAX_DAILY_BUCKETS,
    }

    tokens = 0
    requests = 0
    page: str | None = None

    while True:
        if page:
            params["page"] = page
        elif "page" in params:
            del params["page"]

        payload = _openai_get(api_key, f"/v1/organization/usage/{endpoint}", params)

        for bucket in payload.get("data", []):
            for result in bucket.get("results", bucket.get("result", [])):
                tokens += int(result.get("input_tokens") or 0)
                tokens += int(result.get("output_tokens") or 0)
                requests += int(result.get("num_model_requests") or 0)

        page = payload.get("next_page")
        if not page:
            break

    return {"tokens": tokens, "requests": requests}


def _openai_daily_chunks(start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    if start >= end or start.astimezone(dt.UTC).date() >= end.astimezone(dt.UTC).date():
        return []

    chunks: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = start
    max_delta = dt.timedelta(days=OPENAI_STATS_MAX_DAILY_BUCKETS)

    while cursor < end:
        chunk_end = min(cursor + max_delta, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end

    return chunks


def _openai_daily_bucket_range_is_empty(start_time: int, end_time: int) -> bool:
    if end_time <= start_time:
        return True

    start = dt.datetime.fromtimestamp(start_time, dt.UTC)
    end = dt.datetime.fromtimestamp(end_time, dt.UTC)
    return start.date() >= end.date()


def _openai_get(api_key: str, path: str, params: dict[str, int | str | list[str]]) -> dict[str, Any]:
    url = f"https://api.openai.com{path}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI stats request failed with HTTP {exc.code}: {detail[:240]}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenAI stats request failed: {exc}") from exc


def _openai_stats_from_accumulator(accumulator: dict[str, Any]) -> dict[str, Any]:
    generated_at = _coerce_datetime(accumulator.get("generatedAt"))
    period_start = _coerce_datetime(accumulator.get("periodStart"))
    period_end = _coerce_datetime(accumulator.get("periodEnd"))
    totals_calculated_through = _coerce_datetime(accumulator.get("totalsCalculatedThrough"))
    last_incremental_sync_at = _coerce_datetime(accumulator.get("lastIncrementalSyncAt"))
    last_month_refresh_at = _coerce_datetime(accumulator.get("lastMonthRefreshAt"))
    last_refreshed_at = _coerce_datetime(accumulator.get("lastRefreshedAt"))

    return {
        "configured": True,
        "cached": True,
        "generatedAt": generated_at.isoformat() if generated_at else None,
        "periodStart": period_start.isoformat() if period_start else None,
        "periodEnd": period_end.isoformat() if period_end else None,
        "totalSpend": round(float(accumulator.get("totalSpend") or 0), 6),
        "monthSpend": round(float(accumulator.get("monthSpend") or 0), 6),
        "currency": str(accumulator.get("currency") or "usd").upper(),
        "totalTokens": int(accumulator.get("totalTokens") or 0),
        "totalRequests": int(accumulator.get("totalRequests") or 0),
        "totalsCalculatedThrough": totals_calculated_through.isoformat() if totals_calculated_through else None,
        "lastIncrementalSyncAt": last_incremental_sync_at.isoformat() if last_incremental_sync_at else None,
        "lastMonthRefreshAt": last_month_refresh_at.isoformat() if last_month_refresh_at else None,
        "lastRefreshedAt": last_refreshed_at.isoformat() if last_refreshed_at else None,
        "syncProgressCurrent": int(accumulator.get("syncProgressCurrent") or 0),
        "syncProgressTotal": int(accumulator.get("syncProgressTotal") or 0),
        "syncProgressLabel": str(accumulator.get("syncProgressLabel") or ""),
        "syncStatus": str(accumulator.get("syncStatus") or "ready"),
    }


def _empty_openai_stats(now: dt.datetime, *, sync_status: str = "totalsMissing") -> dict[str, Any]:
    start = dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC)
    return {
        "configured": True,
        "cached": True,
        "generatedAt": now.isoformat(),
        "periodStart": start.isoformat(),
        "periodEnd": now.isoformat(),
        "totalSpend": 0,
        "monthSpend": 0,
        "currency": "USD",
        "totalTokens": 0,
        "totalRequests": 0,
        "syncStatus": sync_status,
        "syncProgressCurrent": 0,
        "syncProgressTotal": 0,
        "syncProgressLabel": "",
    }


def _openai_stats_sync_in_progress(accumulator: dict[str, Any] | None, now: dt.datetime) -> bool:
    if not accumulator:
        return False

    if accumulator.get("syncStatus") not in {"syncingTotals", "syncingMonth"}:
        return False

    sync_started_at = _coerce_datetime(accumulator.get("syncStartedAt") or accumulator.get("bootstrapStartedAt"))
    if not sync_started_at:
        return False

    return (now - sync_started_at).total_seconds() < OPENAI_STATS_SYNC_STALE_SECONDS


class OpenAIUsageStatsMixin:
    def get_openai_stats(self, refresh: str | bool | None = None, background_tasks: Any | None = None) -> dict[str, Any]:
        if not getattr(composed(self), "openai_usage_api_key", ""):
            return {"configured": False, "error": "AL_OPENAI_USAGE_API_KEY is not configured"}

        refresh_mode = self._normalize_openai_stats_refresh_mode(refresh)
        cached = self._openai_stats_cached_response()
        cached_at = _coerce_datetime(cached.get("cachedAt"))
        now = dt.datetime.now(dt.UTC)

        if (
            refresh_mode is None
            and cached_at
            and (now - cached_at).total_seconds() < OPENAI_STATS_CACHE_TTL_SECONDS
            and cached.get("stats")
        ):
            stats = dict(cached["stats"])
            stats["cached"] = True
            return stats

        accumulator = self.db.system_settings.find_one({"kind": OPENAI_STATS_ACCUMULATOR_KIND}, {"_id": 0}) or None
        accumulator_ready = bool(accumulator and accumulator.get("bootstrapCompletedAt"))

        if refresh_mode == "totals":
            return self._queue_openai_stats_totals_sync(accumulator, cached.get("stats"), now, background_tasks)

        if not accumulator_ready:
            stats = dict(cached.get("stats") or _empty_openai_stats(now))
            stats["cached"] = True
            stats["syncStatus"] = "totalsMissing"
            return stats

        if refresh_mode is None and self._openai_month_refresh_due(accumulator, now) and background_tasks is not None:
            stats = self._queue_openai_stats_month_sync(accumulator, cached.get("stats"), now, background_tasks)
            self._store_openai_stats_cache(stats, now)
            return stats

        if refresh_mode == "month":
            return self._refresh_openai_stats_month(accumulator, now)

        try:
            result = _fetch_openai_stats(composed(self).openai_usage_api_key, now, accumulator)
        except RuntimeError as exc:
            if accumulator:
                stats = _openai_stats_from_accumulator(accumulator)
                stats["cached"] = True
                stats["error"] = str(exc)
                return stats
            return {"configured": True, "error": str(exc)}

        stats = result["stats"]
        next_accumulator = result["accumulator"]
        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
            {"$set": next_accumulator},
            upsert=True,
        )
        self._store_openai_stats_cache(stats, now)
        return stats

    def _openai_stats_cached_response(self) -> dict[str, Any]:
        cached = self.db.system_settings.find_one({"kind": OPENAI_STATS_RESPONSE_CACHE_KIND}, {"_id": 0}) or {}
        if cached.get("stats"):
            return cached

        legacy = self.db.system_settings.find_one({"kind": OPENAI_STATS_LEGACY_CACHE_KIND}, {"_id": 0}) or {}
        if not legacy.get("stats"):
            return {}

        stats = dict(legacy["stats"])
        stats.pop("projectId", None)
        return {"kind": OPENAI_STATS_RESPONSE_CACHE_KIND, "cachedAt": legacy.get("cachedAt"), "stats": stats}

    def _normalize_openai_stats_refresh_mode(self, refresh: str | bool | None) -> str | None:
        if refresh is True:
            return "month"

        if refresh is False or refresh is None or refresh == "":
            return None

        value = str(refresh).strip().lower()
        if value == "true":
            return "month"

        if value in {"month", "totals"}:
            return value

        return None

    def _openai_month_refresh_due(self, accumulator: dict[str, Any], now: dt.datetime) -> bool:
        last_month_refresh_at = _coerce_datetime(accumulator.get("lastMonthRefreshAt"))
        if not last_month_refresh_at:
            return True

        return (now - last_month_refresh_at).total_seconds() >= OPENAI_STATS_MONTH_REFRESH_SECONDS

    def _queue_openai_stats_totals_sync(
        self,
        accumulator: dict[str, Any] | None,
        cached_stats: dict[str, Any] | None,
        now: dt.datetime,
        background_tasks: Any | None,
    ) -> dict[str, Any]:
        if _openai_stats_sync_in_progress(accumulator, now):
            fallback_stats = _openai_stats_from_accumulator(accumulator) if accumulator else _empty_openai_stats(now)
            stats = dict(cached_stats or fallback_stats)
            stats["cached"] = True
            stats["syncStatus"] = "syncingTotals"
            return stats

        sync_doc = {
            "kind": OPENAI_STATS_ACCUMULATOR_KIND,
            "syncStatus": "syncingTotals",
            "syncStartedAt": now,
            "syncUpdatedAt": now,
            "syncProgressCurrent": 0,
            "syncProgressTotal": 1,
            "syncProgressLabel": "Starting totals sync",
            "bootstrapStartedAt": now,
        }
        self.db.system_settings.update_one({"kind": OPENAI_STATS_ACCUMULATOR_KIND}, {"$set": sync_doc}, upsert=True)
        if background_tasks is not None:
            background_tasks.add_task(self._run_openai_stats_totals_sync)

        stats = dict(cached_stats or _empty_openai_stats(now))
        stats["cached"] = True
        stats["syncStatus"] = "syncingTotals"
        stats["syncProgressCurrent"] = 0
        stats["syncProgressTotal"] = 1
        stats["syncProgressLabel"] = "Starting totals sync"
        return stats

    def _queue_openai_stats_month_sync(
        self,
        accumulator: dict[str, Any],
        cached_stats: dict[str, Any] | None,
        now: dt.datetime,
        background_tasks: Any,
    ) -> dict[str, Any]:
        if _openai_stats_sync_in_progress(accumulator, now):
            stats = dict(cached_stats or _openai_stats_from_accumulator(accumulator))
            stats["cached"] = True
            return stats

        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
            {
                "$set": {
                    "syncStatus": "syncingMonth",
                    "syncStartedAt": now,
                    "syncUpdatedAt": now,
                    "syncProgressCurrent": 0,
                    "syncProgressTotal": 1,
                    "syncProgressLabel": "Syncing current month",
                }
            },
            upsert=True,
        )
        background_tasks.add_task(self._run_openai_stats_month_sync)
        stats = dict(cached_stats or _openai_stats_from_accumulator(accumulator))
        stats["cached"] = True
        stats["syncStatus"] = "syncingMonth"
        stats["syncProgressCurrent"] = 0
        stats["syncProgressTotal"] = 1
        stats["syncProgressLabel"] = "Syncing current month"
        return stats

    def _refresh_openai_stats_month(self, accumulator: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
        try:
            result = _fetch_openai_month_only_stats(composed(self).openai_usage_api_key, now, accumulator)
        except RuntimeError as exc:
            stats = _openai_stats_from_accumulator(accumulator)
            stats["cached"] = True
            stats["error"] = str(exc)
            return stats

        stats = result["stats"]
        next_accumulator = result["accumulator"]
        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
            {"$set": next_accumulator, "$unset": {"lastError": ""}},
            upsert=True,
        )
        self._store_openai_stats_cache(stats, now)
        return stats

    def _run_openai_stats_month_sync(self) -> None:
        if not getattr(composed(self), "openai_usage_api_key", ""):
            return

        now = dt.datetime.now(dt.UTC)
        accumulator = self.db.system_settings.find_one({"kind": OPENAI_STATS_ACCUMULATOR_KIND}, {"_id": 0}) or None
        if not accumulator or not accumulator.get("bootstrapCompletedAt"):
            return

        self._refresh_openai_stats_month(accumulator, now)

    def _run_openai_stats_totals_sync(self) -> None:
        if not getattr(composed(self), "openai_usage_api_key", ""):
            return

        now = dt.datetime.now(dt.UTC)
        accumulator: dict[str, Any] | None = None

        def progress(current: int, total: int, label: str) -> None:
            self.db.system_settings.update_one(
                {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
                {
                    "$set": {
                        "kind": OPENAI_STATS_ACCUMULATOR_KIND,
                        "syncStatus": "syncingTotals",
                        "syncUpdatedAt": dt.datetime.now(dt.UTC),
                        "syncProgressCurrent": current,
                        "syncProgressTotal": total,
                        "syncProgressLabel": label,
                    }
                },
                upsert=True,
            )

        try:
            result = _fetch_openai_stats(composed(self).openai_usage_api_key, now, accumulator, progress_callback=progress)
        except RuntimeError as exc:
            self.db.system_settings.update_one(
                {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
                {
                    "$set": {
                        "kind": OPENAI_STATS_ACCUMULATOR_KIND,
                        "syncStatus": "error",
                        "syncUpdatedAt": dt.datetime.now(dt.UTC),
                        "syncProgressLabel": "Totals sync failed",
                        "lastError": str(exc),
                    }
                },
                upsert=True,
            )
            return

        stats = result["stats"]
        stats["syncStatus"] = "ready"
        next_accumulator = result["accumulator"]
        next_accumulator["syncStatus"] = "ready"
        next_accumulator["syncUpdatedAt"] = dt.datetime.now(dt.UTC)
        next_accumulator["syncProgressCurrent"] = next_accumulator.get("syncProgressTotal") or 1
        next_accumulator["syncProgressTotal"] = next_accumulator.get("syncProgressTotal") or 1
        next_accumulator["syncProgressLabel"] = "Totals sync complete"
        next_accumulator["lastMonthRefreshAt"] = next_accumulator.get("lastMonthRefreshAt") or now
        next_accumulator["lastRefreshedAt"] = now
        next_accumulator.pop("lastError", None)
        stats = _openai_stats_from_accumulator(next_accumulator)
        stats["cached"] = False

        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
            {"$set": next_accumulator, "$unset": {"lastError": ""}},
            upsert=True,
        )
        self._store_openai_stats_cache(stats, dt.datetime.now(dt.UTC))

    def _store_openai_stats_cache(self, stats: dict[str, Any], cached_at: dt.datetime) -> None:
        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_RESPONSE_CACHE_KIND},
            {"$set": {"kind": OPENAI_STATS_RESPONSE_CACHE_KIND, "cachedAt": cached_at, "stats": stats}},
            upsert=True,
        )
