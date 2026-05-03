from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..api_security import SESSION_COOKIE_NAME, is_local_dev_request, set_session_cookie
from ..models import LoginIn


router = APIRouter(prefix="/api/v1/auth")


@router.post("/login")
def login(credentials: LoginIn, request: Request, response: Response) -> dict:
    user = request.app.state.repo.authenticate_site_user(credentials.email, credentials.password)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = request.app.state.repo.create_site_session(user["email"])
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/dev-login")
def dev_login(request: Request, response: Response) -> dict:
    if not is_local_dev_request(request):
        raise HTTPException(status_code=404, detail="Not found")

    users = [user for user in request.app.state.repo.site_users() if user.get("active")]
    user = next((item for item in users if item.get("role") == "admin"), users[0] if users else None)

    if not user:
        raise HTTPException(status_code=404, detail="No active local site user")

    token = request.app.state.repo.create_site_session(user["email"])
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    request.app.state.repo.delete_site_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def auth_me(request: Request) -> dict:
    user = request.app.state.repo.site_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    return {"user": user}
