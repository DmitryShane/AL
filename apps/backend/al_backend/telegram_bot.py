from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from al_backend.meeting_summary import DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE


LOGGER = logging.getLogger("al.telegram_bot")
POLL_TIMEOUT_SECONDS = 30
RETRY_DELAY_SECONDS = 5

COMMANDS = {
    "online": "online",
    "онлайн": "online",
    "afk": "afk",
    "афк": "afk",
    "offline": "offline",
    "офлайн": "offline",
    "оффлайн": "offline",
}


@dataclass(frozen=True)
class BotConfig:
    token: str
    backend_url: str
    allowed_chat_id: int | None
    bot_secret: str


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_TELEGRAM_LOG_LEVEL", "INFO"))
    config = load_config()
    run_bot(config)


def load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    backend_url = os.getenv("AL_BACKEND_URL", "https://activity.mempic.com").strip().rstrip("/")
    allowed_chat_id = _parse_chat_id(os.getenv("TELEGRAM_ALLOWED_CHAT_ID"))
    bot_secret = os.getenv("AL_TELEGRAM_BOT_SECRET", "").strip()
    return BotConfig(token=token, backend_url=backend_url, allowed_chat_id=allowed_chat_id, bot_secret=bot_secret)


def run_bot(config: BotConfig) -> None:
    offset: int | None = None
    LOGGER.info("Telegram bot started. Backend: %s", config.backend_url)

    while True:
        try:
            updates = get_updates(config.token, offset)

            for update in updates:
                update_id = update.get("update_id")

                if isinstance(update_id, int):
                    offset = update_id + 1

                handle_update(config, update)

            send_due_reminders(config)
        except KeyboardInterrupt:
            LOGGER.info("Telegram bot stopped.")
            return
        except Exception:
            LOGGER.exception("Telegram bot polling failed. Retrying in %s seconds.", RETRY_DELAY_SECONDS)
            time.sleep(RETRY_DELAY_SECONDS)


def get_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeout": POLL_TIMEOUT_SECONDS,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }

    if offset is not None:
        params["offset"] = offset

    response = telegram_request(token, "getUpdates", params)
    return response.get("result", [])


def handle_update(config: BotConfig, update: dict[str, Any]) -> None:
    if update.get("callback_query"):
        handle_callback_query(config, update["callback_query"])
        return

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or ""
    chat_type = str(chat.get("type") or "")
    username = telegram_username(message.get("from") or {})

    if text.strip().lower().split(maxsplit=1)[0] == "/start" and chat_type == "private" and username and chat_id:
        result = save_private_chat(config.backend_url, config.bot_secret, username, int(chat_id))
        LOGGER.info("Saved private Telegram chat for @%s: %s", username, result)
        send_plain_message(config.token, int(chat_id), "Done. I can send meeting summaries here.")
        return

    if config.allowed_chat_id is not None and chat_id != config.allowed_chat_id:
        LOGGER.info("Ignoring message from chat id %s. Allowed chat id is %s.", chat_id, config.allowed_chat_id)
        return

    if config.allowed_chat_id is None:
        LOGGER.info("Received message from chat id %s. Set TELEGRAM_ALLOWED_CHAT_ID to lock the bot to this chat.", chat_id)

    event_type = parse_event_type(text)

    if not event_type:
        return

    if not username:
        LOGGER.warning("Ignoring %s event because sender has no Telegram username.", event_type)
        return

    result = submit_break_event(config.backend_url, username, event_type, message.get("date"))
    LOGGER.info("Recorded %s for @%s: %s", event_type, username, result)

    dup_status = result.get("status")

    if config.bot_secret and config.allowed_chat_id is not None and chat_id == config.allowed_chat_id:
        if dup_status == "duplicate_online":
            since = str(result.get("sinceTimeLocal") or "unknown time")
            dup_text = (
                f"Hi @{username}. You're already counted as online for today since {since}. "
                "Use AFK before writing online again after a break."
            )
            send_plain_message(config.token, config.allowed_chat_id, dup_text)
            return

        if dup_status == "duplicate_offline":
            since = str(result.get("sinceOfflineTimeLocal") or "unknown time")
            send_plain_message(
                config.token,
                config.allowed_chat_id,
                f"Hi @{username}. You already closed your Telegram workday offline at {since}.",
            )
            return

        if dup_status == "duplicate_afk":
            duplicate_afk_prompt_id = str(result.get("reminderId") or "")
            afk_time = str(result.get("afkStartedTimeLocal") or "unknown time")

            if duplicate_afk_prompt_id:
                dup_afk_body = (
                    f"Hi @{username}. You're already on an AFK break that started at {afk_time}. "
                    "Are you already back online, or are you still AFK?"
                )
                prompt_send = send_duplicate_afk_prompt_message(config.token, config.allowed_chat_id, dup_afk_body, duplicate_afk_prompt_id)
                prompt_message_id = (prompt_send.get("result") or {}).get("message_id") if isinstance(prompt_send, dict) else None
                mark_reminder_sent(
                    config.backend_url,
                    config.bot_secret,
                    duplicate_afk_prompt_id,
                    prompt_message_id,
                    kind="duplicate_afk_prompt",
                )


