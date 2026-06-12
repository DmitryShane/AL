from __future__ import annotations

from types import SimpleNamespace

from al_backend.indexes import IndexManager, RAW_REPORTS_RETENTION_INDEX_NAME, RAW_REPORTS_RETENTION_SECONDS


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
