from __future__ import annotations

import shutil
import socket
import subprocess
import json
import random
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError
from ..activity_math import *
from ..author_avatar_cache import DEFAULT_AVATAR_REFRESH_CADENCE, normalize_avatar_refresh_cadence
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


SERVER_STATS_PATHS = {
    "system": Path("/usr"),
    "var": Path("/var"),
    "app": Path("/opt/al"),
    "mongo": Path("/var/lib/mongodb"),
    "aptCache": Path("/var/cache/apt"),
    "logs": Path("/var/log"),
}
SERVER_STATS_SERVICES = (
    ("backend", "AL Backend API", "al-backend.service"),
    ("telegram", "AL Telegram Bot", "al-telegram-bot.service"),
    ("discord", "AL Discord Bot", "al-discord-bot.service"),
    ("mongo", "MongoDB", "mongod.service"),
    ("nginx", "Nginx", "nginx.service"),
)
OPENAI_STATS_CACHE_TTL_SECONDS = 300
OPENAI_STATS_USAGE_ENDPOINTS = ("completions", "audio_transcriptions")
OPENAI_STATS_HISTORY_START = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
OPENAI_STATS_ACCUMULATOR_KIND = "openai_stats_accumulator_v1"
OPENAI_STATS_RESPONSE_CACHE_KIND = "openai_stats_response_cache_v1"
OPENAI_STATS_LEGACY_CACHE_KIND = "openai_stats_cache"
OPENAI_STATS_SYNC_STALE_SECONDS = 3600
OPENAI_STATS_MONTH_REFRESH_SECONDS = 6 * 3600
OPENAI_STATS_MAX_DAILY_BUCKETS = 31
DEFAULT_FAKE_ONLINE_SETTINGS = {
    "enabled": False,
    "daysOfWeek": [],
    "startTime": "10:00",
    "endTime": "12:00",
    "delayMinSeconds": 5,
    "delayMaxSeconds": 60,
}
WEEKDAY_VALUES = set(range(7))


def _server_stats_category(key: str, path: Path) -> dict[str, Any]:
    labels = {
        "system": "System /usr",
        "var": "Variable /var",
        "app": "App /opt/al",
        "mongo": "MongoDB",
        "aptCache": "apt cache",
        "logs": "Logs",
    }
    exists = path.exists()
    size = _path_size_bytes(path) if exists else 0
    return {
        "key": key,
        "label": labels.get(key, key),
        "path": str(path),
        "bytes": size,
        "exists": exists,
    }


def _parse_fake_online_time(value: str) -> int:
    try:
        hour_raw, minute_raw = value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
    except (ValueError, AttributeError):
        raise ValueError("Time must use HH:mm format")

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must use HH:mm format")

    return hour * 60 + minute


