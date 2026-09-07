import datetime as dt

import pytest

from al_backend.services.activity_summary_report_filters import _status_transition_datetime
from tests.fakes import fake_repository

DAY = "2026-09-07"


def status(kind, transition, recorded=None, author="A", day=DAY):
    return {"source": "status", "reportType": "status", "author": author, "date": day,
            "statusEventType": kind, "statusReason": "reports_stopped" if kind == "offline" else "reports_resumed",
            "statusEventKey": f"{author}|{day}|{kind}|{transition}", "recordedAt": recorded or transition,
            "receivedAt": transition}


def test_resumed_old_report_does_not_hide_fresh_reports_in_table():
    repo = fake_repository()
    rows = [status("offline", "2026-09-07T10:31:11+00:00"),
            status("online", "2026-09-07T11:21:07+00:00", "2026-09-07T10:14:41+00:00")]
    for row in rows:
        repo.db.report_rows.insert_one(row)
    for recorded in ["2026-09-07T14:23:42+03:00", "2026-09-07T16:53:42+03:00"]:
        repo.db.report_rows.insert_one({"author": "A", "date": DAY, "source": "ual", "reportType": "auto",
                                       "recordedAt": recorded, "receivedAt": recorded, "activeDeltaSeconds": 60})
    assert repo._status_intervals_for_reports(rows) == {}
    page = repo.reports_page(start_date=DAY, end_date=DAY, author="A", use_snapshots=False)
    assert [r["recordedAt"] for r in page["reports"] if r["source"] == "ual"] == [
        "2026-09-07T16:53:42+03:00", "2026-09-07T14:23:42+03:00"]
    assert page["total"] == 4


@pytest.mark.parametrize("key", [None, "broken", "A|2026-09-07|online|invalid", "Other|2026-09-07|online|2026-09-07T12:00:00Z"])
def test_legacy_or_invalid_key_uses_received_time(key):
    row = status("online", "2026-09-07T11:21:07Z", "2026-09-07T10:14:41Z")
    row["statusEventKey"] = key
    assert _status_transition_datetime(row) == dt.datetime(2026, 9, 7, 11, 21, 7, tzinfo=dt.UTC)


def test_key_has_priority_and_legacy_recorded_fallback():
    row = status("online", "2026-09-07T11:21:07Z", "2026-09-07T10:14:41Z")
    row["receivedAt"] = "2026-09-07T12:00:00Z"
    assert _status_transition_datetime(row).hour == 11
    del row["statusEventKey"]
    del row["receivedAt"]
    assert _status_transition_datetime(row).hour == 10


def test_equal_times_cycles_and_author_day_isolation():
    repo = fake_repository()
    same = "2026-09-07T11:21:07Z"
    rows = [status("online", same), status("offline", same),
            status("offline", "2026-09-07T12:00:00Z"), status("online", "2026-09-07T12:10:00Z"),
            status("offline", "2026-09-07T13:00:00Z", author="B"),
            status("offline", "2026-09-06T13:00:00Z", day="2026-09-06")]
    intervals = repo._status_intervals_for_reports(rows)
    assert ("A", DAY) not in intervals
    assert intervals[("B", DAY)][0][1] is None
    assert intervals[("A", "2026-09-06")][0][1] is None


def test_other_status_reasons_keep_recorded_time():
    row = status("offline", "2026-09-07T11:21:07Z", "2026-09-07T10:14:41Z")
    row["statusReason"] = "telegram_offline"
    assert _status_transition_datetime(row).hour == 10
