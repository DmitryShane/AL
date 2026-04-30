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
from datetime import datetime
from typing import Any


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

    if config.allowed_chat_id is not None and chat_id != config.allowed_chat_id:
        LOGGER.info("Ignoring message from chat id %s. Allowed chat id is %s.", chat_id, config.allowed_chat_id)
        return

    if config.allowed_chat_id is None:
        LOGGER.info("Received message from chat id %s. Set TELEGRAM_ALLOWED_CHAT_ID to lock the bot to this chat.", chat_id)

    event_type = parse_event_type(message.get("text") or "")

    if not event_type:
        return

    username = telegram_username(message.get("from") or {})

    if not username:
        LOGGER.warning("Ignoring %s event because sender has no Telegram username.", event_type)
        return

    result = submit_break_event(config.backend_url, username, event_type, message.get("date"))
    LOGGER.info("Recorded %s for @%s: %s", event_type, username, result)


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

    parsed = parse_reminder_callback(str(callback_query.get("data") or ""))

    if not parsed:
        if callback_id:
            answer_callback_query(config.token, callback_id, "Unknown action.")
        return

    reminder_id, action = parsed
    reminder_username = reminder_username_from_message(message)

    if reminder_username and actor_username and actor_username != reminder_username:
        LOGGER.info("Ignoring reminder callback %s from @%s. Reminder belongs to @%s.", reminder_id, actor_username, reminder_username)
        if callback_id:
            answer_callback_query(config.token, callback_id, "Sorry, this reminder was not sent to you.")
        return

    result = close_reminder(config.backend_url, config.bot_secret, reminder_id, action, actor_username)

    if result.get("status") == "wrong_user":
        if callback_id:
            answer_callback_query(config.token, callback_id, "Sorry, this reminder was not sent to you.")
        return

    LOGGER.info("Closed Telegram day reminder %s with %s: %s", reminder_id, action, result)

    if chat_id and message_id:
        edit_reminder_message(config.token, chat_id, message_id, action, reminder_username or actor_username)

    if callback_id:
        answer_callback_query(config.token, callback_id, "Telegram day closed.")


def parse_event_type(text: str) -> str | None:
    normalized = re.sub(r"^[^\wа-яА-ЯёЁ]+|[^\wа-яА-ЯёЁ]+$", "", text.strip().lower())
    return COMMANDS.get(normalized)


def telegram_username(sender: dict[str, Any]) -> str:
    return str(sender.get("username") or "").strip().lstrip("@").lower()


def reminder_username_from_message(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "")
    match = re.search(r"Hi\s+@([A-Za-z0-9_]{5,32})\b", text)
    return telegram_username({"username": match.group(1)}) if match else ""


def parse_reminder_callback(data: str) -> tuple[str, str] | None:
    parts = data.split(":")

    if len(parts) != 3 or parts[0] != "altd":
        return None

    reminder_id = parts[1].strip()
    action = parts[2].strip()

    if not reminder_id or action not in {"offline", "overtime"}:
        return None

    return reminder_id, action


def send_due_reminders(config: BotConfig) -> None:
    if config.allowed_chat_id is None:
        LOGGER.warning("Telegram reminders require TELEGRAM_ALLOWED_CHAT_ID.")
        return

    if not config.bot_secret:
        LOGGER.warning("Telegram reminders require AL_TELEGRAM_BOT_SECRET.")
        return

    for reminder in due_reminders(config.backend_url, config.bot_secret):
        reminder_id = str(reminder.get("reminderId") or "")
        telegram_name = str(reminder.get("telegramUsername") or "").strip().lstrip("@")

        if not reminder_id or not telegram_name:
            continue

        text = f"Hi @{telegram_name}. Did you forget to go offline, or are you working overtime?"
        result = send_reminder_message(config.token, config.allowed_chat_id, text, reminder_id)
        message_id = (result.get("result") or {}).get("message_id") if isinstance(result, dict) else None
        mark_reminder_sent(config.backend_url, config.bot_secret, reminder_id, message_id)


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


def due_reminders(backend_url: str, bot_secret: str) -> list[dict[str, Any]]:
    result = backend_request(backend_url, "/api/v1/telegram/reminders/due", bot_secret, method="GET")
    return result.get("reminders", [])


def mark_reminder_sent(backend_url: str, bot_secret: str, reminder_id: str, message_id: int | None) -> dict[str, Any]:
    return backend_request(
        backend_url,
        "/api/v1/telegram/reminders/sent",
        bot_secret,
        method="POST",
        payload={"reminderId": reminder_id, "messageId": message_id},
    )


def close_reminder(backend_url: str, bot_secret: str, reminder_id: str, action: str, actor_telegram_username: str = "") -> dict[str, Any]:
    return backend_request(
        backend_url,
        "/api/v1/telegram/reminders/close",
        bot_secret,
        method="POST",
        payload={
            "reminderId": reminder_id,
            "action": action,
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


def answer_callback_query(token: str, callback_query_id: str, text: str) -> dict[str, Any]:
    return telegram_request(token, "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


def edit_reminder_message(token: str, chat_id: int, message_id: int, action: str, telegram_username: str = "") -> dict[str, Any]:
    label = "Overtime" if action == "overtime" else "Offline"
    author = f" @{telegram_username}" if telegram_username else ""
    return telegram_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Done.{author} Telegram day closed as {label}.",
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
