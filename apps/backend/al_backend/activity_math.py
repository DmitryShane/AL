from __future__ import annotations

import datetime as dt
import re
import unicodedata
import urllib.parse
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from .meeting_summary import DEFAULT_MEETING_SUMMARY_PROMPT, DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE
from .settings import Settings
from .auth import hash_password, new_session_token, session_token_hash, verify_password
from .hourly_fill_rules import add_interval_to_hourly, empty_hourly_activity, hourly_deltas, merge_hourly_activity, public_hourly_activity

LOW_PRODUCTIVITY_THRESHOLD = 50
LONG_BREAK_THRESHOLD_SECONDS = 3600
TELEGRAM_DAY_REMINDER_SECONDS = 10 * 3600
DEFAULT_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES = 15
MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES = 24 * 60
TELEGRAM_ONLINE_PROMPT_DELAY_SECONDS = DEFAULT_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES * 60
TELEGRAM_BREAK_ACTIVITY_PROMPT_DELAY_SECONDS = 60 * 60
DEFAULT_DISCORD_MEETING_AUTO_AFK_TIMEOUT_SECONDS = 10 * 60
DEFAULT_PLUGIN_WORK_WINDOW_SECONDS = 32400
DEFAULT_IDLE_THRESHOLD_SECONDS = 300
AUTO_BREAK_SECONDS = 60 * 60
MICROSECONDS_PER_SECOND = 1_000_000
MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS = 10
# When a heartbeat is ingested far after its occurred time (buffered uploads / offline queues),
# cap heartbeat idle attribution so delivery lag does not create multi-hour idle in one bucket.
MAX_STALE_HEARTBEAT_IDLE_MULTIPLIER = 2

# Require receivedAt skew vs occurredAtUtc before treating a heartbeat as "stale" for capping.
STALE_HEARTBEAT_RECEIVE_SKEW_MULTIPLIER = 2

# Minimal skew (seconds) before capping stale heartbeats, even if idle_threshold is very small.
STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR = 120
SELECT_HEAVY_THRESHOLD_PERCENT = 90
SELECT_HEAVY_MIN_EVENTS = 20
AFK_IDLE_ARTIFACT_THRESHOLD_SECONDS = 300
REPORT_CHALLENGE_TTL_SECONDS = 120
DEVICE_SOURCES = {"dev", "dev-ios", "dev-android"}
UAL_SAVED_FILE_EXTENSIONS = {
    ".unity",
    ".prefab",
    ".asset",
    ".mat",
    ".controller",
    ".anim",
    ".shader",
    ".cs",
    ".fbx",
    ".png",
    ".jpg",
    ".jpeg",
    ".tga",
    ".psd",
    ".exr",
}
UAL_IGNORED_SAVED_FILE_PATHS = {
    "packages/com.mempic.ad.provider/runtime/textures/texture.asset",
}
RAW_ACTIVITY_EVENT_TYPES = {
    "click",
    "hold",
    "selection",
    "select",
    "scene_saved",
    "asset_saved",
    "prefab_saved",
    "undo_redo",
    "play_mode",
    "scene_view_navigation",
    "editor_input",
    "scene_object_created",
    "scene_object_destroyed",
    "scene_object_changed",
    "scene_changed",
    "file_saved",
    "file_loaded",
    "external",
}
CODEX_ACTIVITY_EVENT_TYPES = {
    "session_started",
    "task_progress",
    "command_run",
    "file_changed",
    "session_finished",
}


def is_device_source(source: Any) -> bool:
    return str(source or "").strip().lower() in DEVICE_SOURCES


def device_source_from_payload(payload: dict[str, Any], fallback: str = "dev") -> str:
    metadata = _latest_payload_event_metadata(payload)
    platform = str(metadata.get("platform") or metadata.get("runtimePlatform") or "").strip().lower()

    if "iphone" in platform or "ipad" in platform or "ios" in platform:
        return "dev-ios"

    if "android" in platform:
        return "dev-android"

    return fallback if is_device_source(fallback) else "dev"


def _latest_payload_event_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events")

    if not isinstance(events, list):
        return {}

    latest_event = None

    for event in events:
        if not isinstance(event, dict):
            continue

        if latest_event is None:
            latest_event = event
            continue

        current_time = str(event.get("occurredAtUtc") or event.get("occurredAtLocal") or "")
        latest_time = str(latest_event.get("occurredAtUtc") or latest_event.get("occurredAtLocal") or "")

        if current_time >= latest_time:
            latest_event = event

    metadata = (latest_event or {}).get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _metadata_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}

    return bool(value)
