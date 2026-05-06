from al_backend.activity_math import _empty_hourly_activity


def _insert_presence_daily_activity(repo, received_at):
    repo.db.daily_author_activity.insert_one(
        {
            "source": "ual",
            "author": "Future Artist",
            "projectId": "unity",
            "date": "2026-04-28",
            "lastReceivedAt": received_at,
            "activeSeconds": 60,
            "idleSeconds": 0,
            "workWindowSeconds": 32400,
            "activityCounts": [{"type": "selection", "count": 1}],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "hourlyActivity": _empty_hourly_activity(),
        }
    )

def _author_from_summary(repo, now):
    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28", now=now)
    return next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

def _author_status(repo, now):
    return _author_from_summary(repo, now)["status"]

