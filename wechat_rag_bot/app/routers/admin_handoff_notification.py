from fastapi import APIRouter, Depends, Query

from app.schemas.chat import APIResponse
from app.schemas.handoff_notification import (
    HandoffNotificationSettingsUpdateRequest,
)
from app.services.handoff_notification_service import (
    get_handoff_notification_settings,
    list_handoff_notification_contacts,
    update_handoff_notification_settings,
)
from app.services.unpurchased_sop_service import sync_eyun_contacts
from app.utils.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/handoff-notification",
    tags=["handoff-notification"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def get_settings() -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=get_handoff_notification_settings(),
    )


@router.put("", response_model=APIResponse)
async def update_settings(
    request: HandoffNotificationSettingsUpdateRequest,
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_handoff_notification_settings(request),
    )


@router.get("/contacts", response_model=APIResponse)
async def contacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    keyword: str = Query(default="", max_length=256),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_handoff_notification_contacts(
            page=page,
            page_size=page_size,
            keyword=keyword,
        ),
    )


@router.post("/contacts/sync", response_model=APIResponse)
async def sync_contacts() -> APIResponse:
    return APIResponse(code=0, message="success", data=await sync_eyun_contacts())
