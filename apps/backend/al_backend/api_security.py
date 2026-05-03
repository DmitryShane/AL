from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response


SESSION_COOKIE_NAME = "al_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
ROLE_PERMISSIONS = {
    "admin": {"viewDashboard", "manageSettings", "manageUsers"},
    "editor": {"viewDashboard", "manageSettings"},
    "viewer": {"viewDashboard"},
}


def is_local_dev_request(request: Request) -> bool:
    return request.url.hostname in {"127.0.0.1", "localhost"}


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def current_site_user(request: Request) -> dict:
    user = getattr(request.state, "site_user", None)

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    return user


def require_permission(permission: str):
    def dependency(user: dict = Depends(current_site_user)) -> dict:
        role = user.get("role", "viewer")

        if permission not in ROLE_PERMISSIONS.get(role, set()):
            raise HTTPException(status_code=403, detail="Permission denied")

        return user

    return dependency
