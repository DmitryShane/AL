from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..api_security import require_telegram_bot_secret
from ..container import BackendServices
from ..dependencies import get_telegram_service
from ..models import BreakEventIn, TelegramPrivateChatIn, TelegramReminderCloseIn, TelegramReminderSentIn


router = APIRouter()


@router.post("/api/v1/break-events")
def record_break_event(event: BreakEventIn, service: BackendServices = Depends(get_telegram_service)) -> dict:
    return service.record_break_event(
        telegram_username=event.telegram_username,
        event_type=event.event_type,
        timestamp=event.timestamp,
    )


@router.get("/api/v1/telegram/reminders/due")
def telegram_due_reminders(request: Request, service: BackendServices = Depends(get_telegram_service)) -> dict:
    require_telegram_bot_secret(request)
    return {
        "reminders": service.claim_due_telegram_day_reminders(),
        "onlinePrompts": service.claim_due_telegram_online_prompts(),
        "breakActivityPrompts": service.claim_due_telegram_break_activity_prompts(),
        "meetingAutoAfkNotifications": service.claim_due_telegram_meeting_auto_afk_notifications(),
        "meetingRecordingNotifications": service.claim_due_telegram_meeting_recording_notifications(),
        "meetingSummaryNotifications": service.claim_due_telegram_meeting_summary_notifications(),
    }


@router.post("/api/v1/telegram/private-chat")
def telegram_private_chat(
    chat: TelegramPrivateChatIn,
    request: Request,
    service: BackendServices = Depends(get_telegram_service),
) -> dict:
    require_telegram_bot_secret(request)
    return service.save_telegram_private_chat(chat.telegram_username, chat.chat_id)


@router.post("/api/v1/telegram/reminders/sent")
def telegram_reminder_sent(
    sent: TelegramReminderSentIn,
    request: Request,
    service: BackendServices = Depends(get_telegram_service),
) -> dict:
    require_telegram_bot_secret(request)
    if sent.kind == "online_prompt":
        return service.mark_telegram_online_prompt_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "break_activity_prompt":
        return service.mark_telegram_break_activity_prompt_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "duplicate_afk_prompt":
        return service.mark_telegram_duplicate_afk_prompt_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "meeting_auto_afk":
        return service.mark_telegram_meeting_auto_afk_notification_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "meeting_recording":
        return service.mark_telegram_meeting_recording_notification_sent(sent.reminder_id, sent.message_id)

    if sent.kind == "meeting_summary":
        return service.mark_telegram_meeting_summary_sent(sent.reminder_id, sent.message_id)

    return service.mark_telegram_day_reminder_sent(sent.reminder_id, sent.message_id)


@router.post("/api/v1/telegram/reminders/close")
def telegram_reminder_close(
    close: TelegramReminderCloseIn,
    request: Request,
    service: BackendServices = Depends(get_telegram_service),
) -> dict:
    require_telegram_bot_secret(request)
    if close.kind == "online_prompt":
        if close.action not in {"confirm_online", "dismiss"}:
            raise HTTPException(status_code=422, detail="Invalid action for online_prompt")

        return service.close_telegram_online_prompt(
            close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
        )

    if close.kind == "break_activity_prompt":
        if close.action not in {"confirm_online", "still_afk"}:
            raise HTTPException(status_code=422, detail="Invalid action for break_activity_prompt")

        return service.close_telegram_break_activity_prompt(
            close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
        )

    if close.kind == "duplicate_afk_prompt":
        if close.action not in {"confirm_online", "still_afk"}:
            raise HTTPException(status_code=422, detail="Invalid action for duplicate_afk_prompt")

        return service.close_telegram_duplicate_afk_prompt(
            close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
        )

    if close.action not in {"offline", "overtime"}:
        raise HTTPException(status_code=422, detail="Invalid action for day_end")

    return service.close_telegram_day_from_reminder(
        close.reminder_id, close.action, close.timestamp, close.actor_telegram_username
    )
