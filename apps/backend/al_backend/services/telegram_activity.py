from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..activity_math import *
from ..hourly_fill_rules import empty_hourly_activity
from ..backend_composable_host import composed
from ..mongo_composable import MongoComposableMixin


def _event_clock_local(value: dt.datetime | None, time_zone_id: str) -> str:
    if value is None:
        return "unknown time"

    zone_name = (_valid_time_zone_id(time_zone_id) or "").strip()

    try:
        if zone_name:
            localized = value.astimezone(ZoneInfo(zone_name))
        else:
            localized = value.astimezone(dt.UTC)
    except ZoneInfoNotFoundError:
        localized = value.astimezone(dt.UTC)

    return localized.strftime("%H:%M")


def _is_current_or_previous_author_date(day_date: str, now: dt.datetime, profile: dict[str, Any]) -> bool:
    time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"

    try:
        zone = ZoneInfo(time_zone_id)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    local_date = now.astimezone(zone).date()
    return day_date in {local_date.isoformat(), (local_date - dt.timedelta(days=1)).isoformat()}


def _is_night_overtime_prompt_time(value: dt.datetime, time_zone_id: Any) -> bool:
    zone_name = _valid_time_zone_id(time_zone_id) or "UTC"

    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        zone = dt.UTC

    return 0 <= value.astimezone(zone).hour < 7


