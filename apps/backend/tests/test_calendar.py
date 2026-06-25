from tests.fakes import fake_repository


def test_calendar_reasons_include_sick_leave_for_existing_reason_collections():
    repo = fake_repository()
    repo.db.calendar_reasons.insert_one({"id": "vacation", "label": "Vacation"})

    reasons = repo.calendar_reasons()

    assert {reason["id"] for reason in reasons} >= {"vacation", "sick_leave", "day_off", "absence"}
    assert next(reason for reason in reasons if reason["id"] == "sick_leave")["label"] == "Sick leave"
