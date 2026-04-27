from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .models import HealthResponse, IntervalSettingsIn, PluginConfig, ReportIn, SubmitReportResponse, SummaryResponse
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
    project_id: str = Query(default="", alias="projectId"),
) -> PluginConfig:
    return PluginConfig(
        source=source,
        author=author,
        projectId=project_id,
        enabled=True,
        sendIntervalSeconds=app.state.repo.get_interval_for_author(author),
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


@app.put("/api/v1/settings/intervals")
def update_intervals(settings_in: IntervalSettingsIn) -> dict:
    return app.state.repo.upsert_interval_settings(
        default_send_interval_seconds=settings_in.default_send_interval_seconds,
        author=settings_in.author,
        author_send_interval_seconds=settings_in.author_send_interval_seconds,
    )


@app.get("/api/v1/reports/summary", response_model=SummaryResponse)
def reports_summary() -> SummaryResponse:
    return SummaryResponse(
        authors=app.state.repo.list_authors(),
        reports=app.state.repo.latest_reports(),
        intervalSettings=app.state.repo.get_interval_settings(),
    )
