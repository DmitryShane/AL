from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api_security import require_permission
from ..container import BackendServices
from ..dependencies import get_author_service
from ..models import AuthorAliasIn, AuthorProfileIn


router = APIRouter()


@router.put("/api/v1/authors/profile")
def upsert_author_profile(
    profile: AuthorProfileIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.upsert_author_profile(
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


@router.get("/api/v1/authors/aliases")
def author_aliases(
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return {"aliases": service.author_aliases()}


@router.put("/api/v1/authors/aliases")
def upsert_author_alias(
    alias: AuthorAliasIn,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    result = service.upsert_author_alias(
        source_raw_author=alias.source_raw_author,
        target_raw_author=alias.target_raw_author,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Alias save failed"))

    return result


@router.delete("/api/v1/authors/aliases/{source_raw_author}")
def delete_author_alias(
    source_raw_author: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.delete_author_alias(source_raw_author=source_raw_author)


@router.delete("/api/v1/authors/{raw_author}/data")
def delete_author_data(
    raw_author: str,
    start_date: str | None = Query(None, alias="startDate"),
    end_date: str | None = Query(None, alias="endDate"),
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    if start_date is None and end_date is None:
        return service.delete_author_data(raw_author=raw_author)

    if start_date is None or end_date is None:
        raise HTTPException(status_code=422, detail="Both startDate and endDate are required for ranged delete")

    result = service.delete_author_data_for_date_range(raw_author=raw_author, start_date=start_date, end_date=end_date)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "Ranged delete failed"))

    return result


@router.delete("/api/v1/authors/{raw_author}/profile")
def delete_author_profile(
    raw_author: str,
    _: dict = Depends(require_permission("manageSettings")),
    service: BackendServices = Depends(get_author_service),
) -> dict:
    return service.delete_author_profile(raw_author=raw_author)