class TelegramActivityService(MongoComposableMixin):
    def _supersede_open_duplicate_afk_prompts(self, telegram_username: str, break_started_at: dt.datetime | None) -> None:
        if not break_started_at:
            return

        now = dt.datetime.now(dt.UTC)

        self.db.telegram_duplicate_afk_prompts.update_many(
            {"telegramUsername": telegram_username, "breakStartedAt": break_started_at, "status": {"$in": ["pending_send", "sent"]}},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": now,
                    "closeAction": "break_closed_by_online",
                    "updatedAt": now,
                }
            },
        )

    def _close_open_duplicate_afk_prompts_after_online_day(self, raw_author: str, day_date: str) -> None:
        now = dt.datetime.now(dt.UTC)

        self.db.telegram_duplicate_afk_prompts.update_many(
            {"rawAuthor": raw_author, "date": day_date, "status": {"$in": ["pending_send", "sent"]}},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": now,
                    "closeAction": "cancelled_by_online",
                    "updatedAt": now,
                }
            },
        )

    def mark_telegram_duplicate_afk_prompt_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        self.db.telegram_duplicate_afk_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def close_telegram_duplicate_afk_prompt(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"confirm_online", "still_afk"} else "still_afk"
        reminder = self.db.telegram_duplicate_afk_prompts.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder", "status": "unknown_reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": "duplicate_afk_prompt_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)
        received_at = dt.datetime.now(dt.UTC)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        if action == "still_afk":
            self.db.telegram_duplicate_afk_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "still_afk",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "duplicate_afk_prompt_still_afk"}

        resolved_ts = timestamp if timestamp else received_at.isoformat()
        break_result = self.record_break_event(telegram_username, "online", resolved_ts)

        if not break_result.get("ok"):
            return {**break_result, "status": str(break_result.get("status") or "duplicate_afk_online_failed")}

        result_status = break_result.get("status")

        if result_status == "duplicate_online":
            self.db.telegram_duplicate_afk_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "closed_duplicate_online_via_confirm",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "duplicate_afk_prompt_closed_already_online"}

        if result_status != "break_closed":
            return {
                "ok": False,
                "status": str(result_status or "unexpected_online_result"),
                "error": break_result.get("error") or str(result_status),
            }

        self.db.telegram_duplicate_afk_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": received_at,
                    "closeAction": "confirm_online",
                    "updatedAt": received_at,
                }
            },
        )

        return {**break_result, "ok": True, "status": "duplicate_afk_prompt_confirmed_online"}

    def record_break_event(
        self,
        telegram_username: str,
        event_type: str,
        timestamp: str | None = None,
        *,
        ingest_received_at: dt.datetime | None = None,
    ) -> dict[str, Any]:
        normalized_telegram = _normalize_telegram_username(telegram_username)
        event_time = _parse_timestamp(timestamp)
        audit_now = dt.datetime.now(dt.UTC)
        row_received_at = ingest_received_at if ingest_received_at is not None else audit_now
        profile = self.db.author_profiles.find_one({"telegramUsername": normalized_telegram})

        if not profile:
            return {"ok": False, "error": "Unknown telegram username"}

        raw_author = profile["rawAuthor"]
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)

        if event_type == "offline":
            day_probe = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": event_date}, {"_id": 0})
            probe_offline_at = day_probe.get("lastOfflineAt") if day_probe else None
            probe_offline_dt = _coerce_datetime(probe_offline_at)

            if day_probe is not None and probe_offline_dt is not None:
                return {
                    "ok": True,
                    "status": "duplicate_offline",
                    "lastOfflineAt": probe_offline_dt.isoformat(),
                    "sinceOfflineTimeLocal": _event_clock_local(probe_offline_dt, time_zone_id),
                    "timeZoneId": time_zone_id,
                }

        if event_type == "online":
            day_probe_on = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": event_date}, {"_id": 0})
            break_probe_on = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

            if day_probe_on and not day_probe_on.get("lastOfflineAt") and not break_probe_on:
                started_online = _coerce_datetime(day_probe_on.get("startedAt")) or event_time

                return {
                    "ok": True,
                    "status": "duplicate_online",
                    "dayStartedAt": started_online.isoformat(),
                    "sinceTimeLocal": _event_clock_local(started_online, time_zone_id),
                    "timeZoneId": time_zone_id,
                }

        if event_type == "afk":
            break_probe_afk = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

            if break_probe_afk:
                now_close = audit_now

                self.db.telegram_duplicate_afk_prompts.update_many(
                    {"rawAuthor": raw_author, "telegramUsername": normalized_telegram, "status": {"$in": ["pending_send", "sent"]}},
                    {"$set": {"status": "closed", "closedAt": now_close, "closeAction": "superseded", "updatedAt": now_close}},
                )

                dup_reminder_id = _new_id()
                break_afk_started = _coerce_datetime(break_probe_afk.get("startedAt")) or event_time
                sess_tz = _valid_time_zone_id(break_probe_afk.get("timeZoneId")) or time_zone_id

                self.db.telegram_duplicate_afk_prompts.insert_one(
                    {
                        "reminderId": dup_reminder_id,
                        "rawAuthor": raw_author,
                        "telegramUsername": normalized_telegram,
                        "date": event_date,
                        "breakStartedAt": break_afk_started,
                        "timeZoneId": sess_tz,
                        "status": "pending_send",
                        "createdAt": audit_now,
                        "updatedAt": audit_now,
                    }
                )

                return {
                    "ok": True,
                    "status": "duplicate_afk",
                    "reminderId": dup_reminder_id,
                    "breakStartedAt": break_afk_started.isoformat(),
                    "afkStartedTimeLocal": _event_clock_local(break_afk_started, sess_tz),
                    "timeZoneId": sess_tz,
                }

        composed(self).invalidate_activity_summary_cache([event_date])
        self.db.break_events.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": audit_now,
            }
        )

        if event_type == "afk":
            self.db.break_sessions.update_one(
                {"telegramUsername": normalized_telegram},
                {
                    "$set": {
                        "telegramUsername": normalized_telegram,
                        "rawAuthor": raw_author,
                        "startedAt": event_time,
                        "date": event_date,
                        "timeZoneId": time_zone_id,
                    }
                },
                upsert=True,
            )
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, row_received_at, "break_started")
            return {"ok": True, "status": "break_started"}

        if event_type == "offline":
            break_result = self._close_break_session(normalized_telegram, raw_author, event_time)
            day_date = event_date
            day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})

            if not day_state:
                self._insert_telegram_report_row(
                    raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, row_received_at, "offline_without_online", break_result
                )
                return {"ok": True, "status": "offline_without_online", **break_result}

            started_at = _coerce_datetime(day_state["startedAt"]) or event_time
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"lastOfflineAt": event_time, "daySeconds": day_seconds}},
                upsert=True,
            )
            self._upsert_telegram_day_activity(raw_author, normalized_telegram, day_date, time_zone_id, started_at, event_time, row_received_at, day_seconds)
            self._insert_telegram_report_row(
                raw_author,
                normalized_telegram,
                event_type,
                event_time,
                event_date,
                time_zone_id,
                row_received_at,
                "day_closed",
                {"daySeconds": day_seconds, **break_result},
            )
            return {"ok": True, "status": "day_closed", "daySeconds": day_seconds, **break_result}

        online_date = event_date
        self.db.day_sessions.update_one(
            {"rawAuthor": raw_author, "date": online_date},
            {
                "$setOnInsert": {
                    "telegramUsername": normalized_telegram,
                    "rawAuthor": raw_author,
                    "date": online_date,
                    "startedAt": event_time,
                    "daySeconds": 0,
                    "timeZoneId": time_zone_id,
                },
                "$set": {"lastOnlineAt": event_time},
            },
            upsert=True,
        )

        self._invalidate_telegram_online_prompts_for_online_day(raw_author, online_date)
        self._close_open_duplicate_afk_prompts_after_online_day(raw_author, online_date)

        pending_break_snapshot = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})
        break_started_snap = _coerce_datetime((pending_break_snapshot or {}).get("startedAt"))
        break_result = self._close_break_session(normalized_telegram, raw_author, event_time)

        if break_result:
            self._supersede_open_duplicate_afk_prompts(normalized_telegram, break_started_snap)

        if not break_result:
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, row_received_at, "online_recorded")
            return {"ok": True, "status": "online_recorded"}

        self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, row_received_at, "break_closed", break_result)
        return {"ok": True, "status": "break_closed", **break_result}

    def claim_due_telegram_day_reminders(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        reminders: list[dict[str, Any]] = []
        profiles = self._profiles_by_raw_author()

        for session in self.db.day_sessions.find({}, {"_id": 0}):
            if session.get("lastOfflineAt"):
                continue

            raw_author = str(session.get("rawAuthor") or "")
            telegram_username = _normalize_telegram_username(session.get("telegramUsername") or profiles.get(raw_author, {}).get("telegramUsername"))
            day_date = str(session.get("date") or "")
            started_at = _coerce_datetime(session.get("startedAt"))

            if not raw_author or not telegram_username or not day_date or not started_at:
                continue

            if not _is_current_or_previous_author_date(day_date, now, profiles.get(raw_author, {})):
                continue

            elapsed_seconds = max(0, int((now - started_at).total_seconds()))

            if elapsed_seconds < TELEGRAM_DAY_REMINDER_SECONDS:
                continue

            reminder_key = {"rawAuthor": raw_author, "date": day_date}
            current = self.db.telegram_day_reminders.find_one(reminder_key, {"_id": 0}) or {}

            if current.get("status") in {"sent", "closed"}:
                continue

            reminder_id = str(current.get("reminderId") or _new_id())
            self.db.telegram_day_reminders.update_one(
                reminder_key,
                {
                    "$set": {
                        **reminder_key,
                        "reminderId": reminder_id,
                        "telegramUsername": telegram_username,
                        "startedAt": started_at,
                        "elapsedSeconds": elapsed_seconds,
                        "status": "claimed",
                        "lastClaimedAt": now,
                    }
                },
                upsert=True,
            )
            reminders.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": telegram_username,
                    "date": day_date,
                    "startedAt": started_at.isoformat(),
                    "elapsedSeconds": elapsed_seconds,
                }
            )

        return reminders

    def mark_telegram_day_reminder_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_day_reminders.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def close_telegram_day_from_reminder(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"offline", "overtime"} else "offline"
        reminder = self.db.telegram_day_reminders.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": f"reminder_{reminder.get('closeAction') or action}_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        raw_author = str(reminder.get("rawAuthor") or "")
        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        day_date = str(reminder.get("date") or "")
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0}) or {}
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or _valid_time_zone_id(reminder.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)

        if not raw_author or not telegram_username or not day_date:
            return {"ok": False, "error": "Reminder is missing author data"}

        self.db.break_events.insert_one(
            {
                "telegramUsername": telegram_username,
                "rawAuthor": raw_author,
                "eventType": "offline",
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
                "source": "telegram_reminder",
                "reminderAction": action,
            }
        )
        break_result = self._close_break_session(telegram_username, raw_author, event_time)
        day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})
        metadata: dict[str, Any] = {"reminderAction": action, **break_result}

        if not day_state:
            self._insert_telegram_report_row(
                raw_author,
                telegram_username,
                "offline",
                event_time,
                event_date,
                time_zone_id,
                received_at,
                f"reminder_{action}_without_online",
                metadata,
            )
        else:
            started_at = _coerce_datetime(day_state.get("startedAt")) or event_time
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            metadata["daySeconds"] = day_seconds
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"date": event_date, "lastOfflineAt": event_time, "daySeconds": day_seconds, "reminderAction": action}},
                upsert=True,
            )
            self._upsert_telegram_day_activity(raw_author, telegram_username, event_date, time_zone_id, started_at, event_time, received_at, day_seconds)
            self._insert_telegram_report_row(
                raw_author,
                telegram_username,
                "offline",
                event_time,
                event_date,
                time_zone_id,
                received_at,
                f"reminder_{action}",
                metadata,
            )

        self.db.telegram_day_reminders.update_one(
            {"reminderId": reminder_id},
            {"$set": {"status": "closed", "closedAt": received_at, "closeAction": action, "updatedAt": received_at}},
        )
        return {"ok": True, "status": f"reminder_{action}", **metadata}

    def _schedule_telegram_online_prompt_if_needed(
        self, raw_author: str, day_date: str, source: str, received_at: dt.datetime
    ) -> None:
        if not raw_author or not day_date:
            return

        _ = source

        if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
            return

        if self.db.telegram_online_prompts.find_one(
            {"rawAuthor": raw_author, "date": day_date, "status": {"$in": ["pending", "claimed", "sent"]}},
            {"_id": 1},
        ):
            return

        profile = self.db.author_profiles.find_one(
            {"rawAuthor": raw_author},
            {"_id": 0, "telegramUsername": 1, "timeZoneId": 1},
        ) or {}
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))

        if not telegram_username:
            return

        if _is_night_overtime_prompt_time(received_at, profile.get("timeZoneId")):
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.insert_one(
            {
                "reminderId": _new_id(),
                "rawAuthor": raw_author,
                "date": day_date,
                "telegramUsername": telegram_username,
                "firstReportReceivedAt": received_at,
                "status": "pending",
                "createdAt": now,
                "updatedAt": now,
            }
        )

    def _invalidate_telegram_online_prompts_for_online_day(self, raw_author: str, day_date: str) -> None:
        if not raw_author or not day_date:
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.update_many(
            {
                "rawAuthor": raw_author,
                "date": day_date,
                "status": {"$in": ["pending", "claimed", "sent"]},
            },
            {
                "$set": {
                    "status": "closed",
                    "closedAt": now,
                    "closeAction": "cancelled_by_online",
                    "updatedAt": now,
                }
            },
        )
        self.db.telegram_break_activity_prompts.update_many(
            {
                "rawAuthor": raw_author,
                "date": day_date,
                "status": {"$in": ["pending", "claimed", "sent"]},
            },
            {
                "$set": {
                    "status": "closed",
                    "closedAt": now,
                    "closeAction": "cancelled_by_online",
                    "updatedAt": now,
                }
            },
        )
        self._close_open_duplicate_afk_prompts_after_online_day(raw_author, day_date)

    def _schedule_telegram_break_activity_prompt_if_needed(
        self,
        raw_author: str,
        day_date: str,
        source: str,
        report_time: dt.datetime,
    ) -> None:
        if source in {"telegram", "discord"} or not raw_author or not day_date:
            return

        session = self.db.break_sessions.find_one({"rawAuthor": raw_author}, {"_id": 0})

        if not session:
            return

        started_at = _coerce_datetime(session.get("startedAt"))

        if not started_at or report_time < started_at:
            return

        if (report_time - started_at).total_seconds() < TELEGRAM_BREAK_ACTIVITY_PROMPT_DELAY_SECONDS:
            return

        if self.db.telegram_break_activity_prompts.find_one(
            {
                "rawAuthor": raw_author,
                "breakStartedAt": started_at,
                "status": {"$in": ["pending", "claimed", "sent", "closed"]},
            },
            {"_id": 1},
        ):
            return

        telegram_username = _normalize_telegram_username(
            session.get("telegramUsername")
            or (self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0, "telegramUsername": 1}) or {}).get("telegramUsername")
        )

        if not telegram_username:
            return

        now = dt.datetime.now(dt.UTC)
        self.db.telegram_break_activity_prompts.insert_one(
            {
                "reminderId": _new_id(),
                "rawAuthor": raw_author,
                "date": day_date,
                "telegramUsername": telegram_username,
                "breakStartedAt": started_at,
                "firstReportReceivedAt": report_time,
                "timeZoneId": session.get("timeZoneId"),
                "status": "pending",
                "createdAt": now,
                "updatedAt": now,
            }
        )

    def claim_due_telegram_online_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        due: list[dict[str, Any]] = []

        for doc in list(self.db.telegram_online_prompts.find({"status": "pending"}, {"_id": 0})):
            raw_author = str(doc.get("rawAuthor") or "")
            day_date = str(doc.get("date") or "")
            anchor = _coerce_datetime(doc.get("firstReportReceivedAt"))

            if not raw_author or not day_date or not anchor:
                continue

            profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0, "timeZoneId": 1}) or {}

            if not _is_current_or_previous_author_date(day_date, now, profile):
                self.db.telegram_online_prompts.update_one(
                    {"reminderId": doc.get("reminderId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closedAt": now,
                            "closeAction": "stale_date",
                            "updatedAt": now,
                        }
                    },
                )
                continue

            delay_seconds = composed(self).get_telegram_online_prompt_delay_seconds()

            if (now - anchor).total_seconds() < delay_seconds:
                continue

            if self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
                self.db.telegram_online_prompts.update_one(
                    {"reminderId": doc.get("reminderId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closedAt": now,
                            "closeAction": "superseded_day_session",
                            "updatedAt": now,
                        }
                    },
                )
                continue

            reminder_id = str(doc.get("reminderId") or "")
            self.db.telegram_online_prompts.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            due.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": str(doc.get("telegramUsername") or ""),
                    "date": day_date,
                    "firstReportReceivedAt": anchor.isoformat(),
                }
            )

        return due

    def mark_telegram_online_prompt_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_online_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def claim_due_telegram_break_activity_prompts(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        due: list[dict[str, Any]] = []

        for doc in list(self.db.telegram_break_activity_prompts.find({"status": "pending"}, {"_id": 0})):
            raw_author = str(doc.get("rawAuthor") or "")
            break_started_at = _coerce_datetime(doc.get("breakStartedAt"))

            if not raw_author or not break_started_at:
                continue

            session = self.db.break_sessions.find_one({"rawAuthor": raw_author}, {"_id": 0})
            session_started_at = _coerce_datetime((session or {}).get("startedAt"))

            if not session or session_started_at != break_started_at:
                self.db.telegram_break_activity_prompts.update_one(
                    {"reminderId": doc.get("reminderId")},
                    {
                        "$set": {
                            "status": "closed",
                            "closedAt": now,
                            "closeAction": "superseded_break_session",
                            "updatedAt": now,
                        }
                    },
                )
                continue

            reminder_id = str(doc.get("reminderId") or "")
            self.db.telegram_break_activity_prompts.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            due.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": raw_author,
                    "telegramUsername": str(doc.get("telegramUsername") or ""),
                    "date": str(doc.get("date") or ""),
                    "breakStartedAt": break_started_at.isoformat(),
                    "firstReportReceivedAt": _iso(doc.get("firstReportReceivedAt")),
                    "timeZoneId": doc.get("timeZoneId"),
                }
            )

        return due

    def mark_telegram_break_activity_prompt_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_break_activity_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )
        return {"ok": True}

    def _first_plugin_report_recorded_at_for_day(self, raw_author: str, day_date: str) -> dt.datetime | None:
        if not raw_author or not day_date:
            return None

        rows = list(
            self.db.report_rows.find(
                {
                    "author": raw_author,
                    "date": day_date,
                    "source": {"$nin": ["telegram", "discord", "status"]},
                },
                {"_id": 0, "recordedAt": 1, "lastRecordedAt": 1},
            )
            .sort([("recordedAt", ASCENDING)])
            .limit(1)
        )

        if not rows:
            return None

        row = rows[0]
        return _coerce_datetime(row.get("recordedAt")) or _coerce_datetime(row.get("lastRecordedAt"))

    def close_telegram_online_prompt(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"confirm_online", "dismiss"} else "dismiss"
        reminder = self.db.telegram_online_prompts.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder", "status": "unknown_reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": "online_prompt_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        raw_author = str(reminder.get("rawAuthor") or "")
        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)
        received_at = dt.datetime.now(dt.UTC)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        if action == "dismiss":
            day_date = str(reminder.get("date") or "")
            purge_result = composed(self).purge_editor_plugin_activity_for_author_day(raw_author, day_date)
            self.db.telegram_online_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "dismiss",
                        "updatedAt": received_at,
                    }
                },
            )
            response: dict[str, Any] = {"ok": True, "status": "online_prompt_dismissed"}

            if purge_result.get("ok"):
                response["deletedRawActivityEvents"] = purge_result["deletedRawActivityEvents"]
                response["deletedRawEventBatches"] = purge_result["deletedRawEventBatches"]
                response["deletedRawReports"] = purge_result["deletedRawReports"]
                response["deletedActivitySnapshots"] = purge_result["deletedActivitySnapshots"]
                response["deletedStatusEvents"] = purge_result["deletedStatusEvents"]
                response["deletedStatusReportRows"] = purge_result["deletedStatusReportRows"]
                response["purgeRebuildDates"] = purge_result.get("purgeRebuildDates")
                response["purgeRebuildAuthors"] = purge_result.get("purgeRebuildAuthors")
            else:
                response["purgeError"] = purge_result.get("error")

            return response

        aligned_at = _parse_timestamp(timestamp)

        if aligned_at.tzinfo is None:
            aligned_at = aligned_at.replace(tzinfo=dt.UTC)
        else:
            aligned_at = aligned_at.astimezone(dt.UTC)

        timestamp_str = aligned_at.isoformat().replace("+00:00", "Z")
        break_result = self.record_break_event(telegram_username, "online", timestamp_str, ingest_received_at=aligned_at)

        if break_result.get("status") == "duplicate_online":
            self.db.telegram_online_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "closed_duplicate_online_confirm",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "online_prompt_closed_duplicate_online", **{k: v for k, v in break_result.items() if k != "ok"}}

        if not break_result.get("ok"):
            return {**break_result, "status": str(break_result.get("status") or "online_failed")}

        self.db.telegram_online_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": received_at,
                    "closeAction": "confirm_online",
                    "updatedAt": received_at,
                }
            },
        )
        return {**break_result, "ok": True, "status": "online_prompt_confirmed_online"}

    def close_telegram_break_activity_prompt(
        self,
        reminder_id: str,
        action: str,
        timestamp: str | None = None,
        actor_telegram_username: str | None = None,
    ) -> dict[str, Any]:
        action = action if action in {"confirm_online", "still_afk"} else "still_afk"
        reminder = self.db.telegram_break_activity_prompts.find_one({"reminderId": reminder_id}, {"_id": 0})

        if not reminder:
            return {"ok": False, "error": "Unknown reminder", "status": "unknown_reminder"}

        if reminder.get("status") == "closed":
            return {
                "ok": True,
                "status": "break_activity_prompt_already_closed",
                "reminderAction": reminder.get("closeAction") or action,
            }

        telegram_username = _normalize_telegram_username(reminder.get("telegramUsername"))
        actor_telegram_username = _normalize_telegram_username(actor_telegram_username)
        received_at = dt.datetime.now(dt.UTC)

        if actor_telegram_username and telegram_username and actor_telegram_username != telegram_username:
            return {"ok": False, "error": "Reminder belongs to another Telegram user", "status": "wrong_user"}

        if action == "still_afk":
            self.db.telegram_break_activity_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "still_afk",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "break_activity_prompt_still_afk"}

        break_result = self.record_break_event(telegram_username, "online", timestamp)

        if break_result.get("status") == "duplicate_online":
            self.db.telegram_break_activity_prompts.update_one(
                {"reminderId": reminder_id},
                {
                    "$set": {
                        "status": "closed",
                        "closedAt": received_at,
                        "closeAction": "closed_duplicate_online_confirm",
                        "updatedAt": received_at,
                    }
                },
            )
            return {"ok": True, "status": "break_activity_prompt_closed_duplicate_online", **{k: v for k, v in break_result.items() if k != "ok"}}

        if not break_result.get("ok"):
            return {**break_result, "status": str(break_result.get("status") or "online_failed")}

        self.db.telegram_break_activity_prompts.update_one(
            {"reminderId": reminder_id},
            {
                "$set": {
                    "status": "closed",
                    "closedAt": received_at,
                    "closeAction": "confirm_online",
                    "updatedAt": received_at,
                }
            },
        )
        return {**break_result, "ok": True, "status": "break_activity_prompt_confirmed_online"}

    def _upsert_telegram_day_activity(
        self,
        raw_author: str,
        telegram_username: str,
        event_date: str,
        time_zone_id: str,
        started_at: dt.datetime,
        ended_at: dt.datetime,
        received_at: dt.datetime,
        day_seconds: int,
    ) -> None:
        key = {"source": "telegram", "author": raw_author, "projectId": "telegram", "date": event_date}
        self.db.daily_author_activity.update_one(
            key,
            {
                "$setOnInsert": {
                    **key,
                    "authorEmail": "",
                    "pluginVersion": "telegram-bot",
                    "workWindowSeconds": DEFAULT_PLUGIN_WORK_WINDOW_SECONDS,
                    "activityCounts": [],
                    "savedPrefabs": [],
                    "overtimeActivityCounts": [],
                    "overtimeSavedPrefabs": [],
                    "hourlyActivity": empty_hourly_activity(),
                    "activeSeconds": 0,
                    "activeMicroseconds": 0,
                    "idleSeconds": 0,
                    "idleMicroseconds": 0,
                    "overtimeActiveSeconds": 0,
                    "overtimeActiveMicroseconds": 0,
                },
                "$set": {
                    "sessionId": telegram_username,
                    "timeZoneId": time_zone_id,
                    "timeZoneDisplayName": time_zone_id,
                    "lastRecordedAt": ended_at.isoformat(),
                    "lastReceivedAt": received_at,
                    "dayStartedAt": started_at,
                    "dayEndedAt": ended_at,
                    "daySeconds": day_seconds,
                },
            },
            upsert=True,
        )

    def _insert_telegram_report_row(
        self,
        raw_author: str,
        telegram_username: str,
        event_type: str,
        event_time: dt.datetime,
        event_date: str,
        time_zone_id: str,
        received_at: dt.datetime,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        materialize_predicate = getattr(composed(self), "_should_materialize_aggregate_date", None)

        if callable(materialize_predicate) and not materialize_predicate(event_date, raw_author):
            return

        deltas = _empty_event_deltas()
        self.db.report_rows.insert_one(
            {
                "source": "telegram",
                "pluginVersion": "telegram-bot",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "telegram",
                "sessionId": telegram_username,
                "deviceId": "",
                "date": event_date,
                "recordedAt": event_time.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": event_time.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "telegram",
                "activityType": f"telegram_{event_type}",
                "telegramEventType": event_type,
                "telegramStatus": status,
                "telegramUsername": telegram_username,
                "metadata": metadata or {},
                **deltas,
            }
        )