def handle_callback_query(config: BotConfig, callback_query: dict[str, Any]) -> None:
    callback_id = str(callback_query.get("id") or "")
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    actor_username = telegram_username(callback_query.get("from") or {})

    if config.allowed_chat_id is not None and chat_id != config.allowed_chat_id:
        LOGGER.info("Ignoring callback from chat id %s. Allowed chat id is %s.", chat_id, config.allowed_chat_id)
        if callback_id:
            answer_callback_query(config.token, callback_id, "This action is not available in this chat.")
        return

    parsed = parse_callback_data(str(callback_query.get("data") or ""))

    if not parsed:
        if callback_id:
            answer_callback_query(config.token, callback_id, "Unknown action.")
        return

    family, reminder_id, action = parsed
    if family == "altm":
        reminder_kind = "online_prompt"
    elif family == "altb":
        reminder_kind = "break_activity_prompt"
    elif family == "altf":
        reminder_kind = "duplicate_afk_prompt"
    else:
        reminder_kind = "day_end"
    reminder_username = reminder_username_from_message(message)

    if reminder_username and actor_username and actor_username != reminder_username:
        LOGGER.info("Ignoring reminder callback %s from @%s. Reminder belongs to @%s.", reminder_id, actor_username, reminder_username)
        if callback_id:
            answer_callback_query(config.token, callback_id, "Sorry, this reminder was not sent to you.")
        return

    result = close_reminder(config.backend_url, config.bot_secret, reminder_id, action, actor_username, reminder_kind=reminder_kind)

    if result.get("status") == "wrong_user":
        if callback_id:
            answer_callback_query(config.token, callback_id, "Sorry, this reminder was not sent to you.")
        return

    LOGGER.info("Closed Telegram reminder %s (%s) with %s: %s", reminder_id, reminder_kind, action, result)

    if chat_id and message_id:
        edit_reminder_message(
            config.token,
            int(chat_id),
            int(message_id),
            action,
            reminder_username or actor_username,
            reminder_kind=reminder_kind,
        )

    if callback_id:
        if reminder_kind == "online_prompt":
            if action == "confirm_online":
                answer_text = "Online."
            else:
                answer_text = "Dismissed."
        elif reminder_kind == "break_activity_prompt":
            if action == "confirm_online":
                answer_text = "Online."
            else:
                answer_text = "Still AFK."
        elif reminder_kind == "duplicate_afk_prompt":
            if action == "confirm_online":
                answer_text = "Online."
            else:
                answer_text = "Still AFK."
        else:
            answer_text = "Telegram day closed."

        answer_callback_query(config.token, callback_id, answer_text)


def parse_event_type(text: str) -> str | None:
    normalized = re.sub(r"^[^\wа-яА-ЯёЁ]+|[^\wа-яА-ЯёЁ]+$", "", text.strip().lower())
    return COMMANDS.get(normalized)


def telegram_username(sender: dict[str, Any]) -> str:
    return str(sender.get("username") or "").strip().lstrip("@").lower()


def format_prompt_time(value: Any, time_zone_id: Any = None) -> str:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            if time_zone_id:
                try:
                    parsed = parsed.astimezone(ZoneInfo(str(time_zone_id)))
                except ZoneInfoNotFoundError:
                    pass

            return parsed.strftime("%H:%M")
        except ValueError:
            return value

    return "unknown time"


def format_duration_label(seconds: int) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"

    unit = "second" if seconds == 1 else "seconds"
    return f"{seconds} {unit}"


