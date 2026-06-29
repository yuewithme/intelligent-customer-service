from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat
from app.services.wechat_service import (
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
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> Response:
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
