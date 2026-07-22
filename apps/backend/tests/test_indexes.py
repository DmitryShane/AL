from __future__ import annotations

from types import SimpleNamespace

from al_backend.indexes import IndexManager, RAW_REPORTS_RETENTION_INDEX_NAME, RAW_REPORTS_RETENTION_SECONDS
from tests.fakes import FakeCollection


def test_raw_reports_retention_index_replaces_plain_received_at_index() -> None:
    collection = FakeIndexCollection(
        [
            {"name": "receivedAt_-1", "key": {"receivedAt": -1}},
            {"name": "source_1_receivedAt_-1", "key": {"source": 1, "receivedAt": -1}},
        ]
    )
    manager = IndexManager(SimpleNamespace(raw_reports=collection))

    manager._ensure_raw_reports_retention_index()

    assert "receivedAt_-1" in collection.dropped_indexes
    assert "source_1_receivedAt_-1" not in collection.dropped_indexes
    assert collection.created_indexes[-1] == {
        "keys": [("receivedAt", 1)],
        "expireAfterSeconds": RAW_REPORTS_RETENTION_SECONDS,
        "name": RAW_REPORTS_RETENTION_INDEX_NAME,
    }


def test_rebuild_hot_path_indexes_are_created() -> None:
    db = DynamicFakeIndexDb()
    manager = IndexManager(db)

    manager.ensure_indexes()

    assert {"keys": [("date", 1), ("author", 1), ("occurredAtUtc", 1)]} in db.raw_activity_events.created_indexes
    assert {
        "keys": [("date", 1), ("author", 1), ("source", 1), ("eventType", 1), ("occurredAtUtc", 1)]
    } in db.raw_activity_events.created_indexes
    assert {"keys": [("batchId", 1)]} in db.raw_event_batches.created_indexes
    assert {"keys": [("token", 1), ("batchId", 1)]} in db.aggregate_rebuild_event_deltas.created_indexes
    assert {"keys": [("rawAuthor", 1), ("date", 1), ("reasonId", 1)]} in db.calendar_marks.created_indexes


def test_status_report_rows_are_deduplicated_before_unique_index() -> None:
    report_rows = FakeCollection()
    report_rows.insert_one({"_id": "first", "source": "status", "author": "A", "date": "2026-07-22", "statusEventType": "offline", "recordedAt": "2026-07-22T09:00:00+00:00"})
    report_rows.insert_one({"_id": "second", "source": "status", "author": "A", "date": "2026-07-22", "statusEventType": "offline", "recordedAt": "2026-07-22T09:00:00+00:00"})
    manager = IndexManager(SimpleNamespace(report_rows=report_rows))

    manager._deduplicate_status_report_rows()

    assert len(report_rows.items) == 1
    assert report_rows.items[0]["statusEventKey"] == "A|2026-07-22|offline|2026-07-22T09:00:00+00:00"


class FakeIndexCollection:
    def __init__(self, indexes: list[dict]):
        self.indexes = indexes
        self.dropped_indexes: list[str] = []
        self.created_indexes: list[dict] = []

    def list_indexes(self) -> list[dict]:
        return list(self.indexes)

    def drop_index(self, name: str) -> None:
        self.dropped_indexes.append(name)

    def create_index(self, keys, **kwargs) -> None:
        self.created_indexes.append({"keys": keys, **kwargs})


class DynamicFakeIndexDb:
    def __getattr__(self, name: str) -> FakeIndexCollection:
        collection = FakeIndexCollection([])
        setattr(self, name, collection)
        return collection
