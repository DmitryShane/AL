from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import (
    AuthorAliasIn,
    AuthorProfileIn,
    BreakEventIn,
    CalendarMarkIn,
    CalendarMarksDeleteIn,
    CalendarReasonIn,
    DiscordSettingsIn,
    DiscordMeetingAutoAfkIn,
    DiscordMeetingRecordingFailIn,
    DiscordMeetingRecordingStartIn,
    DiscordMeetingRecordingStatusIn,
    DiscordVoiceEventIn,
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
    TelegramPrivateChatIn,
    TelegramReminderCloseIn,
    TelegramReminderSentIn,
)
from .protocol import decode_alr1, generate_report_challenge_keys
from .repository import Repository
from .settings import load_settings
from .meeting_summary import generate_meeting_summary
from .api_security import SESSION_COOKIE_NAME, require_permission
from .routers.auth import router as auth_router


settings = load_settings()
logger = logging.getLogger("al_backend")


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
app.include_router(auth_router)

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

@app.middleware("http")
async def site_auth_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    if request.method == "OPTIONS" or request.url.path not in PUBLIC_API_PATHS and not request.url.path.startswith("/api/v1/"):
        response = await call_next(request)
        return _with_dashboard_metrics(request, response, started_at)

    if request.url.path in PUBLIC_API_PATHS:
        response = await call_next(request)
        return _with_dashboard_metrics(request, response, started_at)

    user = request.app.state.repo.site_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))

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

def require_telegram_bot_secret(request: Request) -> None:
    if not settings.telegram_bot_secret:
        raise HTTPException(status_code=503, detail="Telegram bot secret is not configured")

    if request.headers.get("x-al-telegram-bot-secret") != settings.telegram_bot_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram bot secret")


def require_discord_bot_secret(request: Request) -> None:
    if not settings.discord_bot_secret:
        raise HTTPException(status_code=503, detail="Discord bot secret is not configured")

    if request.headers.get("x-al-discord-bot-secret") != settings.discord_bot_secret:
        raise HTTPException(status_code=403, detail="Invalid Discord bot secret")


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


def parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}

    if isinstance(parsed, dict):
        return parsed

    return {}


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
    enabled = app.state.repo.is_plugin_enabled_for_author(author)
    submit_report_now = enabled and app.state.repo.should_submit_report_now(author)

    return PluginConfig(
        source=source,
        author=author,
        projectId=project_id,
        enabled=enabled,
        sendIntervalSeconds=app.state.repo.get_interval_for_author(author),
        submitReportNow=submit_report_now,
    )


@app.post("/api/v1/reports", response_model=SubmitReportResponse)
def submit_report(report: ReportIn) -> SubmitReportResponse:
    if not app.state.repo.get_plugin_ingest_enabled():
        return SubmitReportResponse(ok=True, reportId="", ignored=True)

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


