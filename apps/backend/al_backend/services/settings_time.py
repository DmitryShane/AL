from __future__ import annotations


WEEKDAY_VALUES = set(range(7))


def parse_settings_time_minutes(value: str) -> int:
    try:
        hour_raw, minute_raw = value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
    except (ValueError, AttributeError):
        raise ValueError("Time must use HH:mm format")

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must use HH:mm format")

    return hour * 60 + minute
