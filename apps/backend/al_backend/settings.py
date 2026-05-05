from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_database: str
    default_send_interval_seconds: int
    telegram_bot_secret: str
    discord_bot_secret: str
    openai_api_key: str
    openai_usage_api_key: str
    openai_usage_project_id: str
    openai_transcription_model: str
    openai_summary_model: str
    cors_origins: list[str]
    admin_email: str
    admin_password: str
    avatar_cache_dir: Path
    aggregate_version_rebuild_scope: str


def load_settings() -> Settings:
    base_dir = Path(__file__).resolve().parent
    raw_origins = os.getenv("AL_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
    avatar_dir_env = os.getenv("AL_AVATAR_CACHE_DIR", "").strip()

    if avatar_dir_env:
        avatar_cache_dir = Path(avatar_dir_env).expanduser()
    else:
        avatar_cache_dir = (base_dir.parent / "data" / "avatars").resolve()

    return Settings(
        mongo_uri=os.getenv("AL_MONGO_URI", "mongodb://127.0.0.1:27017"),
        mongo_database=os.getenv("AL_MONGO_DATABASE", "al"),
        default_send_interval_seconds=int(os.getenv("AL_DEFAULT_SEND_INTERVAL_SECONDS", "300")),
        telegram_bot_secret=os.getenv("AL_TELEGRAM_BOT_SECRET", "").strip(),
        discord_bot_secret=os.getenv("AL_DISCORD_BOT_SECRET", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_usage_api_key=os.getenv("AL_OPENAI_USAGE_API_KEY", "").strip(),
        openai_usage_project_id=os.getenv("AL_OPENAI_USAGE_PROJECT_ID", "").strip(),
        openai_transcription_model=os.getenv("AL_OPENAI_TRANSCRIPTION_MODEL", "whisper-1").strip(),
        openai_summary_model=os.getenv("AL_OPENAI_SUMMARY_MODEL", "gpt-4o-mini").strip(),
        cors_origins=[origin.strip() for origin in raw_origins.split(",") if origin.strip()],
        admin_email=os.getenv("AL_ADMIN_EMAIL", "").strip(),
        admin_password=os.getenv("AL_ADMIN_PASSWORD", ""),
        avatar_cache_dir=avatar_cache_dir,
        aggregate_version_rebuild_scope=os.getenv("AL_AGGREGATE_VERSION_REBUILD_SCOPE", "full").strip().lower(),
    )
