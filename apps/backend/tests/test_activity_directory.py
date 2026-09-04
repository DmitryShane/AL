from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from al_backend.api_security import current_site_user
from al_backend.dependencies import get_author_service
from al_backend.repositories.authors import AuthorRepository
from al_backend.routers.authors import router
from tests.fakes import FakeCollection


class DirectoryRepository(AuthorRepository):
    def _profiles_by_raw_author(self):
        return {row["rawAuthor"]: row for row in self.db.author_profiles.find({})}

    def resolve_author_alias(self, name):
        alias = self.db.author_aliases.find_one({"sourceRawAuthor": name})
        return alias["targetRawAuthor"] if alias else name


def repository():
    repo = DirectoryRepository()
    repo.db = SimpleNamespace(**{name: FakeCollection() for name in (
        "author_profiles", "daily_author_activity", "author_aliases", "device_report_identities"
    )})
    repo.db.author_profiles.insert_one({"rawAuthor": "Alice", "displayName": "Alice", "team": "Core"})
    repo.db.daily_author_activity.insert_one({"author": "Old Alice", "activeSeconds": 900})
    repo.db.daily_author_activity.insert_one({"author": "Bob"})
    repo.db.daily_author_activity.insert_one({"author": "Device99"})
    repo.db.author_aliases.insert_one({"sourceRawAuthor": "Old Alice", "targetRawAuthor": "Alice"})
    return repo


def test_directory_reads_only_small_collections_and_returns_identity():
    rows = repository().activity_author_directory()
    assert [row["rawAuthor"] for row in rows] == ["Alice", "Bob"]
    assert all(set(row) == {"rawAuthor", "displayName", "team", "avatarUrl"} for row in rows)


def test_directory_endpoint_requires_auth_and_allows_viewer():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_author_service] = repository
    client = TestClient(app)
    assert client.get("/api/v1/activity/authors").status_code == 401
    app.dependency_overrides[current_site_user] = lambda: {"role": "viewer"}
    response = client.get("/api/v1/activity/authors")
    assert response.status_code == 200
    assert len(response.json()["authors"]) == 2
