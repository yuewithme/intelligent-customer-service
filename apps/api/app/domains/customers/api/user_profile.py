from fastapi import APIRouter, Depends, Query

from app.domains.conversations.schemas.chat import APIResponse
from app.domains.customers.services.user_profile_service import (
    get_profile_bundle,
    get_profile_events,
    get_recent_memories,
    patch_user_profile,
)
from app.core.auth import require_admin_access


router = APIRouter(
    prefix="/api/v1/users",
    tags=["user-profile"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("/{user_id}/profile", response_model=APIResponse)
async def get_profile_api(user_id: str) -> APIResponse:
    data = await get_profile_bundle(user_id)
    return APIResponse(code=0, message="success", data=data)


@router.patch("/{user_id}/profile", response_model=APIResponse)
async def patch_profile_api(user_id: str, updates: dict) -> APIResponse:
    profile = await patch_user_profile(user_id, updates)
    return APIResponse(code=0, message="success", data={"profile": profile})


@router.get("/{user_id}/memories", response_model=APIResponse)
async def get_memories_api(
    user_id: str,
    limit: int = Query(default=10),
) -> APIResponse:
    data = await get_recent_memories(user_id, limit)
    return APIResponse(code=0, message="success", data=data)


@router.get("/{user_id}/profile/events", response_model=APIResponse)
async def get_profile_events_api(
    user_id: str,
    limit: int = Query(default=20),
) -> APIResponse:
    data = await get_profile_events(user_id, limit)
    return APIResponse(code=0, message="success", data=data)
