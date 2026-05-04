from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_auth_service
from ..models import SiteUserIn


router = APIRouter()


@router.get("/api/v1/site-users")
def site_users(_: dict = Depends(require_permission("manageUsers")), service: BackendServices = Depends(get_auth_service)) -> dict:
    return {"users": service.site_users()}


@router.put("/api/v1/site-users")
def upsert_site_user(
    user_in: SiteUserIn,
    _: dict = Depends(require_permission("manageUsers")),
    service: BackendServices = Depends(get_auth_service),
) -> dict:
    result = service.upsert_site_user(
        email=user_in.email,
        display_name=user_in.display_name,
        role=user_in.role,
        can_view_server_stats=user_in.can_view_server_stats,
        active=user_in.active,
        password=user_in.password,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "User save failed"))

    return result


@router.delete("/api/v1/site-users/{email}")
def delete_site_user(
    email: str,
    current_user: dict = Depends(require_permission("manageUsers")),
    service: BackendServices = Depends(get_auth_service),
) -> dict:
    if email.strip().lower() == current_user.get("email"):
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    return service.delete_site_user(email)
