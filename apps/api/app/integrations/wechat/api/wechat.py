import json

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import ChatRequest
from app.integrations.eyun.services.eyun_callback_service import (
    eyun_success,
    handle_eyun_callback,
    should_process_eyun_payload,
)
from app.domains.conversations.services.chat_orchestrator import handle_chat
from app.integrations.wechat.services.wechat_service import (
    build_text_reply,
    message_deduplicator,
    parse_message,
    verify_signature,
)


router = APIRouter(prefix="/wechat", tags=["wechat"])


@router.get("/callback")
async def verify_wechat(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> PlainTextResponse:
    if not verify_signature(
        get_settings().wechat_token, signature, timestamp, nonce
    ):
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(echostr)


@router.post("/callback")
async def receive_wechat(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: str | None = Query(default=None),
    timestamp: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
) -> Response:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type.lower() or not signature:
        payload = await request.json()
        if should_process_eyun_payload(payload):
            background_tasks.add_task(handle_eyun_callback, payload)
        return Response(
            content=json.dumps(eyun_success(), ensure_ascii=False),
            media_type="application/json",
        )

    if not timestamp or not nonce:
        return PlainTextResponse("Forbidden", status_code=403)
    if not verify_signature(
        get_settings().wechat_token, signature, timestamp, nonce
    ):
        return PlainTextResponse("Forbidden", status_code=403)

    message = parse_message(await request.body())
    to_user = message.get("FromUserName", "")
    from_user = message.get("ToUserName", "")
    message_id = message.get("MsgId", "")

    if message_id:
        cached = message_deduplicator.get(message_id)
        if cached:
            return Response(cached, media_type="application/xml")

    if message.get("MsgType") != "text":
        answer = "当前仅支持文本问题。"
    else:
        chat_request = ChatRequest(
            channel="wechat",
            user_id=to_user,
            session_id=None,
            message=message.get("Content", ""),
            kb_id=get_settings().wechat_default_kb_id,
            metadata={
                "wechat_to_user": from_user,
                "wechat_msg_id": message_id,
            },
        )
        result = await handle_chat(chat_request)
        answer = result["answer"]

    reply = build_text_reply(to_user, from_user, answer)
    if message_id:
        message_deduplicator.set(message_id, reply)
    return Response(reply, media_type="application/xml")
