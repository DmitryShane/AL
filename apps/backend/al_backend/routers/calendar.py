from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_calendar_service
from ..models import CalendarMarkIn, CalendarMarksDeleteIn, CalendarReasonIn


router = APIRouter()


@router.get("/api/v1/calendar/summary")
def calendar_summary(year: int = Query(ge=2000, le=2100), service: BackendServices = Depends(get_calendar_service)) -> dict:
    return service.calendar_summary(year=year)


@router.put("/api/v1/calendar/marks")
def upsert_calendar_marks(
    mark: CalendarMarkIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_calendar_service),
) -> dict:
    return service.upsert_calendar_marks(
        authors=mark.authors,
        dates=mark.dates,
        reason_id=mark.reason_id,
        note=mark.note,
    )


@router.delete("/api/v1/calendar/marks")
def delete_calendar_mark(
    author: str = Query(min_length=1),
    date: str = Query(min_length=1),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_calendar_service),
) -> dict:
    return service.delete_calendar_mark(raw_author=author, date=date)


@router.post("/api/v1/calendar/marks/delete")
def delete_calendar_marks(
    mark: CalendarMarksDeleteIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_calendar_service),
) -> dict:
    return service.delete_calendar_marks(raw_authors=mark.authors, dates=mark.dates)


@router.put("/api/v1/calendar/reasons")
def upsert_calendar_reason(
    reason: CalendarReasonIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_calendar_service),
) -> dict:
    return service.upsert_calendar_reason(reason_id=reason.id or reason.label, label=reason.label)


@router.delete("/api/v1/calendar/reasons/{reason_id}")
def delete_calendar_reason(
    reason_id: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_calendar_service),
) -> dict:
    return service.delete_calendar_reason(reason_id=reason_id)
