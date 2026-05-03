from __future__ import annotations

from ..activity_math import *


class TelegramActivityService:
    def record_break_event(self, telegram_username: str, event_type: str, timestamp: str | None = None) -> dict[str, Any]:
        normalized_telegram = _normalize_telegram_username(telegram_username)
        event_time = _parse_timestamp(timestamp)
        received_at = dt.datetime.now(dt.UTC)
        profile = self.db.author_profiles.find_one({"telegramUsername": normalized_telegram})

        if not profile:
            return {"ok": False, "error": "Unknown telegram username"}

        raw_author = profile["rawAuthor"]
        time_zone_id = _valid_time_zone_id(profile.get("timeZoneId")) or "UTC"
        event_date = _telegram_event_date(event_time, time_zone_id)
        self.db.break_events.insert_one(
            {
                "telegramUsername": normalized_telegram,
                "rawAuthor": raw_author,
                "eventType": event_type,
                "timestamp": event_time,
                "date": event_date,
                "timeZoneId": time_zone_id,
                "createdAt": received_at,
            }
        )

        if event_type == "afk":
            session = self.db.break_sessions.find_one({"telegramUsername": normalized_telegram})

            if session:
                self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_already_started")
                return {"ok": True, "status": "break_already_started"}

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
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_started")
            return {"ok": True, "status": "break_started"}

        if event_type == "offline":
            break_result = self._close_break_session(normalized_telegram, raw_author, event_time)
            day_date = event_date
            day_state = self.db.day_sessions.find_one({"rawAuthor": raw_author, "date": day_date})

            if not day_state:
                self._insert_telegram_report_row(
                    raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "offline_without_online", break_result
                )
                return {"ok": True, "status": "offline_without_online", **break_result}

            started_at = _coerce_datetime(day_state["startedAt"]) or event_time
            day_seconds = max(0, int((event_time - started_at).total_seconds()))
            self.db.day_sessions.update_one(
                {"rawAuthor": raw_author, "date": day_date},
                {"$set": {"lastOfflineAt": event_time, "daySeconds": day_seconds}},
                upsert=True,
            )
            self._upsert_telegram_day_activity(raw_author, normalized_telegram, day_date, time_zone_id, started_at, event_time, received_at, day_seconds)
            self._insert_telegram_report_row(
                raw_author,
                normalized_telegram,
                event_type,
                event_time,
                event_date,
                time_zone_id,
                received_at,
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

        break_result = self._close_break_session(normalized_telegram, raw_author, event_time)

        if not break_result:
            self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "online_recorded")
            return {"ok": True, "status": "online_recorded"}

        self._insert_telegram_report_row(raw_author, normalized_telegram, event_type, event_time, event_date, time_zone_id, received_at, "break_closed", break_result)
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

        if self.db.telegram_online_prompts.find_one({"rawAuthor": raw_author, "date": day_date}, {"_id": 1}):
            return

        profile = self.db.author_profiles.find_one({"rawAuthor": raw_author}, {"_id": 0, "telegramUsername": 1}) or {}
        telegram_username = _normalize_telegram_username(profile.get("telegramUsername"))

        if not telegram_username:
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

            if (now - anchor).total_seconds() < TELEGRAM_ONLINE_PROMPT_DELAY_SECONDS:
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

    def claim_due_telegram_meeting_auto_afk_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        notifications: list[dict[str, Any]] = []

        for doc in self.db.telegram_meeting_auto_afk_notifications.find({"status": "pending"}, {"_id": 0}):
            reminder_id = str(doc.get("reminderId") or "")

            if not reminder_id:
                continue

            self.db.telegram_meeting_auto_afk_notifications.update_one(
                {"reminderId": reminder_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            notifications.append(
                {
                    "reminderId": reminder_id,
                    "rawAuthor": doc.get("rawAuthor"),
                    "telegramUsername": doc.get("telegramUsername"),
                    "soloStartedAt": _isoformat_or_none(doc.get("soloStartedAt")),
                    "movedAt": _isoformat_or_none(doc.get("movedAt")),
                    "timeZoneId": doc.get("timeZoneId"),
                    "excludedSeconds": int(doc.get("excludedSeconds", 0)),
                    "thresholdSeconds": int(doc.get("thresholdSeconds") or doc.get("excludedSeconds") or 0),
                }
            )

        return notifications

    def mark_telegram_meeting_auto_afk_notification_sent(self, reminder_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        self.db.telegram_meeting_auto_afk_notifications.update_one(
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

    def claim_due_telegram_meeting_summary_notifications(self, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        now = now or dt.datetime.now(dt.UTC)
        notifications: list[dict[str, Any]] = []

        for doc in self.db.meeting_summaries.find({"status": "pending"}, {"_id": 0}):
            summary_id = str(doc.get("summaryId") or "")

            if not summary_id:
                continue

            self.db.meeting_summaries.update_one(
                {"summaryId": summary_id},
                {"$set": {"status": "claimed", "lastClaimedAt": now, "updatedAt": now}},
            )
            recording_id = str(doc.get("recordingId") or "")

            if recording_id:
                self._update_meeting_recording_pipeline_status(recording_id, "telegram_claimed", updated_at=now)

            notifications.append(
                {
                    "summaryId": summary_id,
                    "recordingId": doc.get("recordingId"),
                    "participantNames": doc.get("participantNames", []),
                    "startedAt": _isoformat_or_none(doc.get("startedAt")),
                    "endedAt": _isoformat_or_none(doc.get("endedAt")),
                    "durationSeconds": int(doc.get("durationSeconds", 0)),
                    "summary": doc.get("summary", ""),
                    "recipient": self._meeting_summary_recipient(),
                }
            )

        return notifications

    def _meeting_summary_recipient(self) -> dict[str, Any]:
        recipient = self.get_discord_settings()["meetingSummaryRecipient"]

        if recipient == "work_chat":
            return {"kind": "work_chat"}

        profile = self.db.author_profiles.find_one({"rawAuthor": recipient}, {"_id": 0, "telegramPrivateChatId": 1, "telegramUsername": 1})

        if profile and profile.get("telegramPrivateChatId"):
            return {
                "kind": "private",
                "rawAuthor": recipient,
                "telegramUsername": profile.get("telegramUsername", ""),
                "chatId": int(profile["telegramPrivateChatId"]),
            }

        return {"kind": "work_chat", "fallbackReason": "missing_private_chat"}

    def mark_telegram_meeting_summary_sent(self, summary_id: str, message_id: int | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        summary = self.db.meeting_summaries.find_one({"summaryId": summary_id}, {"_id": 0, "recordingId": 1})
        self.db.meeting_summaries.update_one(
            {"summaryId": summary_id},
            {
                "$set": {
                    "status": "sent",
                    "messageId": message_id,
                    "sentAt": now,
                    "updatedAt": now,
                }
            },
        )

        if summary and summary.get("recordingId"):
            self._update_meeting_recording_pipeline_status(str(summary["recordingId"]), "telegram_sent", updated_at=now)

        return {"ok": True}

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
            return {"ok": True, "status": "online_prompt_dismissed"}

        break_result = self.record_break_event(telegram_username, "online", timestamp)
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
                    "hourlyActivity": _empty_hourly_activity(),
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

    def record_status_event(
        self,
        raw_author: str,
        status_event_type: str,
        transition_at: dt.datetime,
        time_zone_id: str | None = None,
        reason: str = "reports_stopped",
        received_at: dt.datetime | None = None,
    ) -> dict[str, Any]:
        event_type = status_event_type if status_event_type in {"offline", "online"} else ""

        if not raw_author or not event_type:
            return {"ok": False, "error": "Status event requires author and offline/online type"}

        transition_at = _coerce_datetime(transition_at) or dt.datetime.now(dt.UTC)
        received_at = _coerce_datetime(received_at) or transition_at
        normalized_time_zone_id = _valid_time_zone_id(time_zone_id) or _author_configured_time_zone_id(raw_author) or "UTC"
        event_date = _telegram_event_date(transition_at, normalized_time_zone_id)
        event = {
            "rawAuthor": raw_author,
            "date": event_date,
            "statusEventType": event_type,
            "transitionAt": transition_at,
            "receivedAt": received_at,
            "timeZoneId": normalized_time_zone_id,
            "reason": reason,
            "createdAt": dt.datetime.now(dt.UTC),
        }
        self.db.status_events.update_one(
            {
                "rawAuthor": raw_author,
                "date": event_date,
                "statusEventType": event_type,
                "transitionAt": transition_at,
            },
            {"$setOnInsert": event},
            upsert=True,
        )
        self._insert_status_report_row(event)
        return {"ok": True, "event": event}

    def _record_status_transition_for_author(
        self,
        author: dict[str, Any],
        send_interval_seconds: int,
        now: dt.datetime,
        include_report_stopped_alerts: bool,
    ) -> None:
        raw_author = str(author.get("rawAuthor") or "")

        if not raw_author:
            return

        previous_state = self.db.status_states.find_one({"rawAuthor": raw_author}, {"_id": 0}) or {}
        previous_status = str(previous_state.get("status") or "online")
        stale_presence = str(author.get("stalePresence") or "")
        is_red_offline = (
            include_report_stopped_alerts
            and author.get("status") == "stale"
            and stale_presence in {"reports", "both"}
        )
        time_zone_id = _valid_time_zone_id(author.get("timeZoneId")) or _author_configured_time_zone_id(raw_author) or "UTC"

        if is_red_offline:
            if previous_status == "offline":
                self.db.status_states.update_one(
                    {"rawAuthor": raw_author},
                    {"$set": {"rawAuthor": raw_author, "status": "offline", "updatedAt": now}},
                    upsert=True,
                )
                return

            last_received_at = _coerce_datetime(author.get("lastReceivedAt")) or now
            transition_at = last_received_at + dt.timedelta(seconds=max(0, send_interval_seconds * 2) + 1)
            self.record_status_event(raw_author, "offline", transition_at, time_zone_id, "reports_stopped")
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "offline", "updatedAt": now, "transitionAt": transition_at}},
                upsert=True,
            )
            return

        if author.get("status") == "online" and previous_status == "offline" and self.get_plugin_ingest_enabled():
            transition_at = (_coerce_datetime(author.get("lastReceivedAt")) or now) + dt.timedelta(microseconds=1)
            self.record_status_event(raw_author, "online", transition_at, time_zone_id, "reports_resumed")
            self.db.status_states.update_one(
                {"rawAuthor": raw_author},
                {"$set": {"rawAuthor": raw_author, "status": "online", "updatedAt": now, "transitionAt": transition_at}},
                upsert=True,
            )

    def _materialize_status_report_rows(self) -> None:
        for event in self.db.status_events.find({}, {"_id": 0}).sort("transitionAt", ASCENDING):
            self._insert_status_report_row(event)

    def _insert_status_report_row(self, event: dict[str, Any]) -> None:
        raw_author = str(event.get("rawAuthor") or "Unknown User")
        transition_at = _coerce_datetime(event.get("transitionAt")) or dt.datetime.now(dt.UTC)
        received_at = _coerce_datetime(event.get("receivedAt")) or transition_at
        time_zone_id = _valid_time_zone_id(event.get("timeZoneId")) or _author_configured_time_zone_id(raw_author) or "UTC"
        event_type = str(event.get("statusEventType") or "")
        event_date = str(event.get("date") or _telegram_event_date(transition_at, time_zone_id))

        if event_type not in {"offline", "online"}:
            return

        self.db.report_rows.delete_many(
            {
                "source": "status",
                "author": raw_author,
                "date": event_date,
                "statusEventType": event_type,
                "recordedAt": transition_at.isoformat(),
            }
        )
        self.db.report_rows.insert_one(
            {
                "source": "status",
                "pluginVersion": "status",
                "author": raw_author,
                "authorEmail": "",
                "projectId": "status",
                "sessionId": raw_author,
                "deviceId": "",
                "date": event_date,
                "recordedAt": transition_at.isoformat(),
                "receivedAt": received_at,
                "lastRecordedAt": transition_at.isoformat(),
                "lastReceivedAt": received_at,
                "timeZoneId": time_zone_id,
                "timeZoneDisplayName": time_zone_id,
                "reportType": "status",
                "activityType": event_type,
                "statusEventType": event_type,
                "statusReason": str(event.get("reason") or ""),
                "metadata": {"reason": str(event.get("reason") or "")},
                **_empty_event_deltas(),
            }
        )

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

    def _break_buckets_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, int]]]:
        author_dates = {
            (item.get("author") or "Unknown User", item.get("date") or "")
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        profiles = self._profiles_by_raw_author()
        min_start = _date_start(dates[0]) - dt.timedelta(days=1)
        max_end = _date_start(dates[-1]) + dt.timedelta(days=2)
        buckets = {key: _empty_hourly_activity() for key in author_dates}

        interval_query = {
            "rawAuthor": {"$in": authors},
            "startedAt": {"$lt": max_end},
            "endedAt": {"$gt": min_start},
        }

        for interval in self.db.break_intervals.find(interval_query, {"_id": 0}):
            _add_break_interval_to_buckets(
                buckets,
                interval.get("rawAuthor"),
                _coerce_datetime(interval.get("startedAt")),
                _coerce_datetime(interval.get("endedAt")),
                _author_time_zone_id(interval.get("rawAuthor"), profiles, interval.get("timeZoneId")),
            )

        now = dt.datetime.now(dt.UTC)

        for session in self.db.break_sessions.find({"rawAuthor": {"$in": authors}}, {"_id": 0}):
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            _add_break_interval_to_buckets(
                buckets,
                session.get("rawAuthor"),
                started_at,
                now,
                _author_time_zone_id(session.get("rawAuthor"), profiles, session.get("timeZoneId")),
            )

        return buckets

    def _meeting_buckets_for_daily_items(
        self, daily_items: list[dict[str, Any]], now: dt.datetime | None = None
    ) -> dict[tuple[str, str], list[dict[str, int]]]:
        author_dates = {
            (item.get("author") or "Unknown User", item.get("date") or "")
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        profiles = self._profiles_by_raw_author()
        min_start = _date_start(dates[0]) - dt.timedelta(days=1)
        max_end = _date_start(dates[-1]) + dt.timedelta(days=2)
        buckets = {key: _empty_hourly_activity() for key in author_dates}

        interval_query = {
            "rawAuthor": {"$in": authors},
            "startedAt": {"$lt": max_end},
            "endedAt": {"$gt": min_start},
        }

        for interval in self.db.meeting_intervals.find(interval_query, {"_id": 0}):
            _add_meeting_interval_to_buckets(
                buckets,
                interval.get("rawAuthor"),
                _coerce_datetime(interval.get("startedAt")),
                _coerce_datetime(interval.get("endedAt")),
                _author_time_zone_id(interval.get("rawAuthor"), profiles, interval.get("timeZoneId")),
            )

        now = now or dt.datetime.now(dt.UTC)

        for session in self.db.meeting_sessions.find({"rawAuthor": {"$in": authors}}, {"_id": 0}):
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            _add_meeting_interval_to_buckets(
                buckets,
                session.get("rawAuthor"),
                started_at,
                now,
                _author_time_zone_id(session.get("rawAuthor"), profiles, session.get("timeZoneId")),
            )

        return buckets

    def _telegram_gaps_for_daily_items(self, daily_items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        author_dates = {
            (str(item.get("author") or "Unknown User"), str(item.get("date") or ""))
            for item in daily_items
            if item.get("date")
        }

        if not author_dates:
            return {}

        authors = sorted({author for author, _date in author_dates})
        dates = sorted({_date for _author, _date in author_dates})
        first_online_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        latest_offline_by_key: dict[tuple[str, str], dt.datetime] = {}
        first_activity_by_key: dict[tuple[str, str], dt.datetime] = {}

        for event in self.db.break_events.find(
            {"rawAuthor": {"$in": authors}, "date": {"$in": dates}, "eventType": {"$in": ["online", "offline"]}},
            {"_id": 0},
        ):
            key = (str(event.get("rawAuthor") or "Unknown User"), str(event.get("date") or ""))

            if key not in author_dates:
                continue

            timestamp = _coerce_datetime(event.get("timestamp"))

            if not timestamp:
                continue

            if event.get("eventType") == "offline":
                if key not in latest_offline_by_key or timestamp > latest_offline_by_key[key]:
                    latest_offline_by_key[key] = timestamp
                continue

            if key not in first_online_by_key or timestamp < first_online_by_key[key]["timestamp"]:
                first_online_by_key[key] = {
                    "timestamp": timestamp,
                    "timeZoneId": str(event.get("timeZoneId") or "UTC"),
                }

        for event in self.db.raw_activity_events.find(
            {"author": {"$in": authors}, "date": {"$in": dates}, "source": {"$nin": ["telegram", "discord"]}},
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "eventType": 1,
                "occurredAtUtc": 1,
                "occurredAtLocal": 1,
                "receivedAt": 1,
            },
        ):
            if str(event.get("eventType") or "") in NON_ACTIVITY_EVENT_TYPES:
                continue

            key = (str(event.get("author") or "Unknown User"), str(event.get("date") or ""))

            if key not in author_dates:
                continue

            occurred_at = (
                _coerce_datetime(event.get("occurredAtUtc"))
                or _coerce_datetime(event.get("occurredAtLocal"))
                or _coerce_datetime(event.get("receivedAt"))
            )

            if occurred_at and (key not in first_activity_by_key or occurred_at < first_activity_by_key[key]):
                first_activity_by_key[key] = occurred_at

        for row in self.db.report_rows.find(
            {"author": {"$in": authors}, "date": {"$in": dates}},
            {
                "_id": 0,
                "author": 1,
                "date": 1,
                "source": 1,
                "reportType": 1,
                "recordedAt": 1,
                "lastRecordedAt": 1,
                "receivedAt": 1,
                "activeDeltaSeconds": 1,
                "idleDeltaSeconds": 1,
                "overtimeActiveDeltaSeconds": 1,
                "activeDeltaMicroseconds": 1,
                "idleDeltaMicroseconds": 1,
                "overtimeActiveDeltaMicroseconds": 1,
            },
        ):
            if row.get("source") in {"telegram", "discord"} or row.get("reportType") in {"telegram", "meeting"}:
                continue

            if not _has_time_delta(row):
                continue

            key = (str(row.get("author") or "Unknown User"), str(row.get("date") or ""))

            if key not in author_dates:
                continue

            if key in first_activity_by_key:
                continue

            occurred_at = (
                _coerce_datetime(row.get("recordedAt"))
                or _coerce_datetime(row.get("lastRecordedAt"))
                or _coerce_datetime(row.get("receivedAt"))
            )

            active_delta_microseconds = _time_microseconds(row, "activeDeltaSeconds", "activeDeltaMicroseconds")
            latest_offline_at = latest_offline_by_key.get(key)

            if occurred_at and active_delta_microseconds > 0 and latest_offline_at and latest_offline_at > occurred_at:
                occurred_at = occurred_at - dt.timedelta(microseconds=active_delta_microseconds)

            if occurred_at and (key not in first_activity_by_key or occurred_at < first_activity_by_key[key]):
                first_activity_by_key[key] = occurred_at

        gaps: dict[tuple[str, str], dict[str, Any]] = {}

        for key, first_activity_at in first_activity_by_key.items():
            first_online = first_online_by_key.get(key)

            if not first_online:
                continue

            first_online_at = first_online["timestamp"]
            gap_seconds = max(0, int((first_activity_at - first_online_at).total_seconds()))

            if gap_seconds <= 0:
                continue

            hourly_activity = _empty_hourly_activity()
            _add_idle_interval_to_buckets(
                hourly_activity,
                first_online_at,
                first_activity_at,
                str(first_online.get("timeZoneId") or "UTC"),
            )
            gaps[key] = {
                "seconds": gap_seconds,
                "hourlyActivity": hourly_activity,
            }

        return gaps

    def _apply_live_telegram_summary(
        self,
        authors_by_raw: dict[str, dict[str, Any]],
        hourly_by_author: dict[str, dict[str, Any]],
        totals: dict[str, int],
        profiles: dict[str, dict[str, Any]],
        telegram_seconds_by_author_date: dict[tuple[str, str], int],
        break_seconds_by_author_date: dict[tuple[str, str], int],
        start_date: str | None,
        end_date: str | None,
        date_mode: str | None,
        now: dt.datetime,
        meeting_seconds_by_author_date: dict[tuple[str, str], int] | None = None,
        meeting_buckets: dict[tuple[str, str], list[dict[str, int]]] | None = None,
    ) -> None:
        meeting_seconds_by_author_date = meeting_seconds_by_author_date if meeting_seconds_by_author_date is not None else {}
        meeting_buckets = meeting_buckets if meeting_buckets is not None else {}
        totals.setdefault("meetingSeconds", 0)
        for session in self.db.day_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            day_date = session.get("date") or ""
            started_at = _coerce_datetime(session.get("startedAt"))

            if not day_date or not started_at:
                continue

            ended_at = _coerce_datetime(session.get("lastOfflineAt"))

            if ended_at and not _date_in_summary_scope(day_date, raw_author, profiles, None, now, start_date, end_date, date_mode):
                continue

            live_day_seconds = int(session.get("daySeconds", 0))

            is_open_day_over_cap = False

            if not ended_at:
                uncapped_live_day_seconds = max(0, int((now - started_at).total_seconds()))
                is_open_day_over_cap = uncapped_live_day_seconds > TELEGRAM_DAY_REMINDER_SECONDS
                live_day_seconds = min(uncapped_live_day_seconds, TELEGRAM_DAY_REMINDER_SECONDS)
            elif live_day_seconds <= 0:
                live_day_seconds = max(0, int((ended_at - started_at).total_seconds()))

            existing_day_seconds = telegram_seconds_by_author_date.get((raw_author, day_date), 0)
            day_delta_seconds = max(0, live_day_seconds - existing_day_seconds)

            if day_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["daySeconds"] += day_delta_seconds
                author_row["telegramDaySeconds"] += day_delta_seconds
                totals["daySeconds"] += day_delta_seconds
                totals["telegramDaySeconds"] += day_delta_seconds

            if is_open_day_over_cap:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row.setdefault("telegramAlerts", []).append(
                    {
                        "id": f"telegram_day_open:{raw_author}:{day_date}",
                        "type": "telegram_day_open",
                        "severity": "warning",
                        "title": "Telegram day still open",
                        "message": "Telegram day was not closed after 10 hours and is capped on the dashboard.",
                        "value": uncapped_live_day_seconds,
                        "threshold": TELEGRAM_DAY_REMINDER_SECONDS,
                    }
                )

        for interval in self.db.break_intervals.find(_report_date_query(start_date, end_date, date_mode, profiles, now), {"_id": 0}):
            raw_author = interval.get("rawAuthor") or "Unknown User"
            break_date = interval.get("date") or ""

            if not _date_in_summary_scope(break_date, raw_author, profiles, interval.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            break_seconds = int(interval.get("breakSeconds", 0))
            existing_break_seconds = break_seconds_by_author_date.get((raw_author, break_date), 0)
            break_delta_seconds = max(0, break_seconds - existing_break_seconds)

            if break_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["breakSeconds"] += break_delta_seconds
                totals["breakSeconds"] += break_delta_seconds

        meeting_query = _meeting_interval_date_query(start_date, end_date, date_mode, profiles, now)
        meeting_bucket_keys_from_daily_items = set(meeting_buckets)

        for interval in self.db.meeting_intervals.find(meeting_query, {"_id": 0}):
            raw_author = interval.get("rawAuthor") or "Unknown User"
            time_zone_id = _author_time_zone_id(raw_author, profiles, interval.get("timeZoneId"))
            meeting_dates = _meeting_interval_scope_dates(start_date, end_date, date_mode, now, time_zone_id)

            for meeting_date in meeting_dates:
                meeting_key = (raw_author, meeting_date)

                if meeting_key in meeting_bucket_keys_from_daily_items:
                    continue

                interval_bucket = {meeting_key: _empty_hourly_activity()}
                _add_meeting_interval_to_buckets(
                    interval_bucket,
                    raw_author,
                    _coerce_datetime(interval.get("startedAt")),
                    _coerce_datetime(interval.get("endedAt")),
                    time_zone_id,
                )
                meeting_delta_seconds = sum(
                    int(hour.get("meetingSeconds", 0)) for hour in interval_bucket[meeting_key]
                )

                if meeting_delta_seconds <= 0:
                    continue

                existing_meeting_seconds = meeting_seconds_by_author_date.get(meeting_key, 0)
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["meetingSeconds"] += meeting_delta_seconds
                totals["meetingSeconds"] += meeting_delta_seconds
                meeting_seconds_by_author_date[meeting_key] = existing_meeting_seconds + meeting_delta_seconds
                meeting_bucket = meeting_buckets.setdefault(meeting_key, _empty_hourly_activity())
                _merge_hourly_activity(meeting_bucket, interval_bucket[meeting_key])

                hourly_author = hourly_by_author.get(raw_author)

                if not hourly_author:
                    profile = profiles.get(raw_author, {})
                    hourly_author = {
                        "author": _display_name(raw_author, profile),
                        "rawAuthor": raw_author,
                        "timeZoneId": profile.get("timeZoneId") or interval.get("timeZoneId"),
                        "timeZoneDisplayName": profile.get("timeZoneDisplayName"),
                        "hourlyActivity": _empty_hourly_activity(),
                    }
                    hourly_by_author[raw_author] = hourly_author

                _merge_hourly_activity(hourly_author["hourlyActivity"], interval_bucket[meeting_key])

        for session in self.db.meeting_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            meeting_date = str(session.get("date") or _telegram_event_date(started_at, _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))))

            if not _date_in_summary_scope(meeting_date, raw_author, profiles, session.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            key = (raw_author, meeting_date)
            author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
            author_row["activeMeeting"] = True

            if key not in meeting_bucket_keys_from_daily_items:
                meeting_buckets.setdefault(key, _empty_hourly_activity())
                _add_meeting_interval_to_buckets(
                    meeting_buckets,
                    raw_author,
                    started_at,
                    now,
                    _author_time_zone_id(raw_author, profiles, session.get("timeZoneId")),
                )

        for session in self.db.break_sessions.find({}, {"_id": 0}):
            raw_author = session.get("rawAuthor") or "Unknown User"
            started_at = _coerce_datetime(session.get("startedAt"))

            if not started_at:
                continue

            break_date = str(session.get("date") or _telegram_event_date(started_at, _author_time_zone_id(raw_author, profiles, session.get("timeZoneId"))))

            if not _date_in_summary_scope(break_date, raw_author, profiles, session.get("timeZoneId"), now, start_date, end_date, date_mode):
                continue

            live_break_seconds = max(0, int((now - started_at).total_seconds()))
            existing_break_seconds = break_seconds_by_author_date.get((raw_author, break_date), 0)
            break_delta_seconds = max(0, live_break_seconds - existing_break_seconds)

            if break_delta_seconds:
                author_row = self._ensure_summary_author(authors_by_raw, raw_author, profiles)
                author_row["breakSeconds"] += break_delta_seconds
                totals["breakSeconds"] += break_delta_seconds
                break_seconds_by_author_date[(raw_author, break_date)] = existing_break_seconds + break_delta_seconds

    def _ensure_summary_author(
        self, authors_by_raw: dict[str, dict[str, Any]], raw_author: str, profiles: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        author_row = authors_by_raw.get(raw_author)

        if author_row:
            return author_row

        profile = profiles.get(raw_author, {})
        author_row = {
            "rawAuthor": raw_author,
            "authorEmail": profile.get("authorEmail", ""),
            "displayName": _display_name(raw_author, profile),
            "team": profile.get("team", ""),
            "telegramUsername": profile.get("telegramUsername", ""),
            "discordUserId": profile.get("discordUserId", ""),
            "discordUsername": profile.get("discordUsername", ""),
            "authorColor": profile.get("authorColor") or _author_color(raw_author),
            "source": None,
            "pluginVersion": None,
            "lastRecordedAt": "",
            "lastReceivedAt": "",
            "daySeconds": 0,
            "telegramDaySeconds": 0,
            "pluginDaySeconds": 0,
            "rawPluginDaySeconds": 0,
            "telegramToFirstActivitySeconds": 0,
            "activeSeconds": 0,
            "idleSeconds": 0,
            "meetingSeconds": 0,
            "breakSeconds": 0,
            "overtimeActiveSeconds": 0,
            "activityCounts": [],
            "activityMix": [],
            "savedPrefabs": [],
            "overtimeActivityCounts": [],
            "overtimeSavedPrefabs": [],
            "telegramAlerts": [],
        }
        authors_by_raw[raw_author] = author_row
        return author_row

    def _profiles_by_raw_author(self) -> dict[str, dict[str, Any]]:
        return {
            item["rawAuthor"]: item
            for item in self.db.author_profiles.find({}, {"_id": 0})
            if item.get("rawAuthor")
        }


