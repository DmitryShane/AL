import datetime as dt
import re
import threading
import tempfile
from pathlib import Path
from typing import Any

from al_backend.container import BackendServices


class FakeCursor:
    def __init__(self, items):
        self.items = items

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def __len__(self):
        return len(self.items)

    def sort(self, *args, **kwargs):
        sort_spec = args[0] if args else []

        if isinstance(sort_spec, list):
            for key, direction in reversed(sort_spec):
                self.items.sort(key=lambda item: self._sort_value(item.get(key)), reverse=direction < 0)

        return self

    def limit(self, *args, **kwargs):
        if args:
            self.items = self.items[: int(args[0])]

        return self

    def skip(self, *args, **kwargs):
        if args:
            self.items = self.items[int(args[0]) :]

        return self

    def _sort_value(self, value):
        if isinstance(value, dt.datetime):
            return value.isoformat()

        return str(value or "")


class FakeCollection:
    def __init__(self):
        self.items = []

    def find_one(self, query, projection=None, sort=None):
        for item in self.items:
            if self._matches(item, query):
                return item.copy()

        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return FakeCursor([item.copy() for item in self.items if self._matches(item, query)])

    def insert_one(self, item):
        self.items.append(item.copy())

        class Result:
            inserted_id = item.get("_id")

        return Result()

    def update_one(self, query, operation, upsert=False):
        for item in self.items:
            if self._matches(item, query):
                self._apply_operation(item, operation, inserting=False)
                return

        if upsert:
            item = query.copy()
            self._apply_operation(item, operation, inserting=True)
            self.items.append(item)

    def update_many(self, query, operation):
        for item in self.items:
            if self._matches(item, query):
                self._apply_operation(item, operation, inserting=False)

    def delete_one(self, query):
        before = len(self.items)
        self.items = [item for item in self.items if not self._matches(item, query)]

        class Result:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return Result(before - len(self.items))

    def find_one_and_delete(self, query):
        for index, item in enumerate(self.items):
            if self._matches(item, query):
                return self.items.pop(index).copy()

        return None

    def delete_many(self, query):
        matching = [item for item in self.items if self._matches(item, query)]
        self.items = [item for item in self.items if not self._matches(item, query)]

        class Result:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return Result(len(matching))

    def distinct(self, key, query=None):
        query = query or {}
        return sorted({item.get(key) for item in self.items if item.get(key) and self._matches(item, query)})

    def count_documents(self, query):
        return len([item for item in self.items if self._matches(item, query)])

    def _matches(self, item, query):
        for key, value in query.items():
            if key == "$or":
                if not any(self._matches(item, option) for option in value):
                    return False

                continue

            item_value = item.get(key)

            if isinstance(value, dict):
                if "$ne" in value and item_value == value["$ne"]:
                    return False

                if "$in" in value and item_value not in value["$in"]:
                    return False

                if "$nin" in value and item_value in value["$nin"]:
                    return False

                if "$regex" in value and not re.search(value["$regex"], str(item_value or "")):
                    return False

                if item_value is None and any(operator in value for operator in ("$gte", "$lte", "$lt", "$gt")):
                    return False

                if "$gte" in value and item_value < value["$gte"]:
                    return False

                if "$lte" in value and item_value > value["$lte"]:
                    return False

                if "$lt" in value and item_value >= value["$lt"]:
                    return False

                if "$gt" in value and item_value <= value["$gt"]:
                    return False
            elif item_value != value:
                return False

        return True

    def _apply_operation(self, item, operation, inserting):
        for key, value in operation.get("$set", {}).items():
            item[key] = value

        if inserting:
            for key, value in operation.get("$setOnInsert", {}).items():
                item.setdefault(key, value)

        for key, value in operation.get("$inc", {}).items():
            item[key] = item.get(key, 0) + value

        for key, value in operation.get("$max", {}).items():
            if key not in item or item[key] < value:
                item[key] = value

        for key in operation.get("$unset", {}).keys():
            item.pop(key, None)


class FakeDb:
    def __init__(self):
        self.author_profiles = FakeCollection()
        self.author_aliases = FakeCollection()
        self.activity_snapshots = FakeCollection()
        self.activity_author_day_summary_snapshots = FakeCollection()
        self.activity_day_summary_snapshots = FakeCollection()
        self.activity_snapshot_maintenance_state = FakeCollection()
        self.activity_summary_cache = FakeCollection()
        self.aggregate_metadata = FakeCollection()
        self.aggregate_day_state = FakeCollection()
        self.break_events = FakeCollection()
        self.break_sessions = FakeCollection()
        self.day_sessions = FakeCollection()
        self.deleted_author_profiles = FakeCollection()
        self.device_report_identities = FakeCollection()
        self.telegram_day_reminders = FakeCollection()
        self.telegram_online_prompts = FakeCollection()
        self.fake_online_settings = FakeCollection()
        self.fake_online_attempts = FakeCollection()
        self.telegram_break_activity_prompts = FakeCollection()
        self.telegram_duplicate_afk_prompts = FakeCollection()
        self.telegram_meeting_auto_afk_notifications = FakeCollection()
        self.telegram_meeting_recording_notifications = FakeCollection()
        self.telegram_meeting_notifications = FakeCollection()
        self.meeting_recordings = FakeCollection()
        self.meeting_summaries = FakeCollection()
        self.break_intervals = FakeCollection()
        self.meeting_events = FakeCollection()
        self.meeting_sessions = FakeCollection()
        self.meeting_intervals = FakeCollection()
        self.calendar_marks = FakeCollection()
        self.calendar_reasons = FakeCollection()
        self.daily_author_activity = FakeCollection()
        self.aggregate_session_state = FakeCollection()
        self.interval_settings = FakeCollection()
        self.manual_report_expectations = FakeCollection()
        self.raw_activity_events = FakeCollection()
        self.raw_event_batches = FakeCollection()
        self.raw_reports = FakeCollection()
        self.report_challenges = FakeCollection()
        self.report_refresh_requests = FakeCollection()
        self.report_rows = FakeCollection()
        self.report_security_events = FakeCollection()
        self.site_users = FakeCollection()
        self.site_sessions = FakeCollection()
        self.status_events = FakeCollection()
        self.status_states = FakeCollection()
        self.system_settings = FakeCollection()


def fake_repository() -> Any:
    repo: Any = BackendServices.__new__(BackendServices)
    repo.db = FakeDb()
    repo.default_send_interval_seconds = 60
    repo.avatar_cache_dir = Path(tempfile.mkdtemp())
    repo.activity_snapshot_maintenance_lock = threading.Lock()
    repo.activity_snapshot_background_disabled = True
    return repo


def set_idle_threshold(repo: Any, seconds: int) -> None:
    repo.db.interval_settings.update_one(
        {"kind": "global"},
        {"$set": {"idleThresholdSeconds": seconds}},
        upsert=True,
    )
