from fastapi import Response

from al_backend.api_security import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, set_session_cookie
from al_backend.auth import session_token_hash
from tests.fakes import fake_repository


def test_session_cookie_is_persistent_for_configured_max_age():
    response = Response()

    set_session_cookie(response, "session-token")

    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert f"Max-Age={SESSION_MAX_AGE_SECONDS}" in set_cookie
    assert "HttpOnly" in set_cookie


def test_site_session_expiration_matches_cookie_max_age():
    repo = fake_repository()

    token = repo.create_site_session("Owner@Example.com")

    session = repo.db.site_sessions.find_one({"tokenHash": session_token_hash(token)})
    assert session is not None
    assert session["email"] == "owner@example.com"
    assert int((session["expiresAt"] - session["createdAt"]).total_seconds()) == SESSION_MAX_AGE_SECONDS