def format_meeting_duration_label(total_seconds: int) -> str:
    if total_seconds < 0:
        return "unknown"

    if total_seconds == 0:
        return "0m"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def reminder_username_from_message(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "")
    match = re.search(r"Hi\s+@([A-Za-z0-9_]{5,32})\b", text)
    return telegram_username({"username": match.group(1)}) if match else ""


def parse_callback_data(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")

    if len(parts) != 3:
        return None

    family = parts[0].strip()
    reminder_id = parts[1].strip()
    action = parts[2].strip()

    if not reminder_id:
        return None

    if family == "altd":
        if action not in {"offline", "overtime"}:
            return None

        return family, reminder_id, action

    if family == "altm":
        if action not in {"confirm_online", "dismiss"}:
            return None

        return family, reminder_id, action

    if family == "altb":
        if action not in {"confirm_online", "still_afk"}:
            return None

        return family, reminder_id, action

    if family == "altf":
        if action not in {"confirm_online", "still_afk"}:
            return None

        return family, reminder_id, action

    return None


def parse_reminder_callback(data: str) -> tuple[str, str] | None:
    parsed = parse_callback_data(data)

    if not parsed or parsed[0] != "altd":
        return None

    return parsed[1], parsed[2]


def send_due_reminders(config: BotConfig) -> None:
    if config.allowed_chat_id is None:
        LOGGER.warning("Telegram reminders require TELEGRAM_ALLOWED_CHAT_ID.")
        return

    if not config.bot_secret:
        LOGGER.warning("Telegram reminders require AL_TELEGRAM_BOT_SECRET.")
        return

    bundle = fetch_reminders_due_bundle(config.backend_url, config.bot_secret)

    for reminder in bundle.get("reminders", []):
        reminder_id = str(reminder.get("reminderId") or "")
        telegram_name = str(reminder.get("telegramUsername") or "").strip().lstrip("@")

        if not reminder_id or not telegram_name:
            continue

        text = f"Hi @{telegram_name}. Did you forget to go offline, or are you working overtime?"
        result = send_reminder_message(config.token, config.allowed_chat_id, text, reminder_id)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, reminder_id, message_id, kind="day_end")

    for prompt in bundle.get("onlinePrompts", []):
        prompt_id = str(prompt.get("reminderId") or "")
        telegram_name = str(prompt.get("telegramUsername") or "").strip().lstrip("@")

        if not prompt_id or not telegram_name:
            continue

        text = (
            f"Hi @{telegram_name}. You have activity today but no \"online\" message yet. "
            "Are you online, or is this a mistake?"
        )
        result = send_online_prompt_message(config.token, config.allowed_chat_id, text, prompt_id)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, prompt_id, message_id, kind="online_prompt")

    for prompt in bundle.get("breakActivityPrompts", []):
        prompt_id = str(prompt.get("reminderId") or "")
        telegram_name = str(prompt.get("telegramUsername") or "").strip().lstrip("@")
        break_started_at = format_prompt_time(prompt.get("breakStartedAt"), prompt.get("timeZoneId"))

        if not prompt_id or not telegram_name:
            continue

        text = (
            f"Hi @{telegram_name}. You went AFK at {break_started_at}, but I now see activity from you. "
            "Did you forget to write online?"
        )
        result = send_break_activity_prompt_message(config.token, config.allowed_chat_id, text, prompt_id)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, prompt_id, message_id, kind="break_activity_prompt")

    for notification in bundle.get("meetingAutoAfkNotifications", []):
        notification_id = str(notification.get("reminderId") or "")
        telegram_name = str(notification.get("telegramUsername") or "").strip().lstrip("@")
        timeout_label = format_duration_label(int(notification.get("thresholdSeconds") or 0))

        if not notification_id or not telegram_name:
            continue

        text = (
            f"Hi @{telegram_name}. You were alone in the meeting channel for over {timeout_label}, "
            "so I moved you to AFK."
        )
        result = send_plain_message(config.token, config.allowed_chat_id, text)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, notification_id, message_id, kind="meeting_auto_afk")

    for notification in bundle.get("meetingRecordingNotifications", []):
        notification_id = str(notification.get("reminderId") or "")

        if not notification_id:
            continue

        text = format_meeting_recording_notification_message(notification)
        result = send_plain_message(config.token, config.allowed_chat_id, text)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, notification_id, message_id, kind="meeting_recording")

    for notification in bundle.get("meetingSummaryNotifications", []):
        summary_id = str(notification.get("summaryId") or "")
        summary_text = str(notification.get("summary") or "").strip()

        if not summary_id or not summary_text:
            continue

        text = format_meeting_summary_message(notification, summary_text)
        chat_id = meeting_summary_chat_id(config.allowed_chat_id, notification)
        result = send_plain_message(config.token, chat_id, text)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, summary_id, message_id, kind="meeting_summary")


