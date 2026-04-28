from __future__ import annotations

import json
import logging
import os
import re
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


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_TELEGRAM_LOG_LEVEL", "INFO"))
    config = load_config()
    run_bot(config)


def load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    backend_url = os.getenv("AL_BACKEND_URL", "http://64.225.108.88:8000").strip().rstrip("/")
    allowed_chat_id = _parse_chat_id(os.getenv("TELEGRAM_ALLOWED_CHAT_ID"))
    return BotConfig(token=token, backend_url=backend_url, allowed_chat_id=allowed_chat_id)


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
        except KeyboardInterrupt:
            LOGGER.info("Telegram bot stopped.")
            return
        except Exception:
            LOGGER.exception("Telegram bot polling failed. Retrying in %s seconds.", RETRY_DELAY_SECONDS)
            time.sleep(RETRY_DELAY_SECONDS)


def get_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeout": POLL_TIMEOUT_SECONDS,
        "allowed_updates": json.dumps(["message"]),
    }

    if offset is not None:
        params["offset"] = offset

    response = telegram_request(token, "getUpdates", params)
    return response.get("result", [])


def handle_update(config: BotConfig, update: dict[str, Any]) -> None:
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


def parse_event_type(text: str) -> str | None:
    normalized = re.sub(r"^[^\wа-яА-ЯёЁ]+|[^\wа-яА-ЯёЁ]+$", "", text.strip().lower())
    return COMMANDS.get(normalized)


def telegram_username(sender: dict[str, Any]) -> str:
    return str(sender.get("username") or "").strip().lstrip("@").lower()


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
