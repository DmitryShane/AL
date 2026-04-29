from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import (
    AuthorAliasIn,
    AuthorProfileIn,
    BreakEventIn,
    CalendarMarkIn,
    CalendarReasonIn,
    HealthResponse,
    IntervalSettingsIn,
    LoginIn,
    PluginConfig,
    ReportChallengeIn,
    ReportChallengeResponse,
    ReportIn,
    ReportRefreshRequest,
    SiteUserIn,
    SubmitReportResponse,
    SummaryResponse,
)
from .protocol import decode_alr1, generate_report_challenge_keys
from .repository import Repository
from .settings import load_settings


settings = load_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    repo = Repository(settings)
    try:
        repo.ensure_indexes()
        repo.ensure_bootstrap_site_admin(settings.admin_email, settings.admin_password)
    except Exception:
        pass
    app.state.repo = repo
    yield
    repo.client.close()


app = FastAPI(title="AL Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_COOKIE_NAME = "al_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
ROLE_PERMISSIONS = {
    "admin": {"viewDashboard", "manageSettings", "manageUsers"},
    "editor": {"viewDashboard", "manageSettings"},
    "viewer": {"viewDashboard"},
}
PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/plugins/config",
    "/api/v1/reports",
    "/api/v1/reports/challenge",
    "/api/v1/break-events",
    "/api/v1/auth/login",
    "/api/v1/auth/me",
    "/api/v1/auth/dev-login",
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


@app.middleware("http")
async def site_auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path not in PUBLIC_API_PATHS and not request.url.path.startswith("/api/v1/"):
        return await call_next(request)

    if request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)

    user = request.app.state.repo.site_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))

    if not user:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    request.state.site_user = user
    return await call_next(request)


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


@app.post("/api/v1/auth/login")
def login(credentials: LoginIn, response: Response) -> dict:
    user = app.state.repo.authenticate_site_user(credentials.email, credentials.password)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = app.state.repo.create_site_session(user["email"])
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@app.post("/api/v1/auth/dev-login")
def dev_login(request: Request, response: Response) -> dict:
    if not is_local_dev_request(request):
        raise HTTPException(status_code=404, detail="Not found")

    users = [user for user in app.state.repo.site_users() if user.get("active")]
    user = next((item for item in users if item.get("role") == "admin"), users[0] if users else None)

    if not user:
        raise HTTPException(status_code=404, detail="No active local site user")

    token = app.state.repo.create_site_session(user["email"])
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response) -> dict:
    app.state.repo.delete_site_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/v1/auth/me")
def auth_me(request: Request) -> dict:
    user = app.state.repo.site_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    return {"user": user}


@app.get("/api/v1/site-users")
def site_users(_: dict = Depends(require_permission("manageUsers"))) -> dict:
    return {"users": app.state.repo.site_users()}


@app.put("/api/v1/site-users")
def upsert_site_user(user_in: SiteUserIn, _: dict = Depends(require_permission("manageUsers"))) -> dict:
    result = app.state.repo.upsert_site_user(
        email=user_in.email,
        display_name=user_in.display_name,
        role=user_in.role,
        active=user_in.active,
        password=user_in.password,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "User save failed"))

    return result


@app.delete("/api/v1/site-users/{email}")
def delete_site_user(email: str, current_user: dict = Depends(require_permission("manageUsers"))) -> dict:
    if email.strip().lower() == current_user.get("email"):
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    return app.state.repo.delete_site_user(email)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        mongo = app.state.repo.ping()
    except Exception:
        mongo = False

    return HealthResponse(ok=mongo, mongo=mongo)


@app.get("/api/v1/plugins/config", response_model=PluginConfig)
def plugin_config(
    source: str = Query(min_length=1),
    author: str = Query(default="Unknown User"),
    author_email: str = Query(default="", alias="authorEmail"),
    project_id: str = Query(default="", alias="projectId"),
) -> PluginConfig:
    app.state.repo.update_author_email(author, author_email)
    submit_report_now = app.state.repo.should_submit_report_now(author)

    return PluginConfig(
        source=source,
        author=author,
        projectId=project_id,
        enabled=app.state.repo.is_plugin_enabled_for_author(author),
        sendIntervalSeconds=app.state.repo.get_interval_for_author(author),
        submitReportNow=submit_report_now,
    )


@app.post("/api/v1/reports", response_model=SubmitReportResponse)
def submit_report(report: ReportIn) -> SubmitReportResponse:
    challenge = app.state.repo.claim_report_challenge(report.challenge_id, report.source, report.device_id)

    if not challenge:
        app.state.repo.log_report_security_event(
            event_type="invalid_challenge",
            source=report.source,
            plugin_version=report.plugin_version,
            device_id=report.device_id,
            challenge_id=report.challenge_id,
            message="Report used an unknown, expired, or already consumed challenge.",
        )
        raise HTTPException(status_code=400, detail="Invalid report challenge")

    try:
        decoded = decode_alr1(challenge["privateKeyPem"], report.encrypted_packet)
    except Exception as exc:
        app.state.repo.log_report_security_event(
            event_type="decode_failed",
            source=report.source,
            plugin_version=report.plugin_version,
            author=challenge.get("author"),
            author_email=challenge.get("authorEmail"),
            project_id=challenge.get("projectId"),
            session_id=challenge.get("sessionId"),
            device_id=report.device_id or challenge.get("deviceId"),
            challenge_id=report.challenge_id,
            message=f"Report decode failed: {exc}",
        )
        raise HTTPException(status_code=400, detail=f"Report decode failed: {exc}") from exc

    payload_source = decoded.payload.get("source")

    if payload_source and payload_source != report.source:
        app.state.repo.log_report_security_event(
            event_type="source_mismatch",
            source=report.source,
            plugin_version=report.plugin_version,
            author=decoded.payload.get("author") or challenge.get("author"),
            author_email=decoded.payload.get("authorEmail") or challenge.get("authorEmail"),
            project_id=decoded.payload.get("projectId") or challenge.get("projectId"),
            session_id=decoded.payload.get("sessionId") or challenge.get("sessionId"),
            device_id=report.device_id or decoded.payload.get("deviceId") or challenge.get("deviceId"),
            challenge_id=report.challenge_id,
            message="Report source does not match encrypted payload source.",
        )
        raise HTTPException(status_code=400, detail="Report source does not match encrypted payload source")

    report_id = app.state.repo.save_report(
        source=report.source,
        plugin_version=report.plugin_version,
        encrypted_packet=report.encrypted_packet,
        payload=decoded.payload,
        challenge_id=report.challenge_id,
        device_id=report.device_id,
    )
    return SubmitReportResponse(ok=True, reportId=report_id)


