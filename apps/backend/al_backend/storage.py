from __future__ import annotations

from pymongo.database import Database
from pymongo import MongoClient

from .settings import Settings


class MongoStorage:
    def __init__(self, settings: Settings):
        self.client: MongoClient = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
        self.db: Database = self.client[settings.mongo_database]
