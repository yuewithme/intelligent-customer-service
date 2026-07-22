from fastapi import APIRouter, Depends, Query

from app.domains.sales.schemas.activity import (
    ActivityActionRequest,
    ActivityFromMessagesRequest,
    ActivitySendRequest,
    ActivitySwitchesRequest,
    ActivityUpdateRequest,
)
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.activity_service import (
    archive_activity,
    create_activity_from_messages,
    get_activity,
    list_activity_send_logs,
    list_activities,
    publish_activity,
    send_activity,
    update_activity,
    update_activity_switches,
)
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/activities",
    tags=["admin-activities"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def activities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    keyword: str | None = None,
    conversation_id: str | None = None,
) -> APIResponse:
    data = list_activities(
        page=page,
        page_size=page_size,
        status=status,
        keyword=keyword,
        available_only=bool(conversation_id),
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/from-messages", response_model=APIResponse)
async def from_messages(request: ActivityFromMessagesRequest) -> APIResponse:
    data = create_activity_from_messages(request)
    return APIResponse(code=0, message="success", data=data)


@router.get("/{activity_id}", response_model=APIResponse)
async def activity_detail(activity_id: int) -> APIResponse:
    return APIResponse(code=0, message="success", data=get_activity(activity_id))


@router.put("/{activity_id}", response_model=APIResponse)
async def update(activity_id: int, request: ActivityUpdateRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_activity(activity_id, request),
    )


@router.post("/{activity_id}/publish", response_model=APIResponse)
async def publish(activity_id: int, request: ActivityActionRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=publish_activity(activity_id, request.operator_id),
    )


@router.patch("/{activity_id}/switches", response_model=APIResponse)
@router.put("/{activity_id}/switches", response_model=APIResponse)
async def switches(
    activity_id: int, request: ActivitySwitchesRequest
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_activity_switches(activity_id, request),
    )


@router.post("/{activity_id}/archive", response_model=APIResponse)
async def archive(activity_id: int, request: ActivityActionRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=archive_activity(activity_id, request.operator_id),
    )


@router.post("/{activity_id}/send", response_model=APIResponse)
async def send(activity_id: int, request: ActivitySendRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=await send_activity(
            activity_id,
            conversation_id=request.conversation_id,
            operator_id=request.operator_id,
        ),
    )


@router.get("/{activity_id}/send-logs", response_model=APIResponse)
async def send_logs(activity_id: int) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_activity_send_logs(activity_id),
    )
