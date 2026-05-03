from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api_security import SESSION_COOKIE_NAME
from .container import BackendContainer
from .routers import analytics, auth, authors, calendar, discord, health, reports, settings as settings_router, site_users, telegram
from .settings import Settings


logger = logging.getLogger("al_backend")

PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/plugins/config",
    "/api/v1/reports",
    "/api/v1/reports/challenge",
    "/api/v1/break-events",
    "/api/v1/telegram/reminders/due",
    "/api/v1/telegram/reminders/sent",
    "/api/v1/telegram/reminders/close",
    "/api/v1/telegram/private-chat",
    "/api/v1/discord/voice-events",
    "/api/v1/discord/meeting-auto-afk",
    "/api/v1/discord/meeting-recordings/start",
    "/api/v1/discord/meeting-recordings/finish",
    "/api/v1/discord/settings",
    "/api/v1/auth/login",
    "/api/v1/auth/me",
    "/api/v1/auth/dev-login",
}
DASHBOARD_METRIC_PATHS = {
    "/api/v1/health",
    "/api/v1/reports/summary",
    "/api/v1/reports/table",
    "/api/v1/analytics/summary",
    "/api/v1/calendar/summary",
}


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = BackendContainer(settings)
        try:
            container.startup()
        except Exception:
            pass
        app.state.container = container
        yield
        container.close()

    app = FastAPI(title="AL Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(site_auth_middleware)
    app.include_router(auth.router)
    app.include_router(site_users.router)
    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(settings_router.router)
    app.include_router(authors.router)
    app.include_router(telegram.router)
    app.include_router(discord.router)
    app.include_router(analytics.router)
    app.include_router(calendar.router)
    return app

async def site_auth_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    if request.method == "OPTIONS" or request.url.path not in PUBLIC_API_PATHS and not request.url.path.startswith("/api/v1/"):
        response = await call_next(request)
        return _with_dashboard_metrics(request, response, started_at)

    if request.url.path in PUBLIC_API_PATHS:
        response = await call_next(request)
        return _with_dashboard_metrics(request, response, started_at)

    user = request.app.state.container.auth.site_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))

    if not user:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    request.state.site_user = user
    response = await call_next(request)
    return _with_dashboard_metrics(request, response, started_at)


def _with_dashboard_metrics(request: Request, response: Response, started_at: float) -> Response:
    if request.url.path not in DASHBOARD_METRIC_PATHS:
        return response

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response.headers["X-AL-Response-Time-Ms"] = str(elapsed_ms)
    content_length = response.headers.get("content-length", "")

    if content_length:
        response.headers["X-AL-Response-Bytes"] = content_length

    logger.info(
        "dashboard_endpoint path=%s status=%s duration_ms=%.2f bytes=%s",
        request.url.path,
        response.status_code,
        elapsed_ms,
        content_length or "unknown",
    )

    return response
