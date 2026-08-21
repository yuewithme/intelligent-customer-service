import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.conversations.schemas.conversation import (
    ClaimRequest,
    ReplyCareManualRequest,
    ReplyEmojiRequest,
    ReplyRequest,
    StatusActionRequest,
)
from app.domains.conversations.services.conversation_event_service import conversation_event_broker
from app.domains.conversations.services.conversation_service import (
    claim_conversation,
    force_handoff,
    get_conversation_detail,
    hide_conversation,
    list_conversations,
    list_conversation_tenants,
    mark_conversation_read,
    list_conversation_emojis,
    release_to_ai,
    reply_conversation,
    reply_conversation_care_manual,
    reply_conversation_emoji,
    reply_conversation_image,
    resolve_message_media,
    resolve_conversation,
    unhide_conversation,
)
from app.core.auth import require_admin_access
from app.domains.customers.services.user_profile_service import get_profile_bundle
from app.integrations.youzan.services.youzan_order_sync_service import (
    list_conversation_orders,
)
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.services.workbench_media_service import (
    store_workbench_image,
)
from app.domains.decisioning.services.demo_sales_agent_service import DEMO_USER_PREFIX


router = APIRouter(
    prefix="/api/v1/admin/conversations",
    tags=["admin-conversations"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("/events")
async def conversation_events(request: Request) -> StreamingResponse:
    async def stream():
        queue = conversation_event_broker.subscribe()
        try:
            yield 'data: {"type":"connected"}\n\n'
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    payload = json.dumps(
                        {"type": "conversation.changed", **event},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            conversation_event_broker.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", response_model=APIResponse)
async def conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
    channel: str | None = None,
    test_only: bool = False,
    tenant_id: str | None = None,
) -> APIResponse:
    material_group_wc_id = get_settings().eyun_material_group_wc_id.strip()
    data = await list_conversations(
        page=page,
        page_size=page_size,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
        channels=(channel,) if channel else None,
        excluded_user_id_prefix=None if test_only else DEMO_USER_PREFIX,
        wechat_group_allowlist=(material_group_wc_id,) if material_group_wc_id else (),
        test_data=test_only,
        tenant_id=tenant_id,
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/tenants", response_model=APIResponse)
async def conversation_tenants() -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_conversation_tenants(),
    )


@router.post("/{conversation_id:path}/claim", response_model=APIResponse)
async def claim(conversation_id: str, request: ClaimRequest) -> APIResponse:
    data = await claim_conversation(conversation_id, request.operator_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply", response_model=APIResponse)
async def reply(conversation_id: str, request: ReplyRequest) -> APIResponse:
    data = await reply_conversation(
        conversation_id, request.operator_id, request.content
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-image", response_model=APIResponse)
async def reply_image(
    request: Request,
    conversation_id: str,
    operator_id: str = Form(...),
    file: UploadFile = File(...),
) -> APIResponse:
    image = await store_workbench_image(request, file)
    data = await reply_conversation_image(
        conversation_id,
        operator_id,
        image["url"],
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-care-manual", response_model=APIResponse)
async def reply_care_manual(
    conversation_id: str, request: ReplyCareManualRequest
) -> APIResponse:
    data = await reply_conversation_care_manual(
        conversation_id,
        request.operator_id,
        request.care_manual_id,
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}/emojis", response_model=APIResponse)
async def conversation_emojis(conversation_id: str) -> APIResponse:
    data = list_conversation_emojis(conversation_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-emoji", response_model=APIResponse)
async def reply_emoji(
    conversation_id: str, request: ReplyEmojiRequest
) -> APIResponse:
    data = await reply_conversation_emoji(
        conversation_id,
        request.operator_id,
        request.source_message_id,
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/read", response_model=APIResponse)
async def mark_read(conversation_id: str) -> APIResponse:
    data = await mark_conversation_read(conversation_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/hide", response_model=APIResponse)
async def hide(conversation_id: str) -> APIResponse:
    data = await hide_conversation(conversation_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/unhide", response_model=APIResponse)
async def unhide(conversation_id: str) -> APIResponse:
    data = await unhide_conversation(conversation_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/messages/{message_id}/resolve-media", response_model=APIResponse)
async def resolve_media(message_id: int) -> APIResponse:
    data = await resolve_message_media(message_id)
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}/orders", response_model=APIResponse)
async def conversation_orders(conversation_id: str) -> APIResponse:
    detail = await get_conversation_detail(conversation_id)
    profile = await get_profile_bundle(detail["conversation"]["user_id"])
    basic_info = profile["profile"].get("basic_info") or {}
    try:
        data = list_conversation_orders(
            conversation_id,
            profile_mobile=str(basic_info.get("mobile") or ""),
        )
    except LookupError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message=str(exc),
            status_code=404,
        ) from exc
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/force-handoff", response_model=APIResponse)
async def force_handoff_route(
    conversation_id: str, request: StatusActionRequest
) -> APIResponse:
    data = await force_handoff(conversation_id, request.operator_id, request.reason)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/release-to-ai", response_model=APIResponse)
async def release_to_ai_route(
    conversation_id: str, request: StatusActionRequest
) -> APIResponse:
    data = await release_to_ai(conversation_id, request.operator_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/resolve", response_model=APIResponse)
async def resolve_route(
    conversation_id: str, request: StatusActionRequest
) -> APIResponse:
    data = await resolve_conversation(
        conversation_id, request.operator_id, request.reason
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}", response_model=APIResponse)
async def conversation_detail(conversation_id: str) -> APIResponse:
    data = await get_conversation_detail(conversation_id)
    return APIResponse(code=0, message="success", data=data)
