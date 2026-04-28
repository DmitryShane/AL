from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_database: str
    private_key_path: Path
    default_send_interval_seconds: int
    cors_origins: list[str]
    admin_email: str
    admin_password: str


def load_settings() -> Settings:
    base_dir = Path(__file__).resolve().parent
    raw_origins = os.getenv("AL_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")

    return Settings(
        mongo_uri=os.getenv("AL_MONGO_URI", "mongodb://127.0.0.1:27017"),
        mongo_database=os.getenv("AL_MONGO_DATABASE", "al"),
        private_key_path=Path(os.getenv("AL_PRIVATE_KEY_PATH", base_dir / "UnityActivityLoggerKey.json")),
        default_send_interval_seconds=int(os.getenv("AL_DEFAULT_SEND_INTERVAL_SECONDS", "300")),
        cors_origins=[origin.strip() for origin in raw_origins.split(",") if origin.strip()],
        admin_email=os.getenv("AL_ADMIN_EMAIL", "").strip(),
        admin_password=os.getenv("AL_ADMIN_PASSWORD", ""),
    )