@app.get("/api/v1/reports/table")
def reports_table(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    date_mode: str | None = Query(default=None, alias="dateMode"),
    author: str | None = Query(default=None),
    source: str | None = Query(default=None),
    hour: int | None = Query(default=None, ge=0, le=23),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return app.state.repo.reports_page(
        start_date=start_date,
        end_date=end_date,
        date_mode=date_mode,
        author=author,
        source=source,
        hour=hour,
        limit=limit,
        offset=offset,
    )


@app.put("/api/v1/settings/intervals")
def update_intervals(settings_in: IntervalSettingsIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_interval_settings(
        default_send_interval_seconds=settings_in.default_send_interval_seconds,
        idle_threshold_seconds=settings_in.idle_threshold_seconds,
        plugin_ingest_enabled=settings_in.plugin_ingest_enabled,
        author=settings_in.author,
        author_send_interval_seconds=settings_in.author_send_interval_seconds,
    )


@app.put("/api/v1/settings/discord")
def update_discord_settings(settings_in: DiscordSettingsIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_discord_summary_settings(
        meeting_auto_afk_timeout_seconds=settings_in.meeting_auto_afk_timeout_seconds,
        meeting_summaries_enabled=settings_in.meeting_summaries_enabled,
        meeting_summary_min_participants=settings_in.meeting_summary_min_participants,
        meeting_summary_min_duration_seconds=settings_in.meeting_summary_min_duration_seconds,
        meeting_summary_language=settings_in.meeting_summary_language,
        meeting_summary_recipient=settings_in.meeting_summary_recipient,
        meeting_audio_retention_seconds=settings_in.meeting_audio_retention_seconds,
        meeting_summary_prompt=settings_in.meeting_summary_prompt,
    )


@app.get("/api/v1/discord/settings")
def discord_settings(request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.get_discord_settings()


@app.get("/api/v1/discord/meeting-recordings/recent")
def recent_discord_meeting_recordings(_: dict = Depends(require_permission("manageSettings"))) -> dict:
    return {"recordings": app.state.repo.recent_meeting_recordings(limit=10)}


@app.put("/api/v1/authors/profile")
def upsert_author_profile(profile: AuthorProfileIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.upsert_author_profile(
        raw_author=profile.raw_author,
        display_name=profile.display_name,
        team=profile.team,
        telegram_username=profile.telegram_username,
        discord_user_id=profile.discord_user_id,
        discord_username=profile.discord_username,
        plugin_enabled=profile.plugin_enabled,
        auto_break_enabled=profile.auto_break_enabled,
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


@app.post("/api/v1/discord/voice-events")
def record_discord_voice_event(event: DiscordVoiceEventIn, request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.record_discord_voice_event(
        discord_user_id=event.discord_user_id,
        discord_username=event.discord_username,
        event_type=event.event_type,
        guild_id=event.guild_id,
        channel_id=event.channel_id,
        timestamp=event.timestamp,
    )


@app.post("/api/v1/discord/meeting-auto-afk")
def record_discord_meeting_auto_afk(event: DiscordMeetingAutoAfkIn, request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.record_discord_meeting_auto_afk(
        discord_user_id=event.discord_user_id,
        discord_username=event.discord_username,
        guild_id=event.guild_id,
        meeting_channel_id=event.meeting_channel_id,
        afk_channel_id=event.afk_channel_id,
        solo_started_at=event.solo_started_at,
        moved_at=event.moved_at,
        threshold_seconds=event.threshold_seconds,
    )


@app.post("/api/v1/discord/meeting-recordings/start")
def record_discord_meeting_recording_started(event: DiscordMeetingRecordingStartIn, request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.record_meeting_recording_started(
        recording_id=event.recording_id,
        guild_id=event.guild_id,
        channel_id=event.channel_id,
        started_at=event.started_at,
        participant_discord_user_ids=event.participant_discord_user_ids,
        participant_names=event.participant_names,
    )


@app.post("/api/v1/discord/meeting-recordings/fail")
def record_discord_meeting_recording_failed(event: DiscordMeetingRecordingFailIn, request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.mark_meeting_recording_failed(
        recording_id=event.recording_id,
        ended_at=event.ended_at,
        error=event.error,
    )


@app.post("/api/v1/discord/meeting-recordings/status")
def update_discord_meeting_recording_status(event: DiscordMeetingRecordingStatusIn, request: Request) -> dict:
    require_discord_bot_secret(request)
    return app.state.repo.update_meeting_recording_status(
        recording_id=event.recording_id,
        status=event.status,
    )


@app.post("/api/v1/discord/meeting-recordings/finish")
def record_discord_meeting_recording_finished(
    request: Request,
    recording_id: str = Form(alias="recordingId"),
    guild_id: str | None = Form(default=None, alias="guildId"),
    channel_id: str | None = Form(default=None, alias="channelId"),
    started_at: str = Form(alias="startedAt"),
    ended_at: str = Form(alias="endedAt"),
    participant_discord_user_ids: str = Form(default="[]", alias="participantDiscordUserIds"),
    participant_names: str = Form(default="[]", alias="participantNames"),
    audio_frame_count: int = Form(default=0, alias="audioFrameCount"),
    non_silent_frame_count: int = Form(default=0, alias="nonSilentFrameCount"),
    corrupted_packet_count: int = Form(default=0, alias="corruptedPacketCount"),
    unknown_source_frame_count: int = Form(default=0, alias="unknownSourceFrameCount"),
    bot_frame_count: int = Form(default=0, alias="botFrameCount"),
    empty_pcm_frame_count: int = Form(default=0, alias="emptyPcmFrameCount"),
    silence_padding_frame_count: int = Form(default=0, alias="silencePaddingFrameCount"),
    out_of_order_frame_count: int = Form(default=0, alias="outOfOrderFrameCount"),
    mixed_user_count: int = Form(default=0, alias="mixedUserCount"),
    per_user_frame_counts: str = Form(default="{}", alias="perUserFrameCounts"),
    per_user_non_silent_frame_counts: str = Form(default="{}", alias="perUserNonSilentFrameCounts"),
    listen_error_count: int = Form(default=0, alias="listenErrorCount"),
    listen_error: str = Form(default="", alias="listenError"),
    audio_quality_status: str = Form(default="", alias="audioQualityStatus"),
    audio_size_bytes: int = Form(default=0, alias="audioSizeBytes"),
    audio: UploadFile = File(),
) -> dict:
    require_discord_bot_secret(request)
    participant_ids = json.loads(participant_discord_user_ids or "[]")
    names = json.loads(participant_names or "[]")
    temp_path = ""

    try:
        suffix = os.path.splitext(audio.filename or "meeting.wav")[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            temp_file.write(audio.file.read())

        return app.state.repo.process_meeting_recording_finished(
            recording_id=recording_id,
            guild_id=guild_id,
            channel_id=channel_id,
            started_at=started_at,
            ended_at=ended_at,
            participant_discord_user_ids=[str(item) for item in participant_ids],
            participant_names=[str(item) for item in names],
            audio_frame_count=audio_frame_count,
            non_silent_frame_count=non_silent_frame_count,
            corrupted_packet_count=corrupted_packet_count,
            unknown_source_frame_count=unknown_source_frame_count,
            bot_frame_count=bot_frame_count,
            empty_pcm_frame_count=empty_pcm_frame_count,
            silence_padding_frame_count=silence_padding_frame_count,
            out_of_order_frame_count=out_of_order_frame_count,
            mixed_user_count=mixed_user_count,
            per_user_frame_counts=parse_json_object(per_user_frame_counts),
            per_user_non_silent_frame_counts=parse_json_object(per_user_non_silent_frame_counts),
            listen_error_count=listen_error_count,
            listen_error=listen_error,
            audio_quality_status=audio_quality_status,
            audio_size_bytes=audio_size_bytes,
            audio_path=temp_path,
            summary_generator=lambda path, people, language, prompt_template, progress_callback=None: generate_meeting_summary(
                settings,
                path,
                participant_names=people,
                language=language,
                prompt_template=prompt_template,
                progress_callback=progress_callback,
            ),
        )
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass


@app.get("/api/v1/telegram/reminders/due")
def telegram_due_reminders(request: Request) -> dict:
    require_telegram_bot_secret(request)
    return {
        "reminders": app.state.repo.claim_due_telegram_day_reminders(),
        "onlinePrompts": app.state.repo.claim_due_telegram_online_prompts(),
        "breakActivityPrompts": app.state.repo.claim_due_telegram_break_activity_prompts(),
        "meetingAutoAfkNotifications": app.state.repo.claim_due_telegram_meeting_auto_afk_notifications(),
        "meetingSummaryNotifications": app.state.repo.claim_due_telegram_meeting_summary_notifications(),
    }


@app.post("/api/v1/telegram/private-chat")
def telegram_private_chat(chat: TelegramPrivateChatIn, request: Request) -> dict:
    require_telegram_bot_secret(request)
    return app.state.repo.save_telegram_private_chat(chat.telegram_username, chat.chat_id)


@app.post("/api/v1/telegram/reminders/sent")
def telegram_reminder_sent(sent: TelegramReminderSentIn, request: Request) -> dict:
    require_telegram_bot_secret(request)
    if sent.kind == "online_prompt":
        return app.state.repo.mark_telegram_online_prompt_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "break_activity_prompt":
        return app.state.repo.mark_telegram_break_activity_prompt_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "meeting_auto_afk":
        return app.state.repo.mark_telegram_meeting_auto_afk_notification_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "meeting_summary":
        return app.state.repo.mark_telegram_meeting_summary_sent(sent.reminder_id, sent.message_id)

    return app.state.repo.mark_telegram_day_reminder_sent(sent.reminder_id, sent.message_id)


@app.post("/api/v1/telegram/reminders/close")
def telegram_reminder_close(close: TelegramReminderCloseIn, request: Request) -> dict:
    require_telegram_bot_secret(request)
    if close.kind == "online_prompt":
        if close.action not in {"confirm_online", "dismiss"}:
            raise HTTPException(status_code=422, detail="Invalid action for online_prompt")

        return app.state.repo.close_telegram_online_prompt(
            close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
        )

    if close.kind == "break_activity_prompt":
        if close.action not in {"confirm_online", "still_afk"}:
            raise HTTPException(status_code=422, detail="Invalid action for break_activity_prompt")

        return app.state.repo.close_telegram_break_activity_prompt(
            close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
        )

    if close.action not in {"offline", "overtime"}:
        raise HTTPException(status_code=422, detail="Invalid action for day_end")

    return app.state.repo.close_telegram_day_from_reminder(close.reminder_id, close.action, close.timestamp, close.actor_telegram_username)


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


@app.post("/api/v1/calendar/marks/delete")
def delete_calendar_marks(mark: CalendarMarksDeleteIn, _: dict = Depends(require_permission("manageSettings"))) -> dict:
    return app.state.repo.delete_calendar_marks(raw_authors=mark.authors, dates=mark.dates)


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
    view: str = Query(default="activity", pattern="^(authors|activity|alerts|settings)$"),
) -> SummaryResponse:
    include_profiles = view == "settings"
    include_hourly = view == "activity"
    include_breakdowns = view == "activity"

    return SummaryResponse(
        authors=app.state.repo.list_authors(),
        reports=[],
        intervalSettings=app.state.repo.get_interval_settings(),
        discordSettings=app.state.repo.get_discord_settings(),
        activitySummary=app.state.repo.activity_summary(
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
            include_profiles=include_profiles,
            include_hourly=include_hourly,
            include_breakdowns=include_breakdowns,
        ),
    )
