from __future__ import annotations

from typing import Any

from ..activity_math import dt, _coerce_datetime, _telegram_event_date, _valid_time_zone_id
from ..mongo_composable import MongoComposableMixin


class BreakSessionService(MongoComposableMixin):
    def _close_break_session(self, normalized_telegram: str, raw_author: str, event_time: dt.datetime) -> dict[str, Any]:
        session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

        if not session:
            return {}

        started_at = _coerce_datetime(session["startedAt"]) or event_time
        break_seconds = max(0, int((event_time - started_at).total_seconds()))
        time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"
        break_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
        self.db.break_sessions.delete_one({"telegramUsername": normalized_telegram})
        self.db.break_intervals.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "startedAt": started_at,
                "endedAt": event_time,
                "date": break_date,
                "timeZoneId": time_zone_id,
                "breakSeconds": break_seconds,
            }
        )
        self.db.daily_author_activity.update_many(
            {"author": raw_author, "date": break_date},
            {"$inc": {"breakSeconds": break_seconds}, "$set": {"updatedAt": dt.datetime.now(dt.UTC)}},
        )
        return {"breakSeconds": break_seconds}
