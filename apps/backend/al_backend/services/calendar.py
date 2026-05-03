from __future__ import annotations

from ..activity_math import *


class CalendarService:
    def calendar_summary(self, year: int) -> dict[str, Any]:
        self._ensure_calendar_reasons()
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        reasons = self.calendar_reasons()
        reasons_by_id = {item["id"]: item for item in reasons}
        authors = self.author_profiles()
        authors_by_raw = {item["rawAuthor"]: item for item in authors}
        marks = []
        stats_by_author: dict[str, dict[str, Any]] = {
            author["rawAuthor"]: {
                "rawAuthor": author["rawAuthor"],
                "displayName": author["displayName"],
                "authorColor": author["authorColor"],
                "totalMarkedDays": 0,
                "byReason": {reason["id"]: 0 for reason in reasons},
                "latestMarks": [],
            }
            for author in authors
        }

        query = {"date": {"$gte": start_date, "$lte": end_date}}

        for mark in self.db.calendar_marks.find(query, {"_id": 0}).sort("date", DESCENDING):
            author = authors_by_raw.get(mark.get("rawAuthor"), {})
            reason = reasons_by_id.get(mark.get("reasonId"), {"id": mark.get("reasonId"), "label": mark.get("reasonId")})
            item = {
                **mark,
                "displayName": author.get("displayName", mark.get("rawAuthor")),
                "authorColor": author.get("authorColor", _author_color(mark.get("rawAuthor"))),
                "reasonLabel": reason.get("label", mark.get("reasonId")),
            }
            marks.append(item)
            stats = stats_by_author.setdefault(
                mark.get("rawAuthor"),
                {
                    "rawAuthor": mark.get("rawAuthor"),
                    "displayName": author.get("displayName", mark.get("rawAuthor")),
                    "authorColor": author.get("authorColor", _author_color(mark.get("rawAuthor"))),
                    "totalMarkedDays": 0,
                    "byReason": {reason_item["id"]: 0 for reason_item in reasons},
                    "latestMarks": [],
                },
            )
            stats["totalMarkedDays"] += 1
            stats["byReason"][mark.get("reasonId")] = int(stats["byReason"].get(mark.get("reasonId"), 0)) + 1

            if len(stats["latestMarks"]) < 5:
                stats["latestMarks"].append(item)

        return {
            "year": year,
            "authors": authors,
            "reasons": reasons,
            "marks": sorted(marks, key=lambda item: (item["date"], item["rawAuthor"])),
            "stats": sorted(stats_by_author.values(), key=lambda item: item["displayName"].lower()),
        }

    def calendar_reasons(self) -> list[dict[str, Any]]:
        self._ensure_calendar_reasons()
        return list(self.db.calendar_reasons.find({}, {"_id": 0}).sort("label", ASCENDING))

    def upsert_calendar_reason(self, reason_id: str, label: str) -> dict[str, Any]:
        normalized_id = _slug(reason_id or label)
        normalized_label = (label or "").strip()

        if not normalized_id or not normalized_label:
            return {"ok": False, "error": "Reason id and label are required"}

        now = dt.datetime.now(dt.UTC)
        reason = {"id": normalized_id, "label": normalized_label, "updatedAt": now}
        self.db.calendar_reasons.update_one(
            {"id": normalized_id},
            {"$set": reason, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
        return {"ok": True, "reason": {"id": normalized_id, "label": normalized_label}}

    def delete_calendar_reason(self, reason_id: str) -> dict[str, Any]:
        if self.db.calendar_marks.find_one({"reasonId": reason_id}, {"_id": 1}):
            return {"ok": False, "error": "Reason is used by calendar marks"}

        self.db.calendar_reasons.delete_one({"id": reason_id})
        return {"ok": True}

    def upsert_calendar_marks(self, authors: list[str], dates: list[str], reason_id: str, note: str) -> dict[str, Any]:
        normalized_note = (note or "").strip()

        if not authors or not dates or not reason_id or not normalized_note:
            return {"ok": False, "error": "Authors, dates, reason, and note are required"}

        self._ensure_calendar_reasons()
        reason = self.db.calendar_reasons.find_one({"id": reason_id}, {"_id": 1})

        if not reason:
            return {"ok": False, "error": "Unknown reason"}

        now = dt.datetime.now(dt.UTC)
        saved_count = 0

        for raw_author in authors:
            for date in dates:
                _parse_date(date)
                self.db.calendar_marks.update_one(
                    {"rawAuthor": raw_author, "date": date},
                    {
                        "$set": {
                            "rawAuthor": raw_author,
                            "date": date,
                            "reasonId": reason_id,
                            "note": normalized_note,
                            "updatedAt": now,
                        },
                        "$setOnInsert": {"createdAt": now},
                    },
                    upsert=True,
                )
                saved_count += 1

        self.invalidate_activity_summary_cache(dates)
        return {"ok": True, "savedCount": saved_count}

    def delete_calendar_mark(self, raw_author: str, date: str) -> dict[str, Any]:
        self.db.calendar_marks.delete_one({"rawAuthor": raw_author, "date": date})
        self.invalidate_activity_summary_cache([date])
        return {"ok": True}

    def delete_calendar_marks(self, raw_authors: list[str], dates: list[str]) -> dict[str, Any]:
        if not raw_authors or not dates:
            return {"ok": False, "error": "Authors and dates are required"}

        for date in dates:
            _parse_date(date)

        result = self.db.calendar_marks.delete_many({"rawAuthor": {"$in": raw_authors}, "date": {"$in": dates}})
        self.invalidate_activity_summary_cache(dates)
        return {"ok": True, "deletedCount": result.deleted_count}

    def _ensure_calendar_reasons(self) -> None:
        if self.db.calendar_reasons.count_documents({}):
            return

        now = dt.datetime.now(dt.UTC)

        for reason in DEFAULT_CALENDAR_REASONS:
            self.db.calendar_reasons.update_one(
                {"id": reason["id"]},
                {"$set": {**reason, "updatedAt": now}, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )


