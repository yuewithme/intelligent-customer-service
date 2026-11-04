import asyncio
import json
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.domains.conversations.schemas.chat import APIResponse
from app.domains.conversations.schemas.conversation import (
    ClaimRequest,
    ConversationListActionRequest,
    ConversationRemarkRequest,
    ReplyCareManualRequest,
    ReplyEmojiRequest,
    ReplyRequest,
    ReplyProductRequest,
    ForwardMessageRequest,
    MessagePreferenceRequest,
    MessageTextRequest,
    ReplyDraftRequest,
    StatusActionRequest,
)
from app.domains.conversations.services.conversation_event_service import conversation_event_broker
from app.domains.conversations.services.chat_review_service import get_chat_review
from app.domains.conversations.services.message_action_service import (
    forward_conversation_message, revoke_conversation_message, reply_conversation_product,
    update_message_preference, message_text_action, list_favorite_messages,
)
from app.domains.conversations.services.reply_draft_service import update_reply_draft
from app.domains.conversations.services.conversation_service import (
    apply_conversation_list_action,
    claim_conversation,
    force_handoff,
    get_conversation_detail,
    get_message_recognition_stats,
    get_message_media_proxy_source,
    hide_conversation,
    list_conversations,
    list_conversation_tenants,
    mark_conversation_read,
    list_conversation_emojis,
    release_to_ai,
    retry_conversation_message_delivery,
    reply_conversation,
    reply_conversation_care_manual,
    reply_conversation_emoji,
    reply_conversation_image,
    reply_conversation_video,
    resolve_message_media,
    resolve_conversation,
    set_conversation_remark,
    unhide_conversation,
)
from app.core.auth import require_admin_access, get_account, operator_identity
from app.domains.access.platform import COOKIE_NAME, stream_account
from app.domains.access.permissions import can_access_conversation
from app.domains.customers.services.user_profile_service import get_profile_bundle
from app.integrations.youzan.services.youzan_order_sync_service import (
    list_conversation_orders,
)
from app.domains.sales.services.service_material_touch_service import (
    get_service_material_touch_delivery_stats,
)
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.services.workbench_media_service import (
    store_workbench_image,
    store_workbench_video,
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
                    account = await stream_account(request)
                    if COOKIE_NAME in request.cookies and (not account or "/workbench" not in account["pages"] or account["role"] == "test"):
                        break
                    if account and account["role"] != "admin" and not can_access_conversation(account, event.get("conversation_id", "")):
                        continue
                    payload = json.dumps(
                        {"type": "conversation.changed", **event},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    account = await stream_account(request)
                    if COOKIE_NAME in request.cookies and (not account or "/workbench" not in account["pages"] or account["role"] == "test"):
                        break
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
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
    channel: str | None = None,
    test_only: bool = False,
    tenant_id: str | None = None,
    for_forwarding: bool = False,
    owner_wc_id: str | None = None,
) -> APIResponse:
    data = await list_conversations(
        page=page,
        page_size=page_size,
        status=status,
        owner_id=owner_id,
        keyword=keyword,
        channels=(channel,) if channel else None,
        excluded_user_id_prefix=None if test_only else DEMO_USER_PREFIX,
        test_data=test_only,
        tenant_id=tenant_id,
        for_forwarding=for_forwarding,
        owner_wc_id=owner_wc_id,
        allowed_owner_wc_ids=(get_account(request)["wechat_ids"] if get_account(request) and get_account(request)["role"] == "employee" and not get_account(request)["all_wechat"] else None),
        wechat_only=bool(get_account(request) and get_account(request)["role"] == "employee"),
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/tenants", response_model=APIResponse)
async def conversation_tenants(request: Request) -> APIResponse:
    data = await list_conversation_tenants()
    account = get_account(request)
    if account and account["role"] == "employee" and not account["all_wechat"]:
        data["items"] = [item for item in data["items"] if item["wc_id"] in account["wechat_ids"]]
    return APIResponse(
        code=0,
        message="success",
        data=data,
    )


@router.get("/message-recognition-stats", response_model=APIResponse)
async def message_recognition_stats(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=await get_message_recognition_stats(
            start_time=start_time,
            end_time=end_time,
        ),
    )


@router.get("/messages/{message_id}/mini-program-thumbnail")
async def mini_program_thumbnail(message_id: int) -> Response:
    from app.domains.conversations.services.conversation_service import _get_session, _load_metadata
    from app.infrastructure.database.models import ConversationMessageModel
    from app.integrations.eyun.services.eyun_callback_service import download_eyun_mini_program_thumbnail

    with _get_session() as session:
        message = session.get(ConversationMessageModel, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        metadata = _load_metadata(message.metadata_json)
    if metadata.get("provider") != "eyun" or metadata.get("revoked_at"):
        raise HTTPException(status_code=404, detail="mini-program thumbnail not found")
    try:
        image = await download_eyun_mini_program_thumbnail(
            w_id=str(metadata.get("w_id") or ""), content=str(metadata.get("raw_content") or ""),
        )
    except Exception:
        raise HTTPException(status_code=502, detail="mini-program thumbnail unavailable")
    return Response(
        image, media_type="image/png" if image.startswith(b"\x89PNG") else "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/message-media/{message_id}")
async def message_media(message_id: int, request: Request) -> Response:
    source = get_message_media_proxy_source(message_id)
    if not source:
        raise HTTPException(status_code=404, detail="message media not found")
    from app.integrations.eyun.services.eyun_video_file_service import local_media_path
    from app.integrations.web.services.link_card_thumbnail_service import _assert_public_url

    local_path = local_media_path(source)
    if local_path is not None:
        return FileResponse(local_path, headers={"Cache-Control": "private, max-age=300"})
    client = httpx.AsyncClient(timeout=60, follow_redirects=False)
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        for _ in range(6):
            # Check every redirect before connecting, not after fetching a private target.
            await _assert_public_url(source)
            response = await client.send(client.build_request("GET", source, headers=headers), stream=True)
            if not response.is_redirect:
                break
            target = response.headers.get("location")
            await response.aclose()
            if not target:
                raise ValueError("missing redirect target")
            source = str(response.url.join(target))
        else:
            raise ValueError("too many redirects")
        response.raise_for_status()
    except Exception:
        if "response" in locals():
            await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="message media unavailable")

    async def body():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    passthrough = {
        key: value
        for key in ("content-type", "content-length", "content-range", "accept-ranges")
        if (value := response.headers.get(key))
    }
    passthrough["Cache-Control"] = "private, max-age=300"
    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers=passthrough,
    )


@router.get("/touch-delivery-stats", response_model=APIResponse)
async def touch_delivery_stats(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    item_limit: int = Query(default=100, ge=1, le=500),
) -> APIResponse:
    try:
        data = get_service_material_touch_delivery_stats(
            start_time=start_time,
            end_time=end_time,
            item_limit=item_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=data)


@router.get("/products", response_model=APIResponse)
async def reply_products(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
) -> APIResponse:
    from app.integrations.youzan.services.youzan_product_sync_service import list_products

    result = list_products(page=page, page_size=page_size, keyword=keyword, catalog_only=True)
    fields = ("item_id", "title", "image_url", "price_cent", "stock", "h5_url", "page_url")
    return APIResponse(code=0, message="success", data={
        "items": [{key: item.get(key) for key in fields} for item in result["items"]],
        "total": result["total"], "page": page, "page_size": page_size,
    })


@router.post("/messages/{message_id}/revoke", response_model=APIResponse)
async def revoke_message(message_id: int, request: ClaimRequest, http_request: Request) -> APIResponse:
    return APIResponse(code=0, message="success", data=await revoke_conversation_message(
        message_id, operator_identity(http_request, request.operator_id),
    ))


@router.post("/messages/{message_id}/preference", response_model=APIResponse)
async def message_preference(message_id: int, request: MessagePreferenceRequest, http_request: Request) -> APIResponse:
    return APIResponse(code=0, message="success", data=update_message_preference(
        message_id, operator_identity(http_request, request.operator_id), request.action,
    ))


@router.post("/messages/{message_id}/text", response_model=APIResponse)
async def message_text(message_id: int, request: MessageTextRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=await message_text_action(message_id, request.action))


@router.post("/{conversation_id:path}/forward", response_model=APIResponse)
async def forward_message(conversation_id: str, request: ForwardMessageRequest, http_request: Request) -> APIResponse:
    return APIResponse(code=0, message="success", data=await forward_conversation_message(
        conversation_id, operator_identity(http_request, request.operator_id), request.source_message_id,
    ))


@router.get("/{conversation_id:path}/favorites", response_model=APIResponse)
async def favorite_messages(
    conversation_id: str, request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> APIResponse:
    data = list_favorite_messages(
        conversation_id, operator_identity(request, "admin"), page=page, page_size=page_size,
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-product", response_model=APIResponse)
async def reply_product(conversation_id: str, request: ReplyProductRequest, http_request: Request) -> APIResponse:
    return APIResponse(code=0, message="success", data=await reply_conversation_product(
        conversation_id, operator_identity(http_request, request.operator_id), request.item_id,
    ))


@router.post("/messages/{message_id}/retry-delivery", response_model=APIResponse)
async def retry_message_delivery(message_id: int) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=retry_conversation_message_delivery(message_id),
    )


@router.post("/{conversation_id:path}/chat-review", response_model=APIResponse)
async def chat_review(conversation_id: str) -> APIResponse:
    return APIResponse(code=0, message="success", data=await get_chat_review(conversation_id))


@router.post("/{conversation_id:path}/claim", response_model=APIResponse)
async def claim(conversation_id: str, request: ClaimRequest, http_request: Request) -> APIResponse:
    data = await claim_conversation(conversation_id, operator_identity(http_request, request.operator_id))
    return APIResponse(code=0, message="success", data=data)


@router.put("/{conversation_id:path}/remark", response_model=APIResponse)
async def update_remark(conversation_id: str, request: ConversationRemarkRequest) -> APIResponse:
    data = await set_conversation_remark(conversation_id, request.remark)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-draft", response_model=APIResponse)
async def reply_draft(conversation_id: str, request: ReplyDraftRequest, http_request: Request) -> APIResponse:
    data = await update_reply_draft(
        conversation_id, operator_identity(http_request, request.operator_id), request.editor_id,
        active=request.active, remaining_seconds=request.remaining_seconds,
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply", response_model=APIResponse)
async def reply(conversation_id: str, request: ReplyRequest, http_request: Request) -> APIResponse:
    data = await reply_conversation(
        conversation_id, operator_identity(http_request, request.operator_id), request.content,
        source_message_id=request.source_message_id,
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
        operator_identity(request, operator_id),
        image["url"],
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-video", response_model=APIResponse)
async def reply_video(
    request: Request,
    conversation_id: str,
    operator_id: str = Form(...),
    file: UploadFile = File(...),
) -> APIResponse:
    video = await store_workbench_video(request, file)
    data = await reply_conversation_video(
        conversation_id, operator_identity(request, operator_id),
        video["url"], video["thumb_url"],
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-care-manual", response_model=APIResponse)
async def reply_care_manual(
    conversation_id: str, request: ReplyCareManualRequest, http_request: Request
) -> APIResponse:
    data = await reply_conversation_care_manual(
        conversation_id,
        operator_identity(http_request, request.operator_id),
        request.care_manual_id,
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}/emojis", response_model=APIResponse)
async def conversation_emojis(conversation_id: str) -> APIResponse:
    data = list_conversation_emojis(conversation_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply-emoji", response_model=APIResponse)
async def reply_emoji(
    conversation_id: str, request: ReplyEmojiRequest, http_request: Request
) -> APIResponse:
    data = await reply_conversation_emoji(
        conversation_id,
        operator_identity(http_request, request.operator_id),
        request.source_message_id,
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/read", response_model=APIResponse)
async def mark_read(conversation_id: str, automatic: bool = False) -> APIResponse:
    data = await mark_conversation_read(conversation_id, automatic=automatic)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/list-action", response_model=APIResponse)
async def list_action(
    conversation_id: str, request: ConversationListActionRequest
) -> APIResponse:
    data = await apply_conversation_list_action(conversation_id, request.action)
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


@router.post("/messages/{message_id}/transcribe", response_model=APIResponse)
async def transcribe_message(message_id: int) -> APIResponse:
    data = await resolve_message_media(message_id, transcribe_only=True)
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
    conversation_id: str, request: StatusActionRequest, http_request: Request
) -> APIResponse:
    data = await force_handoff(conversation_id, operator_identity(http_request, request.operator_id), request.reason)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/release-to-ai", response_model=APIResponse)
async def release_to_ai_route(
    conversation_id: str, request: StatusActionRequest, http_request: Request
) -> APIResponse:
    data = await release_to_ai(conversation_id, operator_identity(http_request, request.operator_id))
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/resolve", response_model=APIResponse)
async def resolve_route(
    conversation_id: str, request: StatusActionRequest, http_request: Request
) -> APIResponse:
    data = await resolve_conversation(
        conversation_id, operator_identity(http_request, request.operator_id), request.reason
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}", response_model=APIResponse)
async def conversation_detail(
    conversation_id: str, request: Request,
    message_limit: int | None = Query(default=None, ge=1, le=200),
) -> APIResponse:
    data = await get_conversation_detail(
        conversation_id, operator_id=operator_identity(request, "admin"),
        message_limit=message_limit,
    )
    return APIResponse(code=0, message="success", data=data)
