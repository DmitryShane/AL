from al_backend.hourly_fill_rules import empty_hourly_activity


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
            "hourlyActivity": empty_hourly_activity(),
        }
    )

def _author_from_summary(repo, now, date_mode=None):
    summary = repo.activity_summary(start_date="2026-04-28", end_date="2026-04-28", date_mode=date_mode, now=now)
    return next(author for author in summary["authors"] if author["rawAuthor"] == "Future Artist")

def _author_status(repo, now, date_mode=None):
    return _author_from_summary(repo, now, date_mode=date_mode)["status"]
