from tests.fakes import fake_repository


def test_calendar_reasons_include_sick_leave_for_existing_reason_collections():
    repo = fake_repository()
    repo.db.calendar_reasons.insert_one({"id": "vacation", "label": "Vacation"})

    reasons = repo.calendar_reasons()

    assert {reason["id"] for reason in reasons} >= {"vacation", "sick_leave", "day_off", "absence"}
    assert next(reason for reason in reasons if reason["id"] == "sick_leave")["label"] == "Sick leave"


def test_calendar_summary_includes_all_author_marks_in_stats():
    repo = fake_repository()
    repo.db.author_profiles.insert_one(
        {
            "rawAuthor": "Test Author",
            "displayName": "Test Author",
            "team": "QA",
            "authorColor": "#22c55e",
            "profileType": "person",
        }
    )

    for day in range(1, 8):
        repo.db.calendar_marks.insert_one(
            {
                "rawAuthor": "Test Author",
                "date": f"2026-08-{day:02d}",
                "reasonId": "sick_leave",
                "note": "",
            }
        )

    summary = repo.calendar_summary(2026)
    stats = next(item for item in summary["stats"] if item["rawAuthor"] == "Test Author")

    assert stats["totalMarkedDays"] == 7
    assert {item["date"] for item in stats["latestMarks"]} == {
        f"2026-08-{day:02d}" for day in range(1, 8)
    }
