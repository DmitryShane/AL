from __future__ import annotations

import logging
from typing import Any

from .repository import Repository
from .settings import load_settings


LOGGER = logging.getLogger("al.discord_author_mappings")

DISCORD_AUTHOR_MAPPINGS = [
    {
        "telegramUsername": "ama_deus",
        "discordUserId": "645196366494171139",
        "discordUsername": "Evgeniy Dotsenko",
    },
    {
        "telegramUsername": "dmitryshane",
        "discordUserId": "328493994822598666",
        "discordUsername": "Dmitry Shane",
    },
    {
        "telegramUsername": "vedamir_infinum",
        "discordUserId": "419521453537624084",
        "discordUsername": "Denis Ostrovskiy",
    },
    {
        "telegramUsername": "zhdamarovich",
        "discordUserId": "689511934030119014",
        "discordUsername": "Dmitriy Zhdamarov",
    },
    {
        "telegramUsername": "igormats",
        "discordUserId": "689526024857321504",
        "discordUsername": "Igor Mats",
    },
]


def main() -> None:
    logging.basicConfig(level="INFO")
    repo = Repository(load_settings())

    try:
        result = apply_discord_author_mappings(repo)
        LOGGER.info("Applied Discord author mappings: %s", result)
    finally:
        repo.client.close()


def apply_discord_author_mappings(repo: Repository) -> dict[str, Any]:
    updated = []
    missing = []

    for mapping in DISCORD_AUTHOR_MAPPINGS:
        telegram_username = mapping["telegramUsername"]
        profile = repo.db.author_profiles.find_one({"telegramUsername": telegram_username}, {"_id": 0})

        if not profile:
            missing.append(telegram_username)
            continue

        repo.db.author_profiles.update_one(
            {"rawAuthor": profile["rawAuthor"]},
            {
                "$set": {
                    "discordUserId": mapping["discordUserId"],
                    "discordUsername": mapping["discordUsername"],
                }
            },
        )
        updated.append(
            {
                "rawAuthor": profile["rawAuthor"],
                "telegramUsername": telegram_username,
                "discordUserId": mapping["discordUserId"],
            }
        )

    return {"updated": updated, "missingTelegramUsernames": missing}


if __name__ == "__main__":
    main()
