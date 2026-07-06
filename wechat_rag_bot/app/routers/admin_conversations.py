import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas.chat import APIResponse
from app.schemas.conversation import ClaimRequest, ReplyRequest, StatusActionRequest
from app.services.conversation_event_service import conversation_event_broker
from app.services.conversation_service import (
    claim_conversation,
    force_handoff,
    get_conversation_detail,
    list_conversations,
    mark_conversation_read,
    release_to_ai,
    reply_conversation,
    resolve_conversation,
)
from app.utils.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/conversations",
    tags=["admin-conversations"],
    dependencies=[Depends(require_api_key)],
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
) -> APIResponse:
    data = await list_conversations(
        page=page,
        page_size=page_size,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
    )
    return APIResponse(code=0, message="success", data=data)


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


@router.post("/{conversation_id:path}/read", response_model=APIResponse)
async def mark_read(conversation_id: str) -> APIResponse:
    data = await mark_conversation_read(conversation_id)
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