NON_ACTIVITY_EVENT_TYPES = {"heartbeat", "blur"}
DEFAULT_CALENDAR_REASONS = [
    {"id": "vacation", "label": "Vacation"},
    {"id": "day_off", "label": "Day off"},
    {"id": "absence", "label": "Absence"},
]
AUTHOR_COLORS = ["#13a37b", "#5b4dff", "#f59e0b", "#dc2626", "#0ea5e9", "#a855f7", "#14b8a6", "#ef4444"]
AUTHOR_TIME_ZONE_IDS = {
    "Denis Ostrovskiy": "Europe/Kyiv",
    "Евгений Доценко": "Europe/Sofia",
}
WINDOWS_TIME_ZONE_IDS = {
    "FLE Standard Time": "Europe/Sofia",
    "FLE Daylight Time": "Europe/Sofia",
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _next_author_local_date(time_zone_id: Any) -> str:
    normalized_time_zone = _valid_time_zone_id(time_zone_id) or "UTC"
    return (dt.datetime.now(ZoneInfo(normalized_time_zone)).date() + dt.timedelta(days=1)).isoformat()


def _empty_event_deltas() -> dict[str, Any]:
    return {
        "activeDeltaSeconds": 0,
        "idleDeltaSeconds": 0,
        "breakDeltaSeconds": 0,
        "overtimeActiveDeltaSeconds": 0,
        "activeDeltaMicroseconds": 0,
        "idleDeltaMicroseconds": 0,
        "breakDeltaMicroseconds": 0,
        "overtimeActiveDeltaMicroseconds": 0,
        "autoBreakDeltaSeconds": 0,
        "activityCountDeltas": [],
        "savedPrefabDeltas": [],
        "overtimeActivityCountDeltas": [],
        "overtimeSavedPrefabDeltas": [],
        "hourlyActivityDelta": empty_hourly_activity(),
    }


def _empty_batch_deltas() -> dict[str, Any]:
    return _empty_event_deltas()


def _merge_batch_deltas(target: dict[str, Any], source: dict[str, Any]) -> None:
    active_microseconds = _time_microseconds(target, "activeDeltaSeconds", "activeDeltaMicroseconds") + _time_microseconds(
        source, "activeDeltaSeconds", "activeDeltaMicroseconds"
    )
    idle_microseconds = _time_microseconds(target, "idleDeltaSeconds", "idleDeltaMicroseconds") + _time_microseconds(
        source, "idleDeltaSeconds", "idleDeltaMicroseconds"
    )
    overtime_active_microseconds = _time_microseconds(
        target, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds"
    ) + _time_microseconds(source, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds")
    break_microseconds = _time_microseconds(target, "breakDeltaSeconds", "breakDeltaMicroseconds") + _time_microseconds(
        source, "breakDeltaSeconds", "breakDeltaMicroseconds"
    )
    target["activeDeltaMicroseconds"] = active_microseconds
    target["idleDeltaMicroseconds"] = idle_microseconds
    target["breakDeltaMicroseconds"] = break_microseconds
    target["overtimeActiveDeltaMicroseconds"] = overtime_active_microseconds
    target["activeDeltaSeconds"] = _seconds_from_microseconds(active_microseconds)
    target["idleDeltaSeconds"] = _seconds_from_microseconds(idle_microseconds)
    target["breakDeltaSeconds"] = _seconds_from_microseconds(break_microseconds)
    target["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(overtime_active_microseconds)
    target["autoBreakDeltaSeconds"] = int(target.get("autoBreakDeltaSeconds", 0)) + int(source.get("autoBreakDeltaSeconds", 0))
    merge_hourly_activity(target["hourlyActivityDelta"], source.get("hourlyActivityDelta", []))
    target["activityCountDeltas"] = _merge_count_list(
        target.get("activityCountDeltas", []), source.get("activityCountDeltas", []), "type", "count"
    )
    target["savedPrefabDeltas"] = _merge_count_list(
        target.get("savedPrefabDeltas", []), source.get("savedPrefabDeltas", []), "path", "saveCount"
    )
    target["overtimeActivityCountDeltas"] = _merge_count_list(
        target.get("overtimeActivityCountDeltas", []), source.get("overtimeActivityCountDeltas", []), "type", "count"
    )
    target["overtimeSavedPrefabDeltas"] = _merge_count_list(
        target.get("overtimeSavedPrefabDeltas", []), source.get("overtimeSavedPrefabDeltas", []), "path", "saveCount"
    )


def _saved_prefabs_for_summary_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    saved_prefabs = [dict(prefab) for prefab in item.get("savedPrefabs", [])]

    if _device_activity_is_overtime_only(item):
        return saved_prefabs

    if is_device_source(item.get("source")):
        device_prefab = _device_activity_prefab_item(item, item.get("activityCounts", []))

        if device_prefab and not any(prefab.get("path") == device_prefab["path"] for prefab in saved_prefabs):
            saved_prefabs.append(device_prefab)

        return saved_prefabs

    editor_project_item = _editor_project_saved_prefab_item(item)

    if not editor_project_item:
        return saved_prefabs

    if any(prefab.get("path") == editor_project_item["path"] for prefab in saved_prefabs):
        return saved_prefabs

    saved_prefabs.append(editor_project_item)
    return saved_prefabs


def _editor_project_saved_prefab_item(item: dict[str, Any], counts: Any | None = None) -> dict[str, Any] | None:
    source = str(item.get("source") or "")

    if source not in {"codex", "cur"}:
        return None

    project_id = str(item.get("projectId") or "")

    if not project_id:
        return None

    path_prefix = "codex" if source == "codex" else "cursor"
    activity_count = sum(int(count.get("count", 0)) for count in (counts if counts is not None else item.get("activityCounts", [])))

    if activity_count <= 0:
        return None

    return {
        "path": f"{path_prefix}:{project_id}",
        "name": project_id,
        "projectId": project_id,
        "saveCount": activity_count,
    }


def _overtime_saved_prefabs_for_summary_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    saved_prefabs = [dict(prefab) for prefab in item.get("overtimeSavedPrefabs", [])]

    editor_project_item = _editor_project_saved_prefab_item(item, item.get("overtimeActivityCounts", []))

    if editor_project_item and not any(prefab.get("path") == editor_project_item["path"] for prefab in saved_prefabs):
        saved_prefabs.append(editor_project_item)

    if not is_device_source(item.get("source")):
        return saved_prefabs

    device_prefab = _device_activity_prefab_item(item, item.get("overtimeActivityCounts", []))

    if device_prefab and not any(prefab.get("path") == device_prefab["path"] for prefab in saved_prefabs):
        saved_prefabs.append(device_prefab)

    return saved_prefabs


def _device_activity_prefab_item(item: dict[str, Any], counts: Any) -> dict[str, Any] | None:
    app_name = _device_activity_app_name(item)

    if not app_name:
        return None

    activity_count = sum(int(count.get("count", 0)) for count in counts or [])

    if activity_count <= 0:
        return None

    return {
        "path": f"device:{app_name.lower()}",
        "name": app_name,
        "projectId": str(item.get("projectId") or ""),
        "saveCount": max(1, activity_count),
    }


def _device_activity_is_overtime_only(item: dict[str, Any]) -> bool:
    if not is_device_source(item.get("source")):
        return False

    normal_count = sum(int(count.get("count", 0)) for count in item.get("activityCounts", []))
    overtime_count = sum(int(count.get("count", 0)) for count in item.get("overtimeActivityCounts", []))
    return normal_count <= 0 and overtime_count > 0


def _device_activity_app_name(item: dict[str, Any]) -> str:
    project_id = str(item.get("projectId") or "").strip()

    if project_id:
        return project_id

    metadata = item.get("metadata") or {}
    app_name = str(metadata.get("applicationName") or metadata.get("appName") or "").strip()

    if app_name:
        return app_name

    return ""


def _has_time_delta(deltas: dict[str, Any]) -> bool:
    return (
        _time_microseconds(deltas, "activeDeltaSeconds", "activeDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "idleDeltaSeconds", "idleDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "breakDeltaSeconds", "breakDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0
    )


def _has_active_or_overtime_delta(deltas: dict[str, Any]) -> bool:
    return (
        _time_microseconds(deltas, "activeDeltaSeconds", "activeDeltaMicroseconds") > 0
        or _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0
    )


def _normalize_raw_event(
    raw_event: dict[str, Any],
    source: str,
    plugin_version: str,
    author: str,
    author_email: str,
    project_id: str,
    session_id: str,
    device_id: str,
    batch_id: str,
    raw_report_id: Any,
    received_at: dt.datetime,
    report_type: str,
    time_zone_id: Any,
    time_zone_display_name: Any,
) -> dict[str, Any] | None:
    event_type = str(raw_event.get("eventType") or "").strip()

    if not event_type:
        return None

    occurred_at = _coerce_datetime(raw_event.get("occurredAtUtc") or raw_event.get("occurredAtLocal"))

    if not occurred_at:
        occurred_at = received_at

    occurred_local = str(raw_event.get("occurredAtLocal") or occurred_at.isoformat())
    event_id = str(raw_event.get("eventId") or _new_id())
    date = str(raw_event.get("date") or occurred_local[:10] or occurred_at.date().isoformat())
    return {
        "eventId": event_id,
        "eventType": event_type,
        "source": source,
        "pluginVersion": plugin_version,
        "author": author,
        "authorEmail": author_email,
        "projectId": project_id,
        "sessionId": session_id,
        "deviceId": device_id,
        "batchId": batch_id,
        "rawReportId": raw_report_id,
        "reportType": report_type,
        "occurredAtUtc": occurred_at,
        "occurredAtLocal": occurred_local,
        "date": date,
        "metadata": raw_event.get("metadata") or {},
        "timeZoneId": time_zone_id,
        "timeZoneDisplayName": time_zone_display_name,
        "receivedAt": received_at,
    }


def _raw_event_session_key(event: dict[str, Any]) -> str:
    return "|".join(
        [
            "author_source_project_device_day_v2",
            str(event.get("author") or "Unknown User"),
            str(event.get("date") or ""),
            str(event.get("source") or ""),
            str(event.get("projectId") or ""),
            str(event.get("deviceId") or ""),
        ]
    )


def _raw_event_author_day_key(event: dict[str, Any]) -> str:
    return "|".join(
        [
            "author_day_activity_v1",
            str(event.get("author") or "Unknown User"),
            str(event.get("date") or ""),
        ]
    )


def _raw_event_activity_scope(event: dict[str, Any]) -> str:
    return "|".join(
        [
            str(event.get("source") or ""),
            str(event.get("projectId") or ""),
            str(event.get("deviceId") or ""),
        ]
    )


def _merge_event_delta_items(
    items: list[tuple[dict[str, Any], dict[str, Any]]], cutoff: dt.datetime | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    batch_deltas = _empty_batch_deltas()
    last_event: dict[str, Any] | None = None

    for event, deltas in items:
        event_time = _raw_event_time(event)

        if cutoff and event_time and event_time <= cutoff:
            continue

        _merge_batch_deltas(batch_deltas, deltas)
        last_event = event

    return batch_deltas, last_event


def _merge_event_delta_items_by_date(
    items: list[tuple[dict[str, Any], dict[str, Any]]], cutoff: dt.datetime | None
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    merged_by_date: dict[str, dict[str, Any]] = {}
    last_event_by_date: dict[str, dict[str, Any]] = {}

    for event, deltas in items:
        event_time = _raw_event_time(event)

        if cutoff and event_time and event_time <= cutoff:
            continue

        event_date = str(event.get("date") or "")

        if not event_date:
            continue

        batch_deltas = merged_by_date.setdefault(event_date, _empty_batch_deltas())
        _merge_batch_deltas(batch_deltas, deltas)
        last_event_by_date[event_date] = event

    return [
        (batch_deltas, last_event_by_date[event_date])
        for event_date, batch_deltas in merged_by_date.items()
        if event_date in last_event_by_date
    ]


def _raw_event_time(event: dict[str, Any]) -> dt.datetime | None:
    return _coerce_datetime(event.get("occurredAtUtc")) or _parse_local_datetime(event.get("occurredAtLocal"))


def _report_row_time(row: dict[str, Any]) -> dt.datetime | None:
    return (
        _coerce_datetime(row.get("lastRecordedAt"))
        or _coerce_datetime(row.get("recordedAt"))
        or _coerce_datetime(row.get("receivedAt"))
    )


def _normalize_report_hour_filter(value: int | None) -> int | None:
    if value is None:
        return None

    return min(23, max(0, int(value)))


def _report_matches_hour_filter(
    report: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    hour: int | None,
) -> bool:
    if hour is None:
        return True

    recorded_at = _coerce_datetime(report.get("recordedAt") or report.get("lastRecordedAt") or report.get("receivedAt"))

    if not recorded_at:
        return False

    raw_author = str(report.get("author") or "Unknown User")
    time_zone_id = _author_time_zone_id(raw_author, profiles, report.get("timeZoneId"))
    report_hour = _to_local_datetime(recorded_at, time_zone_id).hour
    return report_hour == hour


def _report_sort_datetime(report: dict[str, Any]) -> dt.datetime | None:
    return (
        _coerce_datetime(report.get("recordedAt"))
        or _coerce_datetime(report.get("lastRecordedAt"))
        or _coerce_datetime(report.get("receivedAt"))
        or _coerce_datetime(report.get("lastReceivedAt"))
    )


def _report_table_sort_key(
    report: dict[str, Any],
    status_intervals: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime | None]]] | None = None,
) -> tuple[dt.datetime, int, int, dt.datetime]:
    sort_at = _report_sort_datetime(report) or dt.datetime.min.replace(tzinfo=dt.UTC)
    recorded_at = (
        _coerce_datetime(report.get("recordedAt"))
        or _coerce_datetime(report.get("lastRecordedAt"))
        or dt.datetime.min.replace(tzinfo=dt.UTC)
    )
    is_status = report.get("source") == "status" or report.get("reportType") == "status"
    event_type = str(report.get("statusEventType") or report.get("activityType") or "")
    status_priority = 3
    day_start_priority = 1

    if report.get("source") == "telegram" and report.get("telegramEventType") == "online":
        day_start_priority = 0

    if is_status:
        status_priority = 1

    if is_status and event_type == "online":
        reason = str(report.get("statusReason") or (report.get("metadata") or {}).get("reason") or "")
        status_priority = 0 if reason == "reports_resumed" else 2

    if is_status and event_type == "offline" and status_intervals:
        raw_author = str(report.get("author") or "Unknown User")
        report_date = str(report.get("date") or "")

        for opened_at, closed_at in status_intervals.get((raw_author, report_date), []):
            if closed_at and opened_at == sort_at:
                return closed_at, 1, day_start_priority, recorded_at

    return sort_at, status_priority, day_start_priority, recorded_at


def _is_activity_event(event: dict[str, Any] | str) -> bool:
    if isinstance(event, dict):
        event_type = str(event.get("eventType") or "")

        if event.get("source") == "codex" and event_type in CODEX_ACTIVITY_EVENT_TYPES:
            return True

        if event.get("source") == "cur" and event_type == "focus":
            return True

        if event.get("source") == "bal" and event_type == "scene_changed":
            metadata = event.get("metadata") or {}
            return bool(metadata.get("inputType") or metadata.get("changeType") == "object_update")
    else:
        event_type = event

    return event_type in RAW_ACTIVITY_EVENT_TYPES and event_type not in NON_ACTIVITY_EVENT_TYPES


def _activity_count_type(event_type: str) -> str:
    if event_type == "selection":
        return "select"

    if event_type == "scene_object_created":
        return "object_created"

    if event_type == "scene_object_destroyed":
        return "object_destroyed"

    if event_type == "scene_object_changed":
        return "object_changed"

    return event_type


def _saved_prefab_delta(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("eventType") or "")

    if event_type not in {"prefab_saved", "asset_saved", "scene_saved", "file_saved"}:
        return None

    metadata = event.get("metadata") or {}
    path = str(metadata.get("path") or "")

    if not path:
        return None

    if event.get("source") == "ual":
        if event_type == "asset_saved":
            return None

        path = _normalize_unity_saved_file_path(path)

        if not path:
            return None

    lower_path = path.lower()

    if event.get("source") == "ual":
        if lower_path in UAL_IGNORED_SAVED_FILE_PATHS:
            return None

        if event_type == "prefab_saved" and not lower_path.endswith(".prefab"):
            return None
        if event_type == "scene_saved" and not lower_path.endswith(".unity"):
            return None
        if event_type == "asset_saved" and not any(lower_path.endswith(extension) for extension in UAL_SAVED_FILE_EXTENSIONS):
            return None
        if event_type == "file_saved":
            return None
    elif event_type in {"prefab_saved", "asset_saved"} and not lower_path.endswith(".prefab"):
        return None
    elif event_type == "scene_saved":
        return None

    if event_type == "file_saved" and event.get("source") == "fch":
        if "figma.com/" not in lower_path and not metadata.get("fileKey"):
            return None
    elif event_type == "file_saved" and event.get("source") in {"cur", "vsc"}:
        pass
    elif event_type == "file_saved" and not lower_path.endswith(".blend"):
        return None

    name = str(metadata.get("name") or path.rsplit("/", 1)[-1])

    if event.get("source") == "ual" and name.lower().endswith(".meta"):
        name = name[: -len(".meta")]

    saved_file = {"path": path, "name": name, "saveCount": 1}

    if event.get("source") == "cur":
        saved_file["projectId"] = str(event.get("projectId") or "")

    return saved_file


def _normalize_unity_saved_file_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()

    if normalized.lower().endswith(".meta"):
        normalized = normalized[: -len(".meta")]

    if not normalized.lower().startswith("assets/"):
        return ""

    return normalized


def _worked_file_delta(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("eventType") or "")
    source = str(event.get("source") or "")

    if source not in {"fch", "fig"}:
        return None

    if event_type in {"prefab_saved", "asset_saved", "file_saved"}:
        return None

    if not _is_activity_event(event):
        return None

    metadata = event.get("metadata") or {}
    path = str(metadata.get("path") or metadata.get("url") or "")

    if not path:
        return None

    lower_path = path.lower()

    if source == "fch" and "figma.com/" not in lower_path and not metadata.get("fileKey"):
        return None

    name = str(metadata.get("name") or path.rsplit("/", 1)[-1])
    return {"path": path, "name": name, "saveCount": 1}


def _is_overtime_event_delta(
    consumed_normal_microseconds: int,
    deltas: dict[str, Any],
    overtime_window: tuple[dt.datetime, dt.datetime] | None = None,
) -> bool:
    work_window_microseconds = DEFAULT_PLUGIN_WORK_WINDOW_SECONDS * MICROSECONDS_PER_SECOND
    return (
        overtime_window is not None
        and consumed_normal_microseconds >= work_window_microseconds
        or _time_microseconds(deltas, "overtimeActiveDeltaSeconds", "overtimeActiveDeltaMicroseconds") > 0
    )


def _interval_deltas(
    start: dt.datetime,
    end: dt.datetime,
    local_start: dt.datetime,
    local_end: dt.datetime,
    is_active: bool,
    consumed_normal_microseconds: int,
    overtime_window: tuple[dt.datetime, dt.datetime] | None = None,
    count_idle_as_overtime: bool = False,
) -> dict[str, Any]:
    deltas = _empty_event_deltas()

    if end <= start:
        return deltas

    interval_microseconds = _duration_microseconds(start, end)

    if not is_active and overtime_window:
        overtime_start, _ = overtime_window
        idle_end = min(end, overtime_start)

        if idle_end > start:
            idle_microseconds = _duration_microseconds(start, idle_end)
            local_idle_end = local_start + (idle_end - start)
            deltas["idleDeltaMicroseconds"] += idle_microseconds
            deltas["idleDeltaSeconds"] = _seconds_from_microseconds(deltas["idleDeltaMicroseconds"])
            add_interval_to_hourly(deltas["hourlyActivityDelta"], local_start, local_idle_end, "idle")

        if count_idle_as_overtime:
            overtime_end = min(end, overtime_window[1])
            overtime_segment_start = max(start, overtime_start)

            if overtime_end > overtime_segment_start:
                overtime_microseconds = _duration_microseconds(overtime_segment_start, overtime_end)
                local_overtime_start = local_start + (overtime_segment_start - start)
                local_overtime_end = local_start + (overtime_end - start)
                deltas["overtimeActiveDeltaMicroseconds"] += overtime_microseconds
                deltas["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(deltas["overtimeActiveDeltaMicroseconds"])
                add_interval_to_hourly(deltas["hourlyActivityDelta"], local_overtime_start, local_overtime_end, "overtime")

        return deltas

    if not is_active or not overtime_window:
        bucket = "active" if is_active else "idle"
        delta_microseconds_key = "activeDeltaMicroseconds" if is_active else "idleDeltaMicroseconds"
        delta_seconds_key = "activeDeltaSeconds" if is_active else "idleDeltaSeconds"
        deltas[delta_microseconds_key] += interval_microseconds
        deltas[delta_seconds_key] = _seconds_from_microseconds(deltas[delta_microseconds_key])
        add_interval_to_hourly(deltas["hourlyActivityDelta"], local_start, local_end, bucket)
        return deltas

    overtime_start, overtime_end = overtime_window
    normal_end = min(end, overtime_start)

    if normal_end > start:
        normal_microseconds = _duration_microseconds(start, normal_end)
        local_normal_end = local_start + (normal_end - start)
        deltas["activeDeltaMicroseconds"] += normal_microseconds
        deltas["activeDeltaSeconds"] = _seconds_from_microseconds(deltas["activeDeltaMicroseconds"])
        add_interval_to_hourly(deltas["hourlyActivityDelta"], local_start, local_normal_end, "active")

    overtime_segment_start = max(start, overtime_start)
    overtime_segment_end = min(end, overtime_end)

    if overtime_segment_end > overtime_segment_start:
        overtime_microseconds = _duration_microseconds(overtime_segment_start, overtime_segment_end)
        local_overtime_start = local_start + (overtime_segment_start - start)
        local_overtime_end = local_start + (overtime_segment_end - start)
        deltas["overtimeActiveDeltaMicroseconds"] += overtime_microseconds
        deltas["overtimeActiveDeltaSeconds"] = _seconds_from_microseconds(deltas["overtimeActiveDeltaMicroseconds"])
        add_interval_to_hourly(deltas["hourlyActivityDelta"], local_overtime_start, local_overtime_end, "overtime")

    if end > overtime_end:
        local_after_overtime_start = local_start + (overtime_end - start)
        after_overtime_microseconds = _duration_microseconds(overtime_end, end)
        deltas["activeDeltaMicroseconds"] += after_overtime_microseconds
        deltas["activeDeltaSeconds"] = _seconds_from_microseconds(deltas["activeDeltaMicroseconds"])
        add_interval_to_hourly(deltas["hourlyActivityDelta"], local_after_overtime_start, local_end, "active")

    return deltas


def _move_hourly_idle_to_break(hourly_activity: list[dict[str, Any]], transfer_seconds: int) -> None:
    remaining_seconds = max(0, transfer_seconds)

    for item in sorted(hourly_activity, key=lambda value: int(value.get("hour", 0))):
        if remaining_seconds <= 0:
            return

        idle_seconds = int(item.get("idleSeconds", 0))

        if idle_seconds <= 0:
            continue

        moved_seconds = min(idle_seconds, remaining_seconds)
        idle_microseconds = max(
            0,
            _time_microseconds(item, "idleSeconds", "idleMicroseconds") - (moved_seconds * MICROSECONDS_PER_SECOND),
        )
        item["idleMicroseconds"] = idle_microseconds
        item["idleSeconds"] = _seconds_from_microseconds(idle_microseconds)
        item["breakSeconds"] = int(item.get("breakSeconds", 0)) + moved_seconds
        remaining_seconds -= moved_seconds


def _iso(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()

    return value


def _looks_like_missing_transcript_summary(summary: str) -> bool:
    normalized = summary.lower()
    markers = [
        "cannot provide a summary",
        "without a specific transcript",
        "please share the transcript",
        "no transcript",
    ]
    return any(marker in normalized for marker in markers)


def _meeting_audio_quality_status(audio_stats: dict[str, Any]) -> str:
    frame_count = int(audio_stats.get("audioFrameCount") or 0)

    if frame_count <= 0:
        return "unknown"

    if int(audio_stats.get("nonSilentFrameCount") or 0) <= 0:
        return "silent"

    corrupted_ratio = int(audio_stats.get("corruptedPacketCount") or 0) / frame_count

    if corrupted_ratio >= 0.2:
        return "corrupted"

    if corrupted_ratio >= 0.05 or int(audio_stats.get("listenErrorCount") or 0) > 0:
        return "degraded"

    return "ok"


def _looks_like_no_work_content_summary(summary: str) -> bool:
    normalized_lines = [line.strip().strip("-*").strip().lower() for line in summary.splitlines()]
    content_lines = [line for line in normalized_lines if line]
    if not content_lines:
        return True

    empty_markers = {"none", "нет", "n/a", "not applicable"}
    optional_prefixes = ("participants:", "участники:")
    content_section_prefixes = {
        "discussed:",
        "decisions:",
        "action items:",
        "open questions:",
        "обсудили:",
        "решения:",
        "задачи:",
        "открытые вопросы:",
    }

    found_empty_content_section = False
    found_meaningful_content_section = False
    current_section = ""

    for line in content_lines:
        if line.startswith(optional_prefixes):
            current_section = "participants"
            continue

        matched_content_section = False
        for prefix in content_section_prefixes:
            if line.startswith(prefix):
                matched_content_section = True
                current_section = "content"
                value = line.removeprefix(prefix).strip()
                if not value or value in empty_markers:
                    found_empty_content_section = True
                else:
                    found_meaningful_content_section = True
                break

        if matched_content_section:
            continue

        if line in empty_markers:
            found_empty_content_section = True
            continue

        if current_section == "content":
            found_meaningful_content_section = True

    return found_empty_content_section and not found_meaningful_content_section


def _date_query(start_date: str | None, end_date: str | None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    date_filter: dict[str, str] = {}

    if start_date:
        date_filter["$gte"] = start_date

    if end_date:
        date_filter["$lte"] = end_date

    if date_filter:
        query["date"] = date_filter

    return query


def _report_date_query(
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
    profiles: dict[str, dict[str, Any]],
    now: dt.datetime,
) -> dict[str, Any]:
    if date_mode == "authorLocalToday":
        dates = _live_scope_candidate_dates(start_date, end_date, profiles, now)

        if dates:
            return {"date": {"$in": dates}}

    return _date_query(start_date, end_date)


def _meeting_interval_date_query(
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
    profiles: dict[str, dict[str, Any]],
    now: dt.datetime,
) -> dict[str, Any]:
    query = _report_date_query(start_date, end_date, date_mode, profiles, now)
    date_filter = query.get("date")
    dates: set[str] = set()

    if isinstance(date_filter, dict) and "$in" in date_filter:
        dates.update(str(value) for value in date_filter["$in"])
    elif isinstance(date_filter, dict):
        range_start = str(date_filter.get("$gte") or start_date or "")
        range_end = str(date_filter.get("$lte") or end_date or range_start)
        dates.update(_date_values_between(range_start, range_end))
    elif isinstance(date_filter, str):
        dates.add(date_filter)
    else:
        dates.update(_date_values_between(start_date, end_date))

    expanded_dates = set(dates)

    for value in dates:
        expanded_dates.add((_date_start(value) - dt.timedelta(days=1)).date().isoformat())

    return {"date": {"$in": sorted(expanded_dates)}} if expanded_dates else query


def _meeting_interval_scope_dates(
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
    now: dt.datetime,
    time_zone_id: str,
) -> list[str]:
    if date_mode == "authorLocalToday":
        return [_local_date_for_time_zone(now, time_zone_id)]

    return _date_values_between(start_date, end_date)


def _date_values_between(start_date: str | None, end_date: str | None) -> list[str]:
    if not start_date and not end_date:
        return []

    range_start = _date_start(start_date or end_date or "").date()
    range_end = _date_start(end_date or start_date or "").date()

    if range_end < range_start:
        return []

    dates = []
    current = range_start

    while current <= range_end:
        dates.append(current.isoformat())
        current += dt.timedelta(days=1)

    return dates


def _document_identity_query(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("_id") is not None:
        return {"_id": item["_id"]}

    return dict(item)


def _is_author_local_today(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
) -> bool:
    if not value:
        return False

    return str(value) == _local_date_for_time_zone(
        now, _author_time_zone_id(raw_author, profiles, fallback_time_zone_id)
    )


def _date_in_summary_scope(
    value: str,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
) -> bool:
    if date_mode == "authorLocalToday":
        return live_date_in_scope(value, raw_author, profiles, fallback_time_zone_id, now, start_date, end_date)

    return _date_in_range(value, start_date, end_date)


def live_date_in_scope(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: dt.datetime,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    date_value = str(value or "")

    if not date_value:
        return False

    observer_dates = _live_observer_dates(start_date, end_date, now)

    if date_value in observer_dates:
        return True

    observer_latest_date = max(observer_dates)
    author_local_today = _local_date_for_time_zone(now, _author_time_zone_id(raw_author, profiles, fallback_time_zone_id))
    return date_value == author_local_today and author_local_today <= observer_latest_date


def _live_scope_candidate_dates(
    start_date: str | None,
    end_date: str | None,
    profiles: dict[str, dict[str, Any]],
    now: dt.datetime,
) -> list[str]:
    dates = set(_live_observer_dates(start_date, end_date, now))
    observer_latest_date = max(dates)

    for profile in profiles.values():
        author_local_today = _local_date_for_time_zone(now, _author_time_zone_id(profile.get("rawAuthor"), profiles))

        if author_local_today <= observer_latest_date:
            dates.add(author_local_today)

    return sorted(dates)


def _live_observer_dates(start_date: str | None, end_date: str | None, now: dt.datetime) -> list[str]:
    dates = _date_values_between(start_date, end_date)

    if dates:
        return dates

    return [now.astimezone(dt.UTC).date().isoformat()]


def _should_track_plugin_staleness(
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    start_date: str | None,
    end_date: str | None,
    date_mode: str | None,
    now: dt.datetime,
    workday_started: bool,
) -> bool:
    if date_mode == "authorLocalToday":
        return workday_started

    local_today = _local_date_for_time_zone(now, _author_time_zone_id(raw_author, profiles, fallback_time_zone_id))
    return workday_started and _date_in_range(local_today, start_date, end_date)


def _author_time_zone_id(
    raw_author: Any, profiles: dict[str, dict[str, Any]], fallback_time_zone_id: Any = None
) -> str:
    profile = profiles.get(str(raw_author or ""))
    profile_time_zone = _valid_time_zone_id((profile or {}).get("timeZoneId"))

    if profile_time_zone:
        return profile_time_zone

    return _valid_time_zone_id(fallback_time_zone_id) or "UTC"


def _local_date_for_time_zone(value: dt.datetime, time_zone_id: str) -> str:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return value.astimezone(zone).date().isoformat()


def _date_in_range(value: str, start_date: str | None, end_date: str | None) -> bool:
    if start_date and value < start_date:
        return False

    if end_date and value > end_date:
        return False

    return True


def _display_name(raw_author: Any, profile: dict[str, Any]) -> str:
    return str(profile.get("displayName") or raw_author or "Unknown User")


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _public_site_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": user.get("email", ""),
        "displayName": user.get("displayName") or user.get("email", ""),
        "role": user.get("role", "viewer"),
        "canViewServerStats": bool(user.get("canViewServerStats", False)),
        "active": user.get("active", True),
    }


def _with_productivity(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    active_seconds = int(item.get("activeSeconds", 0))
    idle_seconds = int(item.get("idleSeconds", 0))
    break_seconds = int(item.get("breakSeconds", 0))
    overtime_seconds = int(item.get("overtimeActiveSeconds", 0))
    item["productivity"] = round(_productivity(active_seconds, idle_seconds, break_seconds, overtime_seconds), 2)
    return item


def _with_activity_mix(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    item["activityMix"] = _activity_mix_from_list(item.get("activityCounts", []))
    item["overtimeActivityMix"] = _activity_mix_from_list(item.get("overtimeActivityCounts", []))
    return item


def _activity_mix_from_counts(activity_counts: dict[str, int]) -> list[dict[str, Any]]:
    return _activity_mix_from_list(
        [{"type": activity_type, "count": count} for activity_type, count in activity_counts.items()]
    )


def _activity_mix_from_list(activity_counts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total_activities = sum(int(count.get("count", 0)) for count in activity_counts)
    activity_mix = []

    for count in activity_counts:
        activity_type = count.get("type")
        activity_count = int(count.get("count", 0))
        percent = round((activity_count / total_activities) * 100) if total_activities else 0

        if not activity_type or percent <= 0:
            continue

        activity_mix.append({"type": activity_type, "count": activity_count, "percent": percent})

    return activity_mix


def _with_author_presence(
    author: dict[str, Any],
    send_interval_seconds: int,
    now: dt.datetime,
    presence_override: dict[str, Any] | None = None,
    track_plugin_staleness: bool = True,
) -> dict[str, Any]:
    item = dict(author)
    presence_clock = now
    last_received_at = _coerce_datetime(item.get("lastReceivedAt"))
    last_report_received_at = _coerce_datetime(item.get("_lastReportReceivedAt")) or last_received_at
    stale_threshold_seconds = max(0, send_interval_seconds * 2)
    active_meeting = bool(item.get("activeMeeting"))
    forced_offline = False
    has_reports_stopped = False

    if active_meeting:
        pass
    elif not track_plugin_staleness:
        pass
    elif last_report_received_at:
        seconds_since_report = max(0, int((presence_clock - last_report_received_at).total_seconds()))

        if seconds_since_report > stale_threshold_seconds:
            has_reports_stopped = True
    else:
        has_reports_stopped = True

    if presence_override and presence_override.get("offlineAt"):
        overtime_received_at = _coerce_datetime(presence_override.get("overtimeReceivedAt"))
        forced_offline = True

        if overtime_received_at:
            seconds_since_overtime = max(0, int((presence_clock - overtime_received_at).total_seconds()))
            forced_offline = seconds_since_overtime > stale_threshold_seconds

    if not track_plugin_staleness and not active_meeting and not (presence_override and presence_override.get("offlineAt")):
        if last_received_at:
            seconds_since_report = max(0, int((presence_clock - last_received_at).total_seconds()))
            forced_offline = seconds_since_report > stale_threshold_seconds
        else:
            forced_offline = True

    if active_meeting:
        forced_offline = False

    is_stale = forced_offline or has_reports_stopped
    item["status"] = "stale" if is_stale else "online"

    if is_stale:
        if has_reports_stopped and forced_offline:
            item["stalePresence"] = "both"
        elif has_reports_stopped:
            item["stalePresence"] = "reports"
        else:
            item["stalePresence"] = "telegram"

    item["sendIntervalSeconds"] = send_interval_seconds
    item["staleThresholdSeconds"] = stale_threshold_seconds
    item.pop("_lastReportReceivedAt", None)
    return item


def _author_has_summary_activity(author: dict[str, Any]) -> bool:
    return any(
        int(author.get(key) or 0) > 0
        for key in (
            "daySeconds",
            "telegramDaySeconds",
            "pluginDaySeconds",
            "rawPluginDaySeconds",
            "activeSeconds",
            "idleSeconds",
            "meetingSeconds",
            "breakSeconds",
            "overtimeActiveSeconds",
        )
    )


def _clear_inactive_author_report_metadata(authors: Any) -> None:
    for author in authors:
        if _author_has_summary_activity(author):
            continue

        author["source"] = None
        author["pluginVersion"] = None
        author["lastRecordedAt"] = ""
        author["lastReceivedAt"] = ""


def _author_color(raw_author: Any) -> str:
    value = _normalize_author(raw_author)
    index = sum(ord(char) for char in value) % len(AUTHOR_COLORS)
    return AUTHOR_COLORS[index]


def _normalize_author(value: Any) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).strip()
    return normalized or "Unknown User"


def _normalize_github_username(value: Any) -> str:
    s = str(value or "").strip()
    if s.startswith("@"):
        s = s[1:].strip()
    if not s or len(s) > 39:
        return ""
    if s.startswith("-") or s.endswith("-"):
        return ""
    for ch in s:
        if not (ch.isascii() and (ch.isalnum() or ch == "-")):
            return ""
    return s


def _github_login_from_profile_doc(doc: dict[str, Any] | None) -> str:
    if not doc:
        return ""
    return _normalize_github_username(doc.get("githubUsername") or doc.get("github_username"))


def _github_username_ui_default(raw_author: str, profile: dict[str, Any] | None) -> str:
    """Value for dashboard GitHub field: stored login, else GitHub-safe rawAuthor, else raw author string."""
    stored = _github_login_from_profile_doc(profile)
    if stored:
        return stored
    as_login = _normalize_github_username(raw_author)
    if as_login:
        return as_login
    ra = str(raw_author or "").strip()

    if ra and ra != "Unknown User":
        return ra

    return ""


def _github_username_for_avatar_fetch(raw_author: str, profile: dict[str, Any] | None) -> str:
    """Login for https://github.com/{{login}}.png — valid GitHub usernames only (never arbitrary display names)."""
    stored = _github_login_from_profile_doc(profile)
    if stored:
        return stored
    return _normalize_github_username(raw_author)


def _cached_author_avatar_api_url(raw_author: Any, github_username: Any, profile: dict[str, Any] | None = None) -> str:
    login = _normalize_github_username(github_username)

    has_manual_avatar = bool(profile and str(profile.get("avatarSource") or "") == "manual")

    if not login and not has_manual_avatar:
        return ""

    author = _normalize_author(raw_author)
    query = urllib.parse.quote(author, safe="")
    base = f"/api/v1/avatars/author?rawAuthor={query}"

    bust: int | None = None
    if profile:
        ref = _coerce_datetime(profile.get("avatarRefreshedAt"))
        if ref is not None:
            bust = int(ref.timestamp() * 1000)
        else:
            up = _coerce_datetime(profile.get("updatedAt"))
            if up is not None:
                bust = int(up.timestamp() * 1000)

    if bust is not None:
        return f"{base}&v={bust}"

    return base


def _author_configured_time_zone_id(raw_author: str) -> str | None:
    return _valid_time_zone_id(AUTHOR_TIME_ZONE_IDS.get(raw_author))


def _valid_time_zone_id(value: Any) -> str | None:
    normalized = str(value or "").strip()

    if not normalized:
        return None

    normalized = WINDOWS_TIME_ZONE_IDS.get(normalized, normalized)

    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        return None

    return normalized


def _valid_color(value: str | None) -> str | None:
    normalized = (value or "").strip()

    if len(normalized) == 7 and normalized.startswith("#"):
        try:
            int(normalized[1:], 16)
        except ValueError:
            return None

        return normalized

    return None


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value.strip()).strip("_")


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _date_start(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)


def _insert_many_if_supported(collection: Any, docs: list[dict[str, Any]]) -> None:
    if not docs:
        return

    insert_many = getattr(collection, "insert_many", None)

    if insert_many:
        insert_many(docs)
        return

    for doc in docs:
        collection.insert_one(doc)


def _merge_count_list(
    current: list[dict[str, Any]], deltas: list[dict[str, Any]], key_name: str, count_name: str
) -> list[dict[str, Any]]:
    by_key = {item.get(key_name): dict(item) for item in current if item.get(key_name)}

    for delta_item in deltas:
        key = delta_item.get(key_name)

        if not key:
            continue

        existing = by_key.get(key)

        if existing:
            existing[count_name] = int(existing.get(count_name, 0)) + int(delta_item.get(count_name, 0))
        else:
            by_key[key] = dict(delta_item)

    return sorted(by_key.values(), key=lambda item: item.get(count_name, 0), reverse=True)


def _analytics_year_months(docs: list[dict[str, Any]], year: int) -> list[dict[str, Any]]:
    docs_by_date = {str(item.get("date") or ""): item for item in docs if item.get("date")}
    months = []
    today = dt.date.today()
    if year < today.year:
        last_month = 12
    elif year == today.year:
        last_month = today.month
    else:
        last_month = 0

    for month in range(last_month, 0, -1):
        month_start = dt.date(year, month, 1)
        if month == 12:
            month_end = dt.date(year, 12, 31)
        else:
            month_end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

        month_docs = _docs_for_range(docs_by_date, month_start, month_end)
        previous_month_end = month_start - dt.timedelta(days=1)
        previous_month_start = previous_month_end.replace(day=1)
        previous_month_docs = _docs_for_range(docs_by_date, previous_month_start, previous_month_end)
        weeks = _analytics_month_weeks(docs_by_date, month_start, month_end)
        totals = _analytics_totals(month_docs)
        previous = _analytics_totals(previous_month_docs)
        months.append(
            {
                "month": month,
                "label": month_start.strftime("%B"),
                "startDate": month_start.isoformat(),
                "endDate": month_end.isoformat(),
                "totals": totals,
                "previousMonthDeltas": _analytics_deltas(totals, previous),
                "weeks": weeks,
            }
        )

    return months


def _analytics_month_weeks(
    docs_by_date: dict[str, dict[str, Any]], month_start: dt.date, month_end: dt.date
) -> list[dict[str, Any]]:
    weeks = []
    cursor = month_start - dt.timedelta(days=month_start.weekday())

    while cursor <= month_end:
        week_start = cursor
        week_end = week_start + dt.timedelta(days=6)
        days = []
        week_docs = []

        for offset in range(7):
            day = week_start + dt.timedelta(days=offset)
            doc = docs_by_date.get(day.isoformat())
            day_totals = _analytics_totals([doc] if doc else [])
            days.append(
                {
                    "date": day.isoformat(),
                    "label": day.strftime("%a %d"),
                    "inMonth": month_start <= day <= month_end,
                    "totals": day_totals,
                    "hourlyActivity": public_hourly_activity(doc.get("hourlyActivity", [])) if doc else empty_hourly_activity(),
                }
            )

            if month_start <= day <= month_end and doc:
                week_docs.append(doc)

        previous_week_start = week_start - dt.timedelta(days=7)
        previous_week_end = week_start - dt.timedelta(days=1)
        previous_week_docs = _docs_for_range(docs_by_date, previous_week_start, previous_week_end)
        totals = _analytics_totals(week_docs)
        previous = _analytics_totals(previous_week_docs)
        weeks.append(
            {
                "week": len(weeks) + 1,
                "label": f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}",
                "startDate": week_start.isoformat(),
                "endDate": week_end.isoformat(),
                "totals": totals,
                "previousWeekDeltas": _analytics_deltas(totals, previous),
                "days": days,
            }
        )
        cursor += dt.timedelta(days=7)

    return weeks


def _docs_for_range(docs_by_date: dict[str, dict[str, Any]], start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    docs = []
    cursor = start

    while cursor <= end:
        doc = docs_by_date.get(cursor.isoformat())

        if doc:
            docs.append(doc)

        cursor += dt.timedelta(days=1)

    return docs


def _analytics_totals(docs: list[dict[str, Any]]) -> dict[str, Any]:
    active_seconds = sum(int(item.get("activeSeconds", 0)) for item in docs)
    idle_seconds = sum(int(item.get("idleSeconds", 0)) for item in docs)
    break_seconds = sum(int(item.get("breakSeconds", 0)) for item in docs)
    overtime_active_seconds = sum(int(item.get("overtimeActiveSeconds", 0)) for item in docs)
    telegram_day_seconds = sum(int(item.get("daySeconds", 0)) for item in docs)
    plugin_day_seconds = sum(_plugin_day_seconds(item) for item in docs)
    productivity = _productivity(active_seconds, idle_seconds, break_seconds, overtime_active_seconds)
    return {
        "daySeconds": telegram_day_seconds,
        "activeSeconds": active_seconds,
        "idleSeconds": idle_seconds,
        "breakSeconds": break_seconds,
        "overtimeActiveSeconds": overtime_active_seconds,
        "telegramDaySeconds": telegram_day_seconds,
        "pluginDaySeconds": plugin_day_seconds,
        "productivity": round(productivity, 2),
    }


def _analytics_deltas(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "activeSeconds": current["activeSeconds"] - previous["activeSeconds"],
        "idleSeconds": current["idleSeconds"] - previous["idleSeconds"],
        "breakSeconds": current["breakSeconds"] - previous["breakSeconds"],
        "overtimeActiveSeconds": current["overtimeActiveSeconds"] - previous["overtimeActiveSeconds"],
        "telegramDaySeconds": current["telegramDaySeconds"] - previous["telegramDaySeconds"],
        "pluginDaySeconds": current["pluginDaySeconds"] - previous["pluginDaySeconds"],
        "productivity": round(float(current["productivity"]) - float(previous["productivity"]), 2),
    }


def _productivity(
    active_seconds: int, idle_seconds: int, break_seconds: int, overtime_seconds: int = 0
) -> float:
    penalized_break_seconds = max(0, break_seconds - LONG_BREAK_THRESHOLD_SECONDS)
    denominator = active_seconds + idle_seconds + penalized_break_seconds
    numerator = active_seconds + overtime_seconds
    return (numerator / denominator) * 100 if denominator else 0


def _plugin_day_seconds(item: dict[str, Any], active_seconds: int | None = None, idle_seconds: int | None = None) -> int:
    active = int(item.get("activeSeconds", 0) if active_seconds is None else active_seconds)
    idle = int(item.get("idleSeconds", 0) if idle_seconds is None else idle_seconds)
    work_window_seconds = int(item.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
    return min(max(0, work_window_seconds), max(0, active + idle))


def _coerce_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)

        return value.astimezone(dt.UTC)

    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.UTC)

        return parsed.astimezone(dt.UTC)

    return None


def _isoformat_or_none(value: Any) -> str | None:
    parsed = _coerce_datetime(value)
    return parsed.isoformat() if parsed else None


def _normalize_telegram_username(value: str | None) -> str:
    return (value or "").strip().lstrip("@").lower()


def _normalize_discord_user_id(value: str | None) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.UTC)

    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)

    return parsed.astimezone(dt.UTC)


def _telegram_event_date(event_time: dt.datetime, time_zone_id: str) -> str:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return event_time.astimezone(zone).date().isoformat()


def _to_local_datetime(value: dt.datetime, time_zone_id: str) -> dt.datetime:
    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return value.astimezone(zone)


def _latest_datetime(*values: dt.datetime | None) -> dt.datetime | None:
    latest = None

    for value in values:
        if not value:
            continue

        if not latest or value > latest:
            latest = value

    return latest


def _parse_local_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value

    if isinstance(value, str) and value:
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return None


def _session_key(snapshot: dict[str, Any]) -> str:
    return "|".join(
        [
            str(snapshot.get("source") or ""),
            str(snapshot.get("author") or "Unknown User"),
            str(snapshot.get("projectId") or ""),
            str(snapshot.get("sessionId") or ""),
            str(snapshot.get("date") or ""),
        ]
    )


def _state_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "activeSeconds": int(snapshot.get("activeSeconds", 0)),
        "idleSeconds": int(snapshot.get("idleSeconds", 0)),
        "overtimeActiveSeconds": int(snapshot.get("overtimeActiveSeconds", 0)),
        "activityCounts": snapshot.get("activityCounts", []),
        "savedPrefabs": snapshot.get("savedPrefabs", []),
        "hourlyActivity": snapshot.get("hourlyActivity", []),
    }


def _build_deltas(snapshot: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    active_delta = _delta(snapshot.get("activeSeconds"), previous.get("activeSeconds"))
    idle_delta = _delta(snapshot.get("idleSeconds"), previous.get("idleSeconds"))
    overtime_delta = _delta(snapshot.get("overtimeActiveSeconds"), previous.get("overtimeActiveSeconds"))
    work_window_seconds = int(snapshot.get("workWindowSeconds") or DEFAULT_PLUGIN_WORK_WINDOW_SECONDS)
    consumed_normal_seconds = min(
        work_window_seconds,
        max(0, int(previous.get("activeSeconds", 0)) + int(previous.get("idleSeconds", 0))),
    )
    remaining_normal_seconds = max(0, work_window_seconds - consumed_normal_seconds)
    normal_active_delta = min(active_delta, remaining_normal_seconds)
    overtime_delta += max(0, active_delta - normal_active_delta)
    remaining_normal_seconds = max(0, remaining_normal_seconds - normal_active_delta)
    normal_idle_delta = min(idle_delta, remaining_normal_seconds)

    return {
        "activeDeltaSeconds": normal_active_delta,
        "idleDeltaSeconds": normal_idle_delta,
        "overtimeActiveDeltaSeconds": overtime_delta,
        "activityCountDeltas": _count_deltas(snapshot.get("activityCounts", []), previous.get("activityCounts", []), "type", "count"),
        "savedPrefabDeltas": _count_deltas(snapshot.get("savedPrefabs", []), previous.get("savedPrefabs", []), "path", "saveCount"),
        "hourlyActivityDelta": hourly_deltas(snapshot.get("hourlyActivity", []), previous.get("hourlyActivity", [])),
    }


def _delta(current: Any, previous: Any) -> int:
    return max(0, int(current or 0) - int(previous or 0))


def _duration_microseconds(start: dt.datetime, end: dt.datetime) -> int:
    delta = end - start

    return max(
        0,
        ((delta.days * 24 * 60 * 60) + delta.seconds) * MICROSECONDS_PER_SECOND + delta.microseconds,
    )


def _seconds_from_microseconds(value: Any) -> int:
    microseconds = max(0, int(value or 0))

    return int((microseconds + (MICROSECONDS_PER_SECOND // 2)) // MICROSECONDS_PER_SECOND)


def _time_microseconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    if microseconds_key in item:
        return max(0, int(item.get(microseconds_key) or 0))

    return max(0, int(item.get(seconds_key) or 0)) * MICROSECONDS_PER_SECOND


def _time_seconds(item: dict[str, Any], seconds_key: str, microseconds_key: str) -> int:
    if microseconds_key in item:
        return _seconds_from_microseconds(item.get(microseconds_key))

    return max(0, int(item.get(seconds_key) or 0))


def _count_deltas(current: list[dict[str, Any]], previous: list[dict[str, Any]], key_name: str, count_name: str) -> list[dict[str, Any]]:
    previous_by_key = {item.get(key_name): int(item.get(count_name, 0)) for item in previous if item.get(key_name)}
    deltas = []

    for item in current:
        key = item.get(key_name)

        if not key:
            continue

        count_delta = max(0, int(item.get(count_name, 0)) - previous_by_key.get(key, 0))

        if count_delta:
            delta_item = dict(item)
            delta_item[count_name] = count_delta
            deltas.append(delta_item)

    return deltas



__all__: tuple[str, ...] = (
    "AFK_IDLE_ARTIFACT_THRESHOLD_SECONDS",
    "ASCENDING",
    "AUTHOR_COLORS",
    "AUTHOR_TIME_ZONE_IDS",
    "AUTO_BREAK_SECONDS",
    "Any",
    "DEFAULT_CALENDAR_REASONS",
    "DEFAULT_DISCORD_MEETING_AUTO_AFK_TIMEOUT_SECONDS",
    "DEFAULT_IDLE_THRESHOLD_SECONDS",
    "DEFAULT_MEETING_SUMMARY_PROMPT",
    "DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE",
    "DEFAULT_PLUGIN_WORK_WINDOW_SECONDS",
    "DEFAULT_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES",
    "DEVICE_SOURCES",
    "DESCENDING",
    "Database",
    "DuplicateKeyError",
    "LONG_BREAK_THRESHOLD_SECONDS",
    "LOW_PRODUCTIVITY_THRESHOLD",
    "MAX_STALE_HEARTBEAT_IDLE_MULTIPLIER",
    "MAX_TELEGRAM_ONLINE_PROMPT_DELAY_MINUTES",
    "MICROSECONDS_PER_SECOND",
    "MIN_HEARTBEAT_IDLE_FRAGMENT_SECONDS",
    "MongoClient",
    "NON_ACTIVITY_EVENT_TYPES",
    "RAW_ACTIVITY_EVENT_TYPES",
    "REPORT_CHALLENGE_TTL_SECONDS",
    "ReturnDocument",
    "SELECT_HEAVY_MIN_EVENTS",
    "SELECT_HEAVY_THRESHOLD_PERCENT",
    "STALE_HEARTBEAT_RECEIVE_SKEW_MULTIPLIER",
    "STALE_HEARTBEAT_RECEIVE_SKEW_SECONDS_FLOOR",
    "Settings",
    "TELEGRAM_BREAK_ACTIVITY_PROMPT_DELAY_SECONDS",
    "TELEGRAM_DAY_REMINDER_SECONDS",
    "TELEGRAM_ONLINE_PROMPT_DELAY_SECONDS",
    "WINDOWS_TIME_ZONE_IDS",
    "ZoneInfo",
    "ZoneInfoNotFoundError",
    "device_source_from_payload",
    "is_device_source",
    "live_date_in_scope",
    "_activity_count_type",
    "_activity_mix_from_counts",
    "_activity_mix_from_list",
    "_analytics_deltas",
    "_analytics_month_weeks",
    "_analytics_totals",
    "_analytics_year_months",
    "_author_color",
    "_author_configured_time_zone_id",
    "_author_has_summary_activity",
    "_author_time_zone_id",
    "_build_deltas",
    "_cached_author_avatar_api_url",
    "_clear_inactive_author_report_metadata",
    "_coerce_datetime",
    "_count_deltas",
    "_date_in_range",
    "_date_in_summary_scope",
    "_date_query",
    "_date_start",
    "_date_values_between",
    "_delta",
    "_display_name",
    "_docs_for_range",
    "_document_identity_query",
    "_duration_microseconds",
    "_empty_batch_deltas",
    "_empty_event_deltas",
    "_github_login_from_profile_doc",
    "_github_username_for_avatar_fetch",
    "_github_username_ui_default",
    "_has_active_or_overtime_delta",
    "_has_time_delta",
    "_insert_many_if_supported",
    "_interval_deltas",
    "_is_activity_event",
    "_is_author_local_today",
    "_is_overtime_event_delta",
    "_iso",
    "_isoformat_or_none",
    "_latest_datetime",
    "_local_date_for_time_zone",
    "_looks_like_missing_transcript_summary",
    "_looks_like_no_work_content_summary",
    "_meeting_audio_quality_status",
    "_meeting_interval_date_query",
    "_meeting_interval_scope_dates",
    "_merge_batch_deltas",
    "_merge_count_list",
    "_merge_event_delta_items",
    "_merge_event_delta_items_by_date",
    "_move_hourly_idle_to_break",
    "_new_id",
    "_next_author_local_date",
    "_normalize_author",
    "_normalize_discord_user_id",
    "_normalize_email",
    "_normalize_github_username",
    "_normalize_raw_event",
    "_normalize_report_hour_filter",
    "_normalize_telegram_username",
    "_parse_date",
    "_parse_local_datetime",
    "_parse_timestamp",
    "_plugin_day_seconds",
    "_productivity",
    "_public_site_user",
    "_raw_event_activity_scope",
    "_raw_event_author_day_key",
    "_raw_event_session_key",
    "_raw_event_time",
    "_report_date_query",
    "_report_matches_hour_filter",
    "_report_row_time",
    "_report_sort_datetime",
    "_report_table_sort_key",
    "_saved_prefab_delta",
    "_saved_prefabs_for_summary_item",
    "_overtime_saved_prefabs_for_summary_item",
    "_seconds_from_microseconds",
    "_session_key",
    "_should_track_plugin_staleness",
    "_slug",
    "_state_snapshot",
    "_telegram_event_date",
    "_time_microseconds",
    "_time_seconds",
    "_to_local_datetime",
    "_valid_color",
    "_valid_time_zone_id",
    "_with_activity_mix",
    "_with_author_presence",
    "_with_productivity",
    "_worked_file_delta",
    "annotations",
    "dt",
    "hash_password",
    "new_session_token",
    "re",
    "session_token_hash",
    "unicodedata",
    "urllib",
    "uuid",
    "verify_password",
)