def submit_break_event(backend_url: str, telegram_username_value: str, event_type: str, telegram_timestamp: Any) -> dict[str, Any]:
    timestamp = None

    if isinstance(telegram_timestamp, int):
        timestamp = datetime.fromtimestamp(telegram_timestamp).astimezone().isoformat()

    payload = {
        "telegramUsername": telegram_username_value,
        "eventType": event_type,
        "timestamp": timestamp,
    }
    request = urllib.request.Request(
        f"{backend_url}/api/v1/break-events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_reminders_due_bundle(backend_url: str, bot_secret: str) -> dict[str, Any]:
    return backend_request(backend_url, "/api/v1/telegram/reminders/due", bot_secret, method="GET")


def save_private_chat(backend_url: str, bot_secret: str, telegram_username_value: str, chat_id: int) -> dict[str, Any]:
    return backend_request(
        backend_url,
        "/api/v1/telegram/private-chat",
        bot_secret,
        method="POST",
        payload={"telegramUsername": telegram_username_value, "chatId": chat_id},
    )


def meeting_summary_chat_id(default_chat_id: int, notification: dict[str, Any]) -> int:
    raw_recipient = notification.get("recipient")
    recipient: dict[str, Any] = raw_recipient if isinstance(raw_recipient, dict) else {}

    if recipient.get("kind") == "private" and recipient.get("chatId"):
        return int(recipient["chatId"])

    return default_chat_id


def _format_meeting_summary_participant_mentions(names: list[str]) -> str:
    tokens: list[str] = []

    for raw in names:
        name = str(raw).strip()

        if not name:
            continue

        while name.startswith("@"):
            name = name[1:].strip()

        if not name:
            continue

        tokens.append(f"@{name}")

    if not tokens:
        return "Unknown participants"

    return ", ".join(tokens)


def _format_meeting_recording_participant_names(names: list[str]) -> str:
    tokens: list[str] = []

    for raw in names:
        name = str(raw).strip()

        while name.startswith("@"):
            name = name[1:].strip()

        if name:
            tokens.append(name)

    if not tokens:
        return "Unknown participants"

    return ", ".join(tokens)


def format_meeting_summary_message(notification: dict[str, Any], summary_text: str) -> str:
    started_at = format_meeting_summary_date(str(notification.get("startedAt") or ""))
    duration_seconds = int(notification.get("durationSeconds") or 0)
    duration_label = format_meeting_duration_label(duration_seconds)
    raw_telegram_participants = notification.get("participantTelegramUsernames")
    raw_participants = (
        raw_telegram_participants
        if isinstance(raw_telegram_participants, list) and raw_telegram_participants
        else notification.get("participantNames")
    )
    participants = raw_participants if isinstance(raw_participants, list) else []
    participants_text = _format_meeting_summary_participant_mentions([str(item) for item in participants])
    template = str(notification.get("telegramTemplate") or DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE)

    try:
        return template.format(
            date=started_at,
            duration=duration_label,
            participants=participants_text,
            summary=summary_text,
        ).strip()
    except (KeyError, ValueError):
        return DEFAULT_MEETING_SUMMARY_TELEGRAM_TEMPLATE.format(
            date=started_at,
            duration=duration_label,
            participants=participants_text,
            summary=summary_text,
        ).strip()


def format_meeting_recording_notification_message(notification: dict[str, Any]) -> str:
    raw_telegram_participants = notification.get("participantTelegramUsernames")
    raw_participants = (
        raw_telegram_participants
        if isinstance(raw_telegram_participants, list) and raw_telegram_participants
        else notification.get("participantNames")
    )
    participants = raw_participants if isinstance(raw_participants, list) else []
    participants_text = _format_meeting_recording_participant_names([str(item) for item in participants])

    if notification.get("kind") == "ended":
        return f"Hi {participants_text}. Your meeting has ended. Thanks everyone, please wait for the summary."

    return f"Hi {participants_text}. Your meeting has started. Hope it goes smoothly!"


def format_meeting_summary_date(value: str) -> str:
    if not value:
        return "unknown date"

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return parsed.astimezone().strftime("%Y-%m-%d")


def mark_reminder_sent(
    backend_url: str,
    bot_secret: str,
    reminder_id: str,
    message_id: int | None,
    *,
    kind: str = "day_end",
) -> dict[str, Any]:
    return backend_request(
        backend_url,
        "/api/v1/telegram/reminders/sent",
        bot_secret,
        method="POST",
        payload={"reminderId": reminder_id, "messageId": message_id, "kind": kind},
    )


def close_reminder(
    backend_url: str,
    bot_secret: str,
    reminder_id: str,
    action: str,
    actor_telegram_username: str = "",
    *,
    reminder_kind: str = "day_end",
) -> dict[str, Any]:
    return backend_request(
        backend_url,
        "/api/v1/telegram/reminders/close",
        bot_secret,
        method="POST",
        payload={
            "reminderId": reminder_id,
            "action": action,
            "kind": reminder_kind,
            "timestamp": datetime.now().astimezone().isoformat(),
            "actorTelegramUsername": actor_telegram_username,
        },
    )


def backend_request(backend_url: str, path: str, bot_secret: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "X-AL-Telegram-Bot-Secret": bot_secret}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(f"{backend_url.rstrip('/')}{path}", data=data, headers=headers, method=method)

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def send_reminder_message(token: str, chat_id: int, text: str, reminder_id: str) -> dict[str, Any]:
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "Offline", "callback_data": f"altd:{reminder_id}:offline"},
                {"text": "Overtime", "callback_data": f"altd:{reminder_id}:overtime"},
            ]
        ]
    }
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps(reply_markup),
        },
    )


