from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_author_service
from ..models import PublisherProfileIn

router = APIRouter()


@router.get("/api/v1/publisher-profiles")
def publisher_profiles(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"publisherProfiles": service.publisher_profiles(), "deviceProfiles": service.device_profiles()}


@router.put("/api/v1/publisher-profiles/{raw_author}")
def upsert_publisher_profile(
    raw_author: str,
    profile: PublisherProfileIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.upsert_publisher_profile(raw_author, profile.display_name, profile.team, profile.author_color)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher profile save failed"))

    return result


@router.post("/api/v1/publisher-profiles/{raw_author}/avatar")
async def upload_publisher_avatar(
    raw_author: str,
    avatar: UploadFile = File(...),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.save_publisher_avatar(raw_author, await avatar.read(), avatar.content_type or "")

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher avatar upload failed"))

    return result


@router.put("/api/v1/publisher-profiles/{raw_author}/devices/{raw_device}")
def link_publisher_device(
    raw_author: str,
    raw_device: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.link_publisher_device(raw_author, raw_device)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Publisher device link failed"))

    return result


@router.delete("/api/v1/publisher-profiles/{raw_author}/devices/{raw_device}")
def unlink_publisher_device(
    raw_author: str,
    raw_device: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.unlink_publisher_device(raw_author, raw_device)
