from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.schemas.event import NormalizedMessage
from app.core.ids import generate_id


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
