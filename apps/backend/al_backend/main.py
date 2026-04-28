from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    AnalyticsScoreSettingsIn,
    AuthorProfileIn,
    BreakEventIn,
    CalendarMarkIn,
    CalendarReasonIn,
    HealthResponse,
    IntervalSettingsIn,
    PluginConfig,
    ReportIn,
    ReportRefreshRequest,
    SubmitReportResponse,
    SummaryResponse,
)
from .protocol import decode_alr1
from .repository import Repository
from .settings import load_settings


settings = load_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    repo = Repository(settings)
    try:
        repo.ensure_indexes()
    except Exception:
        pass
    app.state.repo = repo
    key_data = json.loads(settings.private_key_path.read_text())
    app.state.private_key_pem = key_data["privateKeyPem"]
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
    try:
        decoded = decode_alr1(app.state.private_key_pem, report.encrypted_packet)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Report decode failed: {exc}") from exc

    payload_source = decoded.payload.get("source")

    if payload_source and payload_source != report.source:
        raise HTTPException(status_code=400, detail="Report source does not match encrypted payload source")

    report_id = app.state.repo.save_report(
        source=report.source,
        plugin_version=report.plugin_version,
        encrypted_packet=report.encrypted_packet,
        payload=decoded.payload,
    )
    return SubmitReportResponse(ok=True, reportId=report_id)


@app.post("/api/v1/reports/request-refresh")
def request_report_refresh(refresh: ReportRefreshRequest) -> dict:
    return app.state.repo.request_report_refresh(author=refresh.author)


@app.put("/api/v1/settings/intervals")
def update_intervals(settings_in: IntervalSettingsIn) -> dict:
    return app.state.repo.upsert_interval_settings(
        default_send_interval_seconds=settings_in.default_send_interval_seconds,
        author=settings_in.author,
        author_send_interval_seconds=settings_in.author_send_interval_seconds,
    )


@app.get("/api/v1/settings/analytics-score")
def analytics_score_settings() -> dict:
    return app.state.repo.get_analytics_score_settings()


@app.put("/api/v1/settings/analytics-score")
def update_analytics_score_settings(settings_in: AnalyticsScoreSettingsIn) -> dict:
    return app.state.repo.upsert_analytics_score_settings(settings_in.model_dump(by_alias=True))


@app.put("/api/v1/authors/profile")
def upsert_author_profile(profile: AuthorProfileIn) -> dict:
    return app.state.repo.upsert_author_profile(
        raw_author=profile.raw_author,
        display_name=profile.display_name,
        team=profile.team,
        telegram_username=profile.telegram_username,
        plugin_enabled=profile.plugin_enabled,
        author_color=profile.author_color,
    )


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
def upsert_calendar_marks(mark: CalendarMarkIn) -> dict:
    return app.state.repo.upsert_calendar_marks(
        authors=mark.authors,
        dates=mark.dates,
        reason_id=mark.reason_id,
        note=mark.note,
    )


@app.delete("/api/v1/calendar/marks")
def delete_calendar_mark(author: str = Query(min_length=1), date: str = Query(min_length=1)) -> dict:
    return app.state.repo.delete_calendar_mark(raw_author=author, date=date)


@app.put("/api/v1/calendar/reasons")
def upsert_calendar_reason(reason: CalendarReasonIn) -> dict:
    return app.state.repo.upsert_calendar_reason(reason_id=reason.id or reason.label, label=reason.label)


@app.delete("/api/v1/calendar/reasons/{reason_id}")
def delete_calendar_reason(reason_id: str) -> dict:
    return app.state.repo.delete_calendar_reason(reason_id=reason_id)


@app.get("/api/v1/reports/summary", response_model=SummaryResponse)
def reports_summary(start_date: str | None = Query(default=None, alias="startDate"), end_date: str | None = Query(default=None, alias="endDate")) -> SummaryResponse:
    return SummaryResponse(
        authors=app.state.repo.list_authors(),
        reports=app.state.repo.latest_reports(start_date=start_date, end_date=end_date),
        intervalSettings=app.state.repo.get_interval_settings(),
        activitySummary=app.state.repo.activity_summary(start_date=start_date, end_date=end_date),
    )
