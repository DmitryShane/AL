from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..container import BackendServices
from ..dependencies import get_summary_service


router = APIRouter()


@router.get("/api/v1/analytics/summary")
def analytics_summary(
    period: str = Query(default="7d", pattern="^(7d|30d|90d|year)$"),
    service: BackendServices = Depends(get_summary_service),
) -> dict:
    return service.analytics_summary(period=period)
