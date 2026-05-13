from __future__ import annotations

import datetime as dt
from typing import Any

from ..activity_time import _coerce_datetime


def status_interval_context_for_event(
    status_events: list[dict[str, Any]],
    occurred_at: dt.datetime,
    received_at: dt.datetime | None,
) -> dict[str, Any] | None:
    previous_closed_interval: dict[str, Any] | None = None
    open_offline_event: dict[str, Any] | None = None
    open_offline_at: dt.datetime | None = None

    for status_event in sorted(
        status_events,
        key=lambda item: _coerce_datetime(item.get("transitionAt")) or dt.datetime.min.replace(tzinfo=dt.UTC),
    ):
        transition_at = _coerce_datetime(status_event.get("transitionAt"))

        if not transition_at:
            continue

        event_type = str(status_event.get("statusEventType") or "")
        reason = str(status_event.get("reason") or "")

        if event_type == "offline":
            if reason == "reports_stopped":
                continue

            if occurred_at > transition_at:
                open_offline_event = status_event
                open_offline_at = transition_at

            continue

        if event_type != "online" or not open_offline_event or not open_offline_at:
            continue

        if transition_at <= open_offline_at:
            continue

        if occurred_at < transition_at:
            return {
                "offlineAt": open_offline_at,
                "onlineAt": transition_at,
                "insideOffline": True,
                "timeZoneId": status_event.get("timeZoneId") or open_offline_event.get("timeZoneId"),
            }

        previous_closed_interval = {
            "offlineAt": open_offline_at,
            "onlineAt": transition_at,
            "insideOffline": False,
            "timeZoneId": status_event.get("timeZoneId") or open_offline_event.get("timeZoneId"),
        }
        open_offline_event = None
        open_offline_at = None

    if open_offline_event and open_offline_at and occurred_at > open_offline_at:
        return {
            "offlineAt": open_offline_at,
            "onlineAt": None,
            "insideOffline": True,
            "timeZoneId": open_offline_event.get("timeZoneId"),
        }

    if previous_closed_interval and received_at and received_at >= previous_closed_interval["onlineAt"]:
        return previous_closed_interval

    return None
