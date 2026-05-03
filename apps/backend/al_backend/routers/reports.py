from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_author_service, get_report_service, get_settings_service, get_summary_service
from ..models import (
    PluginConfig,
    ReportChallengeIn,
    ReportChallengeResponse,
    ReportIn,
    ReportRefreshRequest,
    SubmitReportResponse,
    SummaryResponse,
)
from ..protocol import decode_alr1, generate_report_challenge_keys


router = APIRouter()


@router.get("/api/v1/plugins/config", response_model=PluginConfig)
def plugin_config(
    source: str = Query(min_length=1),
    author: str = Query(default="Unknown User"),
    author_email: str = Query(default="", alias="authorEmail"),
    project_id: str = Query(default="", alias="projectId"),
    service: BackendServices = Depends(get_report_service),
) -> PluginConfig:
    service.update_author_email(author, author_email)
    enabled = service.is_plugin_enabled_for_author(author)
    submit_report_now = enabled and service.should_submit_report_now(author)

    return PluginConfig(
        source=source,
        author=author,
        projectId=project_id,
        enabled=enabled,
        sendIntervalSeconds=service.get_interval_for_author(author),
        submitReportNow=submit_report_now,
    )


@router.post("/api/v1/reports", response_model=SubmitReportResponse)
def submit_report(report: ReportIn, service: BackendServices = Depends(get_report_service)) -> SubmitReportResponse:
    if not service.get_plugin_ingest_enabled():
        return SubmitReportResponse(ok=True, reportId="", ignored=True)

    challenge = service.claim_report_challenge(report.challenge_id, report.source, report.device_id)

    if not challenge:
        service.log_report_security_event(
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
        service.log_report_security_event(
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
        service.log_report_security_event(
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

    report_id = service.save_report(
        source=report.source,
        plugin_version=report.plugin_version,
        encrypted_packet=report.encrypted_packet,
        payload=decoded.payload,
        challenge_id=report.challenge_id,
        device_id=report.device_id,
    )
    return SubmitReportResponse(ok=True, reportId=report_id)


@router.post("/api/v1/reports/challenge", response_model=ReportChallengeResponse)
def create_report_challenge(
    challenge_in: ReportChallengeIn,
    service: BackendServices = Depends(get_report_service),
) -> ReportChallengeResponse:
    keys = generate_report_challenge_keys()
    challenge = service.create_report_challenge(challenge_in, keys)
    return ReportChallengeResponse(
        challengeId=challenge["challengeId"],
        publicModulus=challenge["publicModulus"],
        publicExponent=challenge["publicExponent"],
        expiresAt=challenge["expiresAt"],
    )


@router.post("/api/v1/reports/request-refresh")
def request_report_refresh(
    refresh: ReportRefreshRequest,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_report_service),
) -> dict:
    return service.request_report_refresh(author=refresh.author)


@router.get("/api/v1/reports/table")
def reports_table(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    date_mode: str | None = Query(default=None, alias="dateMode"),
    author: str | None = Query(default=None),
    source: str | None = Query(default=None),
    hour: int | None = Query(default=None, ge=0, le=23),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: BackendServices = Depends(get_summary_service),
) -> dict:
    return service.reports_page(
        start_date=start_date,
        end_date=end_date,
        date_mode=date_mode,
        author=author,
        source=source,
        hour=hour,
        limit=limit,
        offset=offset,
    )


@router.get("/api/v1/reports/summary", response_model=SummaryResponse)
def reports_summary(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    date_mode: str | None = Query(default=None, alias="dateMode"),
    view: str = Query(default="activity", pattern="^(authors|activity|alerts|settings)$"),
    author_service: BackendServices = Depends(get_author_service),
    settings_service: BackendServices = Depends(get_settings_service),
    summary_service: BackendServices = Depends(get_summary_service),
) -> SummaryResponse:
    include_profiles = view == "settings"
    include_hourly = view == "activity"
    include_breakdowns = view == "activity"

    return SummaryResponse(
        authors=author_service.list_authors(),
        reports=[],
        intervalSettings=settings_service.get_interval_settings(),
        discordSettings=settings_service.get_discord_settings(),
        activitySummary=summary_service.activity_summary(
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
            include_profiles=include_profiles,
            include_hourly=include_hourly,
            include_breakdowns=include_breakdowns,
        ),
    )
