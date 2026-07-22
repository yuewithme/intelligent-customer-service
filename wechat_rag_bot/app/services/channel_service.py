from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.event import NormalizedMessage
from app.utils.ids import generate_id


async def normalize_chat_request(request: ChatRequest) -> NormalizedMessage:
    metadata = dict(request.metadata or {})
    session_id = request.session_id or metadata.get("session_id")
    if not session_id:
        session_id = "default" if metadata.get("provider") == "eyun" else generate_id("session")
    source_trace_id = (
        str(metadata.get("source_trace_id") or "").strip()
        if metadata.get("provider") == "eyun"
        else ""
    )
    return NormalizedMessage(
        trace_id=source_trace_id or generate_id("request"),
        channel=request.channel,
        user_id=request.user_id,
        session_id=session_id,
        message_id=metadata.get("message_id"),
        message=request.message,
        kb_id=request.kb_id,
        tenant_id=metadata.get("tenant_id", "tenant_default"),
        permission=metadata.get("permission", "public"),
        metadata=metadata,
    )


async def normalize_wechat_message(msg: dict) -> NormalizedMessage:
    settings = get_settings()
    metadata = {
        "wechat_to_user": msg.get("ToUserName", ""),
        "wechat_msg_id": msg.get("MsgId"),
    }
    return NormalizedMessage(
        trace_id=generate_id("request"),
        channel="wechat",
        user_id=msg.get("FromUserName", ""),
        session_id=generate_id("session"),
        message_id=msg.get("MsgId"),
        message=msg.get("Content", ""),
        kb_id=settings.wechat_default_kb_id,
        tenant_id="tenant_default",
        permission="public",
        metadata=metadata,
    )