@app.post("/api/v1/reports/challenge", response_model=ReportChallengeResponse)
def create_report_challenge(challenge_in: ReportChallengeIn) -> ReportChallengeResponse:
    keys = generate_report_challenge_keys()
    challenge = app.state.repo.create_report_challenge(challenge_in, keys)
    return ReportChallengeResponse(
        challengeId=challenge["challengeId"],
        publicModulus=challenge["publicModulus"],
        publicExponent=challenge["publicExponent"],
        expiresAt=challenge["expiresAt"],
    )


@app.post("/api/v1/reports/request-refresh")
def request_report_refresh(refresh: ReportRefreshRequest, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.request_report_refresh(author=refresh.author)


@app.put("/api/v1/settings/intervals")
def update_intervals(settings_in: IntervalSettingsIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_interval_settings(
        default_send_interval_seconds=settings_in.default_send_interval_seconds,
        author=settings_in.author,
        author_send_interval_seconds=settings_in.author_send_interval_seconds,
    )


@app.put("/api/v1/authors/profile")
def upsert_author_profile(profile: AuthorProfileIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_author_profile(
        raw_author=profile.raw_author,
        display_name=profile.display_name,
        team=profile.team,
        telegram_username=profile.telegram_username,
        plugin_enabled=profile.plugin_enabled,
        author_color=profile.author_color,
    )


@app.get("/api/v1/authors/aliases")
def author_aliases(_: dict = Depends(require_permission("manageSettings"))) -> dict:
    return {"aliases": app.state.repo.author_aliases()}


@app.put("/api/v1/authors/aliases")
def upsert_author_alias(alias: AuthorAliasIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    result = app.state.repo.upsert_author_alias(
        source_raw_author=alias.source_raw_author,
        target_raw_author=alias.target_raw_author,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Alias save failed"))

    return result


@app.delete("/api/v1/authors/aliases/{source_raw_author}")
def delete_author_alias(source_raw_author: str, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.delete_author_alias(source_raw_author=source_raw_author)


@app.delete("/api/v1/authors/{raw_author}/data")
def delete_author_data(raw_author: str, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.delete_author_data(raw_author=raw_author)


@app.delete("/api/v1/authors/{raw_author}/profile")
def delete_author_profile(raw_author: str, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.delete_author_profile(raw_author=raw_author)


@app.post("/api/v1/break-events")
def record_break_event(event: BreakEventIn) -> dict:
    return app.state.repo.record_break_event(
        telegram_username=event.telegram_username,
        event_type=event.event_type,
        timestamp=event.timestamp,
    )


@app.get("/api/v1/analytics/summary")
def analytics_summary(period: str = Query(default="7d", pattern="^(7d|30d|90d|year)$")) -> dict:
    return app.state.repo.analytics_summary(period=period)


@app.get("/api/v1/calendar/summary")
def calendar_summary(year: int = Query(ge=2000, le=2100)) -> dict:
    return app.state.repo.calendar_summary(year=year)


@app.put("/api/v1/calendar/marks")
def upsert_calendar_marks(mark: CalendarMarkIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_calendar_marks(
        authors=mark.authors,
        dates=mark.dates,
        reason_id=mark.reason_id,
        note=mark.note,
    )


@app.delete("/api/v1/calendar/marks")
def delete_calendar_mark(
    author: str = Query(min_length=1), date: str = Query(min_length=1), _: dict = Depends(require_permission("manageSettings"))
) -> dict:
    return app.state.repo.delete_calendar_mark(raw_author=author, date=date)


@app.put("/api/v1/calendar/reasons")
def upsert_calendar_reason(reason: CalendarReasonIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_calendar_reason(reason_id=reason.id or reason.label, label=reason.label)


@app.delete("/api/v1/calendar/reasons/{reason_id}")
def delete_calendar_reason(reason_id: str, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.delete_calendar_reason(reason_id=reason_id)


@app.get("/api/v1/reports/summary", response_model=SummaryResponse)
def reports_summary(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    date_mode: str | None = Query(default=None, alias="dateMode"),
) -> SummaryResponse:
    return SummaryResponse(
        authors=app.state.repo.list_authors(),
        reports=app.state.repo.latest_reports(start_date=start_date, end_date=end_date, date_mode=date_mode),
        intervalSettings=app.state.repo.get_interval_settings(),
        activitySummary=app.state.repo.activity_summary(start_date=start_date, end_date=end_date, date_mode=date_mode),
    )
