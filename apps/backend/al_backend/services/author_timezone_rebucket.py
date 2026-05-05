from __future__ import annotations

from ..activity_math import _coerce_datetime, _document_identity_query, _telegram_event_date
from ..mongo_composable import MongoComposableMixin


class AuthorTimezoneRebucketService(MongoComposableMixin):
    def _rebucket_author_telegram_time_zone(self, raw_author: str, time_zone_id: str, time_zone_display_name: str) -> None:
        for event in self.db.break_events.find({"rawAuthor": raw_author}, {"_id": 1, "timestamp": 1}):
            event_time = _coerce_datetime(event.get("timestamp"))

            if event_time:
                self.db.break_events.update_one(
                    _document_identity_query(event),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )

        for collection_name, time_field in (
            ("day_sessions", "startedAt"),
            ("break_sessions", "startedAt"),
            ("break_intervals", "startedAt"),
        ):
            collection = getattr(self.db, collection_name)

            for item in collection.find({"rawAuthor": raw_author}, {"_id": 1, time_field: 1}):
                event_time = _coerce_datetime(item.get(time_field))

                if event_time:
                    collection.update_one(
                        _document_identity_query(item),
                        {
                            "$set": {
                                "date": _telegram_event_date(event_time, time_zone_id),
                                "timeZoneId": time_zone_id,
                                "timeZoneDisplayName": time_zone_display_name,
                            }
                        },
                    )

        for row in self.db.report_rows.find({"source": "telegram", "author": raw_author}, {"_id": 1, "recordedAt": 1}):
            event_time = _coerce_datetime(row.get("recordedAt"))

            if event_time:
                self.db.report_rows.update_one(
                    _document_identity_query(row),
                    {
                        "$set": {
                            "date": _telegram_event_date(event_time, time_zone_id),
                            "timeZoneId": time_zone_id,
                            "timeZoneDisplayName": time_zone_display_name,
                        }
                    },
                )
