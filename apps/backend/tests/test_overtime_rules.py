import datetime as dt

from al_backend.overtime_rules import OvertimeRuleContext, is_night_overtime_window, overtime_window_for_event


def _context() -> OvertimeRuleContext:
    return OvertimeRuleContext(
        vacation_overtime_window_for_event=lambda _event: None,
        is_author_offline_after_latest_telegram_state=lambda _author, _date, _time: False,
        day_session_for_author_date=lambda _author, _date: None,
    )


def test_night_overtime_window_matches_local_midnight_to_six():
    event = {
        "author": "Igor Mats",
        "date": "2026-05-09",
        "timeZoneId": "America/Vancouver",
        "occurredAtUtc": "2026-05-09T10:00:00Z",
    }

    window = is_night_overtime_window(event)

    assert window == (
        dt.datetime(2026, 5, 9, 7, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 9, 13, 0, tzinfo=dt.UTC),
    )
    assert overtime_window_for_event(event, _context()) == window


def test_night_overtime_window_excludes_six_am_local():
    event = {
        "author": "Igor Mats",
        "date": "2026-05-09",
        "timeZoneId": "America/Vancouver",
        "occurredAtUtc": "2026-05-09T13:00:00Z",
    }

    assert is_night_overtime_window(event) is None
    assert overtime_window_for_event(event, _context()) is None


def test_night_overtime_window_uses_author_timezone_not_utc_hour():
    event = {
        "author": "Evgeniy Dotsenko",
        "date": "2026-05-09",
        "timeZoneId": "Europe/Sofia",
        "occurredAtUtc": "2026-05-08T22:30:00Z",
    }

    window = is_night_overtime_window(event)

    assert window == (
        dt.datetime(2026, 5, 8, 21, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 9, 3, 0, tzinfo=dt.UTC),
    )


def test_night_overtime_window_uses_local_timestamp_offset_when_timezone_id_is_local():
    event = {
        "author": "Igor Mats",
        "date": "2026-05-09",
        "timeZoneId": "Local",
        "occurredAtUtc": "2026-05-09T08:57:10Z",
        "occurredAtLocal": "2026-05-09T01:57:10-07:00",
    }

    window = is_night_overtime_window(event)

    assert window == (
        dt.datetime(2026, 5, 9, 7, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 9, 13, 0, tzinfo=dt.UTC),
    )