def _fake_online_settings_row(profile: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    raw_author = str(profile.get("rawAuthor") or saved.get("rawAuthor") or "")
    telegram_username = _normalize_telegram_username(profile.get("telegramUsername") or saved.get("telegramUsername"))
    return {
        **DEFAULT_FAKE_ONLINE_SETTINGS,
        **{key: saved.get(key) for key in DEFAULT_FAKE_ONLINE_SETTINGS if key in saved},
        "rawAuthor": raw_author,
        "displayName": profile.get("displayName") or raw_author,
        "authorEmail": profile.get("authorEmail") or "",
        "telegramUsername": telegram_username,
        "timeZoneId": profile.get("timeZoneId") or saved.get("timeZoneId") or "UTC",
        "timeZoneDisplayName": profile.get("timeZoneDisplayName") or profile.get("timeZoneId") or saved.get("timeZoneId") or "UTC",
        "canEnable": bool(telegram_username),
    }


def _path_size_bytes(path: Path) -> int:
    du_size = _du_size_bytes(path)
    if du_size is not None:
        return du_size

    if path.is_file():
        return path.stat().st_size

    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue

    return total


def _du_size_bytes(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["sudo", "-n", "du", "-sb", "--", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    first_field = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
    try:
        return int(first_field)
    except ValueError:
        return None


def _server_stats_service(key: str, label: str, unit: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=LoadState",
                "--property=UnitFileState",
                "--property=ActiveEnterTimestamp",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return _unknown_server_stats_service(key, label, unit)

    values = _parse_systemctl_show(result.stdout)
    active_state = values.get("ActiveState") or "unknown"
    sub_state = values.get("SubState") or ""
    load_state = values.get("LoadState") or "unknown"
    unit_file_state = values.get("UnitFileState") or "unknown"
    active_entered_at = values.get("ActiveEnterTimestamp") or ""

    if active_state == "active":
        status = "running"
    elif active_state in {"inactive", "failed", "deactivating"}:
        status = "stopped"
    else:
        status = "unknown"

    return {
        "key": key,
        "label": label,
        "unit": unit,
        "status": status,
        "activeState": active_state or "unknown",
        "subState": sub_state or None,
        "loadState": load_state or "unknown",
        "unitFileState": unit_file_state or "unknown",
        "activeEnteredAt": active_entered_at or None,
    }


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values: dict[str, str] = {}

    for line in output.splitlines():
        key, separator, value = line.partition("=")

        if separator:
            values[key] = value.strip()

    return values


def _unknown_server_stats_service(key: str, label: str, unit: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "status": "unknown",
        "activeState": "unknown",
        "subState": None,
        "loadState": "unknown",
        "unitFileState": "unknown",
        "activeEnteredAt": None,
    }


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

        payload = _openai_get(
            api_key,
            "/v1/organization/costs",
            params,
        )

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

        payload = _openai_get(
            api_key,
            f"/v1/organization/usage/{endpoint}",
            params,
        )

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


class SettingsRepository(MongoComposableMixin):
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
            result = _fetch_openai_stats(
                composed(self).openai_usage_api_key,
                now,
                accumulator,
            )
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
        return {
            "kind": OPENAI_STATS_RESPONSE_CACHE_KIND,
            "cachedAt": legacy.get("cachedAt"),
            "stats": stats,
        }

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
        self.db.system_settings.update_one(
            {"kind": OPENAI_STATS_ACCUMULATOR_KIND},
            {"$set": sync_doc},
            upsert=True,
        )
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

    def get_interval_for_author(self, author: str, source: str | None = None) -> int:
        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if is_device_source(source) and global_setting and global_setting.get("deviceSendIntervalSeconds"):
            return int(global_setting["deviceSendIntervalSeconds"])

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return composed(self).default_send_interval_seconds

    def get_idle_threshold_for_author(self, author: str, source: str | None = None) -> int:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}

        if is_device_source(source) and global_setting.get("deviceIdleThresholdSeconds"):
            return int(global_setting["deviceIdleThresholdSeconds"])

        if global_setting.get("idleThresholdSeconds"):
            return int(global_setting["idleThresholdSeconds"])

        return DEFAULT_IDLE_THRESHOLD_SECONDS

    def get_plugin_ingest_enabled(self) -> bool:
        settings = self.db.system_settings.find_one({"kind": "plugins"}, {"_id": 0, "pluginIngestEnabled": 1}) or {}
        return settings.get("pluginIngestEnabled") is not False

    def is_plugin_enabled_for_author(self, author: str) -> bool:
        if not self.get_plugin_ingest_enabled():
            return False

        author = self.resolve_author_alias(_normalize_author(author))

        if composed(self).is_deleted_author_profile(author):
            return False

        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"pluginEnabled": 1})

        if profile and profile.get("pluginEnabled") is False:
            return False

        return True

    def get_effective_plugin_ingest_resume_cutoff_utc(self, author: str) -> dt.datetime | None:
        author = self.resolve_author_alias(_normalize_author(author))
        stamps: list[dt.datetime] = []
        plugins = self.db.system_settings.find_one({"kind": "plugins"}, {"_id": 0, "pluginIngestResumedAtUtc": 1}) or {}
        global_resume = _coerce_datetime(plugins.get("pluginIngestResumedAtUtc"))

        if global_resume is not None:
            stamps.append(global_resume)

        profile = self.db.author_profiles.find_one({"rawAuthor": author}, {"_id": 0, "pluginIngestResumedAtUtc": 1}) or {}
        author_resume = _coerce_datetime(profile.get("pluginIngestResumedAtUtc"))

        if author_resume is not None:
            stamps.append(author_resume)

        if not stamps:
            return None

        return max(stamps)

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

    def upsert_interval_settings(
        self,
        default_send_interval_seconds: int | None,
        device_send_interval_seconds: int | None = None,
        idle_threshold_seconds: int | None = None,
        device_idle_threshold_seconds: int | None = None,
        plugin_ingest_enabled: bool | None = None,
        telegram_online_prompt_delay_minutes: int | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        global_update: dict[str, Any] = {"updatedAt": now}

        if default_send_interval_seconds is not None:
            global_update["sendIntervalSeconds"] = default_send_interval_seconds

        if device_send_interval_seconds is not None:
            global_update["deviceSendIntervalSeconds"] = device_send_interval_seconds

        if idle_threshold_seconds is not None:
            global_update["idleThresholdSeconds"] = idle_threshold_seconds

        if device_idle_threshold_seconds is not None:
            global_update["deviceIdleThresholdSeconds"] = device_idle_threshold_seconds

        if telegram_online_prompt_delay_minutes is not None:
            clamped = max(
                1,
                min(int(telegram_online_prompt_delay_minutes), MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES),
            )
            global_update["telegramOnlinePromptDelayMinutes"] = clamped

        if len(global_update) > 1:
            self.db.interval_settings.update_one(
                {"kind": "global"},
                {"$set": global_update},
                upsert=True,
            )

        if plugin_ingest_enabled is not None:
            prev_plugin_enabled = self.get_plugin_ingest_enabled()
            plugin_fields: dict[str, Any] = {
                "kind": "plugins",
                "pluginIngestEnabled": plugin_ingest_enabled,
                "updatedAt": now,
            }

            if plugin_ingest_enabled and not prev_plugin_enabled:
                plugin_fields["pluginIngestResumedAtUtc"] = now

            self.db.system_settings.update_one(
                {"kind": "plugins"},
                {"$set": plugin_fields},
                upsert=True,
            )

        composed(self).invalidate_activity_summary_cache()
        return self.get_interval_settings()

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

    def get_telegram_online_prompt_delay_seconds(self) -> int:
        settings = self.get_interval_settings()
        return int(settings["telegramOnlinePromptDelayMinutes"]) * 60

    def fake_online_settings(self) -> dict[str, Any]:
        settings_by_author = {
            str(item.get("rawAuthor") or ""): item
            for item in self.db.fake_online_settings.find({}, {"_id": 0})
        }
        profiles_by_author = {str(profile.get("rawAuthor") or ""): profile for profile in self.author_profiles()}
        rows: list[dict[str, Any]] = []
        available_profiles: list[dict[str, Any]] = []

        for raw_author, saved in settings_by_author.items():
            profile = profiles_by_author.get(raw_author)

            if not profile:
                continue

            rows.append(_fake_online_settings_row(profile, saved))

        configured_authors = {row["rawAuthor"] for row in rows}
        for raw_author, profile in profiles_by_author.items():
            if raw_author in configured_authors:
                continue

            telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
            available_profiles.append(
                {
                    "rawAuthor": raw_author,
                    "displayName": profile.get("displayName") or raw_author,
                    "authorEmail": profile.get("authorEmail") or "",
                    "telegramUsername": telegram_username,
                    "canEnable": bool(telegram_username),
                }
            )

        return {"settings": rows, "availableProfiles": available_profiles}

    def _fake_online_default_row_for_author(self, raw_author: str) -> dict[str, Any] | None:
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0})

        if not profile:
            return None

        return _fake_online_settings_row(profile, {})

    def delete_fake_online_settings(self, raw_author: str) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)
        self.db.fake_online_settings.delete_one({"rawAuthor": normalized_author})
        self.db.fake_online_attempts.update_many(
            {"rawAuthor": normalized_author, "status": {"$in": ["pending", "claimed"]}},
            {
                "$set": {
                    "status": "closed",
                    "closeAction": "removed_from_fake_online",
                    "closedAt": dt.datetime.now(dt.UTC),
                    "updatedAt": dt.datetime.now(dt.UTC),
                }
            },
        )
        return {"ok": True, **self.fake_online_settings()}

    def upsert_fake_online_settings(
        self,
        raw_author: str,
        enabled: bool,
        days_of_week: list[int],
        start_time: str,
        end_time: str,
        delay_min_seconds: int,
        delay_max_seconds: int,
    ) -> dict[str, Any]:
        normalized_author = _normalize_author(raw_author)
        profile = self.db.author_profiles.find_one({"rawAuthor": normalized_author}, {"_id": 0})

        if not profile:
            return {"ok": False, "error": "Author profile not found"}

        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))
        if not telegram_username:
            return {"ok": False, "error": "Author profile must have a Telegram username"}

        normalized_days = sorted({int(day) for day in days_of_week if int(day) in WEEKDAY_VALUES})
        if enabled and not normalized_days:
            return {"ok": False, "error": "Select at least one weekday"}

        if _parse_fake_online_time(start_time) >= _parse_fake_online_time(end_time):
            return {"ok": False, "error": "Start time must be before end time"}

        min_delay = max(0, int(delay_min_seconds))
        max_delay = max(0, int(delay_max_seconds))
        if min_delay > max_delay:
            return {"ok": False, "error": "Minimum delay must be less than or equal to maximum delay"}

        now = dt.datetime.now(dt.UTC)
        self.db.fake_online_settings.update_one(
            {"rawAuthor": normalized_author},
            {
                "$set": {
                    "rawAuthor": normalized_author,
                    "enabled": bool(enabled),
                    "daysOfWeek": normalized_days,
                    "startTime": start_time,
                    "endTime": end_time,
                    "delayMinSeconds": min_delay,
                    "delayMaxSeconds": max_delay,
                    "telegramUsername": telegram_username,
                    "timeZoneId": profile.get("timeZoneId") or "UTC",
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        return {"ok": True, **self.fake_online_settings()}

    def claim_due_fake_online_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        due: list[dict[str, Any]] = []

        for setting in list(self.db.fake_online_settings.find({"enabled": True}, {"_id": 0})):
            attempt = self._ensure_today_fake_online_attempt(setting, now)
            if not attempt or attempt.get("status") != "pending":
                continue

            scheduled_at = _coerce_datetime(attempt.get("scheduledPromptAt"))
            if not scheduled_at or scheduled_at > now:
                continue

            raw_author = str(attempt.get("rawAuthor") or "")
            local_date = str(attempt.get("localDate") or "")
            if composed(self).should_suppress_vacation_prompt(raw_author, local_date):
                self.db.fake_online_attempts.update_one(
                    {"attemptId": attempt.get("attemptId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closeAction": "skipped_vacation_day",
                            "closedAt": now,
                            "updatedAt": now,
                        }
                    },
                )
                continue

            if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": local_date}, {"_id": 1}):
                self.db.fake_online_attempts.update_one(
                    {"attemptId": attempt.get("attemptId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closeAction": "skipped_day_already_open",
                            "closedAt": now,
                            "updatedAt": now,
                        }
                    },
                )
                continue

            prompt = self._create_fake_online_prompt_for_attempt(attempt, now)
            if not prompt:
                continue

            self.db.fake_online_attempts.update_one(
                {"attemptId": attempt.get("attemptId")},
                {
                    "$set": {
                        "status": "claimed",
                        "telegramPromptId": prompt["reminderId"],
                        "lastClaimedAt": now,
                        "updatedAt": now,
                    }
                },
            )
            due.append(prompt)

        return due

    def _ensure_today_fake_online_attempt(self, setting: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
        raw_author = str(setting.get("rawAuthor") or "")
        if not raw_author:
            return None

        profile = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author},
            {"_id": 0, "telegramUsername": 1, "timeZoneId": 1},
        ) or {}
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername") or setting.get("telegramUsername"))
        if not telegram_username:
            return None

        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId") or setting.get("timeZoneId")) or "UTC"
        local_now = now.astimezone(ZoneInfo(time_zone_id))
        local_date = local_now.date().isoformat()
        existing = self.db.fake_online_attempts.find_one({"rawAuthor": raw_author, "localDate": local_date}, {"_id": 0})
        if existing:
            return existing

        days = {int(day) for day in setting.get("daysOfWeek", []) if int(day) in WEEKDAY_VALUES}
        if local_now.weekday() not in days:
            return None

        if composed(self).should_suppress_vacation_prompt(raw_author, local_date):
            return None

        start_minutes = _parse_fake_online_time(str(setting.get("startTime") or DEFAULT_FAKE_ONLINE_SETTINGS["startTime"]))
        end_minutes = _parse_fake_online_time(str(setting.get("endTime") or DEFAULT_FAKE_ONLINE_SETTINGS["endTime"]))
        if start_minutes >= end_minutes:
            return None

        start_local = local_now.replace(hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0)
        end_local = local_now.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0)

        window_seconds = max(0, int((end_local - start_local).total_seconds()))
        scheduled_local = start_local + dt.timedelta(seconds=random.randint(0, window_seconds))
        delay_min = max(0, int(setting.get("delayMinSeconds", DEFAULT_FAKE_ONLINE_SETTINGS["delayMinSeconds"])))
        delay_max = max(delay_min, int(setting.get("delayMaxSeconds", DEFAULT_FAKE_ONLINE_SETTINGS["delayMaxSeconds"])))
        now_utc = dt.datetime.now(dt.UTC)
        attempt = {
            "attemptId": _new_id(),
            "rawAuthor": raw_author,
            "localDate": local_date,
            "telegramUsername": telegram_username,
            "timeZoneId": time_zone_id,
            "scheduledPromptAt": scheduled_local.astimezone(dt.UTC),
            "autoConfirmDelaySeconds": random.randint(delay_min, delay_max),
            "status": "pending",
            "createdAt": now_utc,
            "updatedAt": now_utc,
        }
        self.db.fake_online_attempts.insert_one(attempt)
        return {k: v for k, v in attempt.items() if k != "_id"}

    def _create_fake_online_prompt_for_attempt(self, attempt: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
        raw_author = str(attempt.get("rawAuthor") or "")
        local_date = str(attempt.get("localDate") or "")
        reminder_id = _new_id()
        first_report_received_at = _coerce_datetime(attempt.get("scheduledPromptAt")) + dt.timedelta(minutes=1)
        try:
            self.db.telegram_online_prompts.insert_one(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "date": local_date,
                    "telegramUsername": str(attempt.get("telegramUsername") or ""),
                    "firstReportReceivedAt": first_report_received_at,
                    "status": "claimed",
                    "source": "fake_online",
                    "fakeOnlineAttemptId": attempt.get("attemptId"),
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
        except DuplicateKeyError:
            self.db.fake_online_attempts.update_one(
                {"attemptId": attempt.get("attemptId")},
                {
                    "$set": {
                        "status": "closed",
                        "closeAction": "skipped_existing_online_prompt",
                        "closedAt": now,
                        "updatedAt": now,
                    }
                },
            )
            return None

        return {
            "reminderId": reminder_id,
            "rawAuthor": raw_author,
            "telegramUsername": str(attempt.get("telegramUsername") or ""),
            "date": local_date,
            "firstReportReceivedAt": first_report_received_at.isoformat(),
            "fakeOnlineAttemptId": str(attempt.get("attemptId") or ""),
            "autoConfirmDelaySeconds": int(attempt.get("autoConfirmDelaySeconds") or 0),
        }

    def get_interval_settings(self) -> dict[str, Any]:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
        raw_minutes = global_setting.get("telegramOnlinePromptDelayMinutes")

        if raw_minutes is None:
            telegram_online_prompt_delay_minutes = DEFAULT_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES
        else:
            telegram_online_prompt_delay_minutes = max(
                1,
                min(int(raw_minutes), MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES),
            )

        return {
            "defaultSendIntervalSeconds": int(
                global_setting.get("sendIntervalSeconds", composed(self).default_send_interval_seconds)
            ),
            "deviceSendIntervalSeconds": int(
                global_setting.get(
                    "deviceSendIntervalSeconds",
                    global_setting.get("sendIntervalSeconds", composed(self).default_send_interval_seconds),
                )
            ),
            "idleThresholdSeconds": int(global_setting.get("idleThresholdSeconds", DEFAULT_IDLE_THRESHOLD_SECONDS)),
            "deviceIdleThresholdSeconds": int(global_setting.get("deviceIdleThresholdSeconds", DEFAULT_IDLE_THRESHOLD_SECONDS)),
            "pluginIngestEnabled": self.get_plugin_ingest_enabled(),
            "telegramOnlinePromptDelayMinutes": telegram_online_prompt_delay_minutes,
            "avatarRefreshCadence": self.get_avatar_refresh_cadence(),
        }

    def get_server_stats(self) -> dict[str, Any]:
        usage = shutil.disk_usage("/")
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        percent = round((used / total) * 100, 1) if total else 0.0
        categories = [
            _server_stats_category(key, path)
            for key, path in SERVER_STATS_PATHS.items()
        ]
        known_bytes = sum(int(item["bytes"]) for item in categories)
        other_bytes = max(0, used - known_bytes)

        categories.append(
            {
                "key": "other",
                "label": "Other",
                "path": "/",
                "bytes": other_bytes,
                "exists": True,
            }
        )

        if percent >= 90:
            warning_level = "critical"
        elif percent >= 80:
            warning_level = "warning"
        else:
            warning_level = "ok"

        return {
            "generatedAt": dt.datetime.now(dt.UTC).isoformat(),
            "hostname": socket.gethostname(),
            "root": {
                "path": "/",
                "totalBytes": total,
                "usedBytes": used,
                "freeBytes": free,
                "usedPercent": percent,
                "warningLevel": warning_level,
            },
            "categories": categories,
            "services": [
                _server_stats_service(key, label, unit)
                for key, label, unit in SERVER_STATS_SERVICES
            ],
        }

    def reboot_server(self) -> dict[str, Any]:
        requested_at = dt.datetime.now(dt.UTC)
        services = ["mongod", "nginx", "al-backend", "al-telegram-bot", "al-discord-bot"]
        unit_name = f"al-dashboard-reboot-{requested_at.strftime('%Y%m%d%H%M%S')}"

        try:
            subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "/usr/bin/systemd-run",
                    "--unit",
                    unit_name,
                    "--on-active=2s",
                    "/usr/bin/systemctl",
                    "restart",
                    *services,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": str(exc), "requestedAt": requested_at.isoformat()}

        return {"ok": True, "status": "services_restart_scheduled", "requestedAt": requested_at.isoformat(), "services": services}

    def upsert_discord_settings(self, meeting_auto_afk_timeout_seconds: int) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        current = self.get_discord_settings()
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": current["meetingSummariesEnabled"],
                    "meetingSummaryMinParticipants": current["meetingSummaryMinParticipants"],
                    "meetingSummaryMinDurationSeconds": current["meetingSummaryMinDurationSeconds"],
                    "meetingSummaryLanguage": current["meetingSummaryLanguage"],
                    "meetingSummaryRecipient": current["meetingSummaryRecipient"],
                    "meetingAudioRetentionSeconds": current["meetingAudioRetentionSeconds"],
                    "meetingSummaryPrompt": current["meetingSummaryPrompt"],
                    "meetingSummaryTelegramTemplate": current["meetingSummaryTelegramTemplate"],
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()

    def upsert_discord_summary_settings(
        self,
        *,
        meeting_auto_afk_timeout_seconds: int,
        meeting_summaries_enabled: bool,
        meeting_summary_min_participants: int,
        meeting_summary_min_duration_seconds: int,
        meeting_summary_language: str,
        meeting_summary_recipient: str,
        meeting_audio_retention_seconds: int,
        meeting_summary_prompt: str,
        meeting_summary_telegram_template: str = "",
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.system_settings.update_one(
            {"kind": "discord"},
            {
                "$set": {
                    "kind": "discord",
                    "meetingAutoAfkTimeoutSeconds": meeting_auto_afk_timeout_seconds,
                    "meetingSummariesEnabled": meeting_summaries_enabled,
                    "meetingSummaryMinParticipants": meeting_summary_min_participants,
                    "meetingSummaryMinDurationSeconds": meeting_summary_min_duration_seconds,
                    "meetingSummaryLanguage": meeting_summary_language.strip() or "English",
                    "meetingSummaryRecipient": meeting_summary_recipient.strip() or "work_chat",
                    "meetingAudioRetentionSeconds": meeting_audio_retention_seconds,
                    "meetingSummaryPrompt": meeting_summary_prompt.strip() or DEFAULT_MEETING_SUMMARY_PROMPT,
                    "meetingSummaryTelegramTemplate": meeting_summary_telegram_template.strip() or DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )
        composed(self).invalidate_activity_summary_cache()
        return self.get_discord_settings()

    def get_discord_settings(self) -> dict[str, Any]:
        settings = self.db.system_settings.find_one({"kind": "discord"}) or {}
        return {
            "meetingAutoAfkTimeoutSeconds": int(
                settings.get("meetingAutoAfkTimeoutSeconds", DEFAULT_DISCORD_MEETING_AUTO_AFK_TIMEOUT_SECONDS)
            ),
            "meetingSummariesEnabled": bool(settings.get("meetingSummariesEnabled", False)),
            "meetingSummaryMinParticipants": int(settings.get("meetingSummaryMinParticipants", 2)),
            "meetingSummaryMinDurationSeconds": int(settings.get("meetingSummaryMinDurationSeconds", 120)),
            "meetingSummaryLanguage": str(settings.get("meetingSummaryLanguage") or "English"),
            "meetingSummaryRecipient": str(settings.get("meetingSummaryRecipient") or "work_chat"),
            "meetingAudioRetentionSeconds": int(settings.get("meetingAudioRetentionSeconds", 0)),
            "meetingSummaryPrompt": str(
                settings.get("meetingSummaryPrompt") or settings.get("meetingAudioEditPrompt") or DEFAULT_MEETING_SUMMARY_PROMPT
            ),
            "meetingSummaryTelegramTemplate": str(
                settings.get("meetingSummaryTelegramTemplate") or DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE
            ),
        }
