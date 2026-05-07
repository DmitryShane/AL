from __future__ import annotations

import datetime as dt
import os
from typing import Any

from .activity_math import _coerce_datetime, _telegram_event_date, _valid_time_zone_id


def close_imported_open_live_states(services: Any) -> dict[str, int]:
    if os.getenv("AL_CLOSE_IMPORTED_OPEN_LIVE_STATES", "").strip().lower() not in {"1", "true", "yes"}:
        return {}

    guard = _ImportedOpenLiveStateGuard(services)
    return guard.close_all()


class _ImportedOpenLiveStateGuard:
    def __init__(self, services: Any):
        self.services = services
        self.db = services.db

    def close_all(self) -> dict[str, int]:
        result = {
            "daySessions": self._close_day_sessions(),
            "breakSessions": self._close_break_sessions(),
            "meetingSessions": self._close_meeting_sessions(),
            "statusEvents": self._close_status_events(),
        }

        dates = sorted(self._affected_dates(result))

        if dates:
            self.services.invalidate_activity_summary_cache(dates)

        return result

    def _affected_dates(self, result: dict[str, int]) -> set[str]:
        if not any(result.values()):
            return set()

        return {
            str(item.get("date") or "")
            for collection in (
                self.db.day_sessions,
                self.db.break_intervals,
                self.db.meeting_intervals,
                self.db.status_events,
            )
            for item in collection.find({"metadata.localImportGuard": True}, {"_id": 0, "date": 1})
            if item.get("date")
        }

    def _close_day_sessions(self) -> int:
        closed = 0

        for session in self.db.day_sessions.find({"lastOfflineAt": {"$exists": False}}, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            started_at = _coerce_datetime(session.get("startedAt"))
            day_date = str(session.get("date") or "")

            if not started_at or not day_date:
                continue

            ended_at = self._close_time(raw_author, day_date, started_at)
            day_seconds = max(0, int((ended_at - started_at).total_seconds()))
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date, "lastOfflineAt": {"$exists": False}},
                {
                    "$set": {
                        "lastOfflineAt": ended_at,
                        "daySeconds": day_seconds,
                        "updatedAt": dt.datetime.now(dt.UTC),
                        "metadata.localImportGuard": True,
                    }
                },
            )
            closed += 1

        return closed

    def _close_break_sessions(self) -> int:
        closed = 0

        for session in self.db.break_sessions.find({}, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            started_at = _coerce_datetime(session.get("startedAt"))
            time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"

            if not started_at:
                continue

            break_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
            ended_at = self._close_time(raw_author, break_date, started_at)
            break_seconds = max(0, int((ended_at - started_at).total_seconds()))
            self.db.break_sessions.delete_one({"telegramUsername": session.get("telegramUsername")})
            self.db.break_intervals.insert_one(
                {
                    "telegramUsername": str(session.get("telegramUsername") or ""),
                    "rawAuthor": raw_author,
                    "startedAt": started_at,
                    "endedAt": ended_at,
                    "date": break_date,
                    "timeZoneId": time_zone_id,
                    "breakSeconds": break_seconds,
                    "metadata": {"localImportGuard": True},
                }
            )
            closed += 1

        return closed

    def _close_meeting_sessions(self) -> int:
        closed = 0

        for session in self.db.meeting_sessions.find({}, {"_id": 0}):
            raw_author = str(session.get("rawAuthor") or "Unknown User")
            started_at = _coerce_datetime(session.get("startedAt"))
            time_zone_id = _valid_time_zone_id(session.get("timeZoneId")) or "UTC"

            if not started_at:
                continue

            meeting_date = str(session.get("date") or _telegram_event_date(started_at, time_zone_id))
            ended_at = self._close_time(raw_author, meeting_date, started_at)
            meeting_seconds = max(0, int((ended_at - started_at).total_seconds()))
            self.db.meeting_sessions.delete_one({"discordUserId": session.get("discordUserId")})
            self.db.meeting_intervals.insert_one(
                {
                    "discordUserId": str(session.get("discordUserId") or ""),
                    "discordUsername": str(session.get("discordUsername") or ""),
                    "rawAuthor": raw_author,
                    "guildId": str(session.get("guildId") or ""),
                    "channelId": str(session.get("channelId") or ""),
                    "startedAt": started_at,
                    "endedAt": ended_at,
                    "date": meeting_date,
                    "timeZoneId": time_zone_id,
                    "meetingSeconds": meeting_seconds,
                    "createdAt": dt.datetime.now(dt.UTC),
                    "metadata": {"localImportGuard": True},
                }
            )
            closed += 1

        return closed

    def _close_status_events(self) -> int:
        closed = 0
        pipeline = [
            {"$sort": {"transitionAt": 1}},
            {"$group": {"_id": "$rawAuthor", "last": {"$last": "$$ROOT"}}},
        ]

        for item in self.db.status_events.aggregate(pipeline):
            event = item.get("last") or {}

            if str(event.get("statusEventType") or "") != "offline":
                continue

            raw_author = str(event.get("rawAuthor") or "Unknown User")
            transition_at = _coerce_datetime(event.get("transitionAt"))
            time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or "UTC"
            event_date = str(event.get("date") or "")

            if not transition_at or not event_date:
                continue

            ended_at = self._close_time(raw_author, event_date, transition_at)
            reason = str(event.get("reason") or "")
            close_reason = "reports_resumed" if reason == "reports_stopped" else reason or "local_import_guard"
            close_event = {
                "rawAuthor": raw_author,
                "date": _telegram_event_date(ended_at, time_zone_id),
                "statusEventType": "online",
                "transitionAt": ended_at,
                "receivedAt": ended_at,
                "timeZoneId": time_zone_id,
                "reason": close_reason,
                "createdAt": dt.datetime.now(dt.UTC),
                "metadata": {"localImportGuard": True},
            }
            self.db.status_events.update_one(
                {
                    "rawAuthor": raw_author,
                    "date": close_event["date"],
                    "statusEventType": "online",
                    "transitionAt": ended_at,
                },
                {"$setOnInsert": close_event},
                upsert=True,
            )
            self.services._insert_status_report_row(close_event)
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "online", "updatedAt": ended_at, "transitionAt": ended_at}},
                upsert=True,
            )
            closed += 1

        return closed

    def _close_time(self, raw_author: str, day_date: str, started_at: dt.datetime) -> dt.datetime:
        latest_at = self._latest_imported_timestamp(raw_author, day_date)

        if latest_at and latest_at > started_at:
            return latest_at

        return started_at

    def _latest_imported_timestamp(self, raw_author: str, day_date: str) -> dt.datetime | None:
        latest_at: dt.datetime | None = None

        def remember(value: Any) -> None:
            nonlocal latest_at
            timestamp = _coerce_datetime(value)

            if timestamp and (latest_at is None or timestamp > latest_at):
                latest_at = timestamp

        for row in self.db.report_rows.find(
            {"author": raw_author, "date": day_date, "metadata.live": {"$ne": True}},
            {"_id": 0, "receivedAt": 1, "recordedAt": 1, "lastRecordedAt": 1},
        ):
            remember(row.get("receivedAt"))
            remember(row.get("recordedAt"))
            remember(row.get("lastRecordedAt"))

        for event in self.db.raw_activity_events.find(
            {"author": raw_author, "date": day_date},
            {"_id": 0, "receivedAt": 1, "occurredAtUtc": 1, "occurredAtLocal": 1},
        ):
            remember(event.get("receivedAt"))
            remember(event.get("occurredAtUtc"))
            remember(event.get("occurredAtLocal"))

        for event in self.db.break_events.find({"rawAuthor": raw_author, "date": day_date}, {"_id": 0, "timestamp": 1, "receivedAt": 1}):
            remember(event.get("timestamp"))
            remember(event.get("receivedAt"))

        for event in self.db.meeting_events.find({"rawAuthor": raw_author, "date": day_date}, {"_id": 0, "timestamp": 1, "receivedAt": 1}):
            remember(event.get("timestamp"))
            remember(event.get("receivedAt"))

        for event in self.db.status_events.find({"rawAuthor": raw_author, "date": day_date}, {"_id": 0, "transitionAt": 1, "receivedAt": 1}):
            remember(event.get("transitionAt"))
            remember(event.get("receivedAt"))

        return latest_at
