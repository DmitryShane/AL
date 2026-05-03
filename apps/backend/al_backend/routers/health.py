from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import BackendServices
from ..dependencies import get_backend_services
from ..models import HealthResponse


router = APIRouter()


@router.get("/api/v1/health", response_model=HealthResponse)
def health(service: BackendServices = Depends(get_backend_services)) -> HealthResponse:
    try:
        mongo = service.ping()
    except Exception:
        mongo = False

    return HealthResponse(ok=mongo, mongo=mongo)
