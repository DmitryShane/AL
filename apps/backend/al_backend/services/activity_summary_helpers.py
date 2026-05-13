from __future__ import annotations

from typing import Any

from ..activity_math import _activity_mix_from_list, live_date_in_scope


def _is_device_profile_raw_author(value: str) -> bool:
    normalized = str(value or "").strip()
    return normalized.startswith("Device") and normalized[6:].isdigit()


def _is_live_date_match(
    value: Any,
    raw_author: str,
    profiles: dict[str, dict[str, Any]],
    fallback_time_zone_id: Any,
    now: Any,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    return live_date_in_scope(value, raw_author, profiles, fallback_time_zone_id, now, start_date, end_date)


def _activity_mix_source_groups(
    items_by_source: dict[str, list[dict[str, Any]]],
    active_seconds_by_source: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    groups = []

    for source, items in items_by_source.items():
        activity_mix = _activity_mix_from_list(items)

        if not activity_mix:
            continue

        groups.append(
            {
                "source": source,
                "totalCount": sum(int(item.get("count", 0)) for item in items),
                "activeSeconds": int((active_seconds_by_source or {}).get(source, 0)),
                "activityMix": activity_mix,
            }
        )

    return sorted(groups, key=lambda item: (-int(item.get("totalCount", 0)), str(item.get("source") or "")))


def _saved_prefab_source_groups(items_by_source: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups = []

    for source, items in items_by_source.items():
        saved_prefabs = sorted(items, key=lambda item: int(item.get("saveCount", 0)), reverse=True)

        if not saved_prefabs:
            continue

        groups.append(
            {
                "source": source,
                "totalSaveCount": sum(int(item.get("saveCount", 0)) for item in saved_prefabs),
                "savedPrefabs": saved_prefabs,
            }
        )

    return sorted(groups, key=lambda item: (-int(item.get("totalSaveCount", 0)), str(item.get("source") or "")))


def _with_source_breakdowns(author: dict[str, Any]) -> dict[str, Any]:
    item = dict(author)
    activity_counts_by_source = item.pop("_activityCountsBySource", {})
    active_seconds_by_source = item.pop("_activeSecondsBySource", {})
    saved_prefabs_by_source = item.pop("_savedPrefabsBySource", {})
    overtime_activity_counts_by_source = item.pop("_overtimeActivityCountsBySource", {})
    overtime_active_seconds_by_source = item.pop("_overtimeActiveSecondsBySource", {})
    overtime_saved_prefabs_by_source = item.pop("_overtimeSavedPrefabsBySource", {})

    item["activityMixBySource"] = _activity_mix_source_groups(activity_counts_by_source, active_seconds_by_source)
    item["savedPrefabsBySource"] = _saved_prefab_source_groups(saved_prefabs_by_source)
    item["overtimeActivityMixBySource"] = _activity_mix_source_groups(overtime_activity_counts_by_source, overtime_active_seconds_by_source)
    item["overtimeSavedPrefabsBySource"] = _saved_prefab_source_groups(overtime_saved_prefabs_by_source)
    return item