def send_online_prompt_message(token: str, chat_id: int, text: str, reminder_id: str) -> dict[str, Any]:
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "I'm online", "callback_data": f"altm:{reminder_id}:confirm_online"},
                {"text": "Mistake", "callback_data": f"altm:{reminder_id}:dismiss"},
            ]
        ]
    }
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps(reply_markup),
        },
    )


def send_break_activity_prompt_message(token: str, chat_id: int, text: str, reminder_id: str) -> dict[str, Any]:
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "I'm online", "callback_data": f"altb:{reminder_id}:confirm_online"},
                {"text": "Still AFK", "callback_data": f"altb:{reminder_id}:still_afk"},
            ]
        ]
    }
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps(reply_markup),
        },
    )


def send_duplicate_afk_prompt_message(token: str, chat_id: int, text: str, reminder_id: str) -> dict[str, Any]:
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "I'm online", "callback_data": f"altf:{reminder_id}:confirm_online"},
                {"text": "Still AFK", "callback_data": f"altf:{reminder_id}:still_afk"},
            ]
        ]
    }
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps(reply_markup),
        },
    )


def send_plain_message(token: str, chat_id: int, text: str) -> dict[str, Any]:
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        },
    )


def answer_callback_query(token: str, callback_query_id: str, text: str) -> dict[str, Any]:
    return telegram_request(token, "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


def edit_reminder_message(
    token: str,
    chat_id: int,
    message_id: int,
    action: str,
    telegram_username: str = "",
    *,
    reminder_kind: str = "day_end",
) -> dict[str, Any]:
    author = f" @{telegram_username}" if telegram_username else ""
    remove_keyboard = json.dumps({"inline_keyboard": []})

    if reminder_kind == "online_prompt":
        if action == "confirm_online":
            body = f"Done.{author} Online."
        else:
            body = f"Done.{author} Still offline."
    elif reminder_kind == "break_activity_prompt":
        if action == "confirm_online":
            body = f"Done.{author} Online."
        else:
            body = f"Done.{author} Still AFK."
    elif reminder_kind == "duplicate_afk_prompt":
        if action == "confirm_online":
            body = f"Done.{author} Online."
        else:
            body = f"Done.{author} Still AFK."
    else:
        label = "Overtime" if action == "overtime" else "Offline"
        body = f"Done.{author} Telegram day closed as {label}."

    return telegram_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": body,
            "reply_markup": remove_keyboard,
        },
    )


def telegram_request(token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{token}/{method}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=POLL_TIMEOUT_SECONDS + 10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API request failed: HTTP {exc.code}: {body}") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API request failed: {payload}")

    return payload


def _parse_chat_id(value: str | None) -> int | None:
    normalized = (value or "").strip()

    if not normalized:
        return None

    return int(normalized)


if __name__ == "__main__":
    main()
