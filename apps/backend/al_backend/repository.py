from __future__ import annotations

import datetime as dt
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .settings import Settings


class Repository:
    def __init__(self, settings: Settings):
        self.client: MongoClient = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
        self.db: Database = self.client[settings.mongo_database]
        self.default_send_interval_seconds = settings.default_send_interval_seconds

    def ensure_indexes(self) -> None:
        self.db.raw_reports.create_index([("source", ASCENDING), ("receivedAt", DESCENDING)])
        self.db.activity_snapshots.create_index(
            [("source", ASCENDING), ("author", ASCENDING), ("date", ASCENDING)]
        )
        self.db.activity_snapshots.create_index(
            [("sessionId", ASCENDING), ("date", ASCENDING), ("recordedAt", DESCENDING)]
        )
        self.db.interval_settings.create_index("kind", unique=True)
        self.db.interval_settings.create_index("author", unique=True, sparse=True)

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True

    def get_interval_for_author(self, author: str) -> int:
        author_setting = self.db.interval_settings.find_one({"kind": "author", "author": author})

        if author_setting and author_setting.get("sendIntervalSeconds"):
            return int(author_setting["sendIntervalSeconds"])

        global_setting = self.db.interval_settings.find_one({"kind": "global"})

        if global_setting and global_setting.get("sendIntervalSeconds"):
            return int(global_setting["sendIntervalSeconds"])

        return self.default_send_interval_seconds

    def upsert_interval_settings(
        self,
        default_send_interval_seconds: int | None,
        author: str | None,
        author_send_interval_seconds: int | None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)

        if default_send_interval_seconds is not None:
            self.db.interval_settings.update_one(
                {"kind": "global"},
                {"$set": {"sendIntervalSeconds": default_send_interval_seconds, "updatedAt": now}},
                upsert=True,
            )

        if author and author_send_interval_seconds is not None:
            self.db.interval_settings.update_one(
                {"kind": "author", "author": author},
                {
                    "$set": {
                        "author": author,
                        "sendIntervalSeconds": author_send_interval_seconds,
                        "updatedAt": now,
                    }
                },
                upsert=True,
            )

        return self.get_interval_settings()

    def get_interval_settings(self) -> dict[str, Any]:
        global_setting = self.db.interval_settings.find_one({"kind": "global"}) or {}
        author_settings = list(
            self.db.interval_settings.find({"kind": "author"}, {"_id": 0}).sort("author", ASCENDING)
        )

        return {
            "defaultSendIntervalSeconds": int(
                global_setting.get("sendIntervalSeconds", self.default_send_interval_seconds)
            ),
            "authors": author_settings,
        }

    def save_report(self, source: str, plugin_version: str, encrypted_packet: str, payload: dict[str, Any]) -> str:
        now = dt.datetime.now(dt.UTC)
        raw_result = self.db.raw_reports.insert_one(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "encryptedPacket": encrypted_packet,
                "receivedAt": now,
                "status": "decoded",
            }
        )
        snapshot = dict(payload)
        snapshot.update(
            {
                "source": source,
                "pluginVersion": plugin_version,
                "rawReportId": raw_result.inserted_id,
                "receivedAt": now,
            }
        )
        self.db.activity_snapshots.insert_one(snapshot)
        return str(raw_result.inserted_id)

    def list_authors(self) -> list[str]:
        return sorted(author for author in self.db.activity_snapshots.distinct("author") if author)

    def latest_reports(self, limit: int = 100) -> list[dict[str, Any]]:
        reports = []

        for item in self.db.activity_snapshots.find({}, {"_id": 0, "rawReportId": 0}).sort("receivedAt", DESCENDING).limit(limit):
            item["receivedAt"] = _iso(item.get("receivedAt"))
            reports.append(item)

        return reports


def _iso(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()

    return value
