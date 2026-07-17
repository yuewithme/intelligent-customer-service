import hashlib
import re
from typing import Literal

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.services.channel_service import normalize_chat_request
from app.services.chat_orchestrator import handle_chat
from app.services.conversation_service import (
    record_ai_turn,
    user_has_conversation_in_channels,
)
from app.services.reply_builder import build_opening_reply
from app.services.user_profile_service import append_conversation_memory


DemoChannel = Literal["web_demo", "mcp_demo"]
DEMO_CHANNELS = ("web_demo", "mcp_demo")


def demo_user_id(customer_id: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_.:@-]+", "_", customer_id.strip())
    digest = hashlib.sha256(customer_id.strip().encode("utf-8")).hexdigest()[:12]
    return f"demo:{value[:96] or 'customer'}:{digest}"


async def chat_with_demo_sales_agent(
    *,
    channel: DemoChannel,
    customer_id: str,
    message: str,
    conversation_id: str | None = None,
    customer_name: str | None = None,
) -> dict:
    _reject_corrupted_message(message)
    request = ChatRequest(
        channel=channel,
        user_id=demo_user_id(customer_id),
        session_id=conversation_id,
        message=message,
        kb_id=get_settings().wechat_default_kb_id,
        metadata={
            "tenant_id": "tenant_default",
            "permission": "public",
            "demo": True,
            "demo_customer_id": customer_id,
            "display_name": customer_name or customer_id,
        },
    )
    normalized = await normalize_chat_request(request)
    is_first_inbound = not await user_has_conversation_in_channels(
        normalized.user_id, (channel,)
    )
    if is_first_inbound:
        result = await _handle_opening_message(normalized)
    else:
        result = await handle_chat(
            request.model_copy(update={"session_id": normalized.session_id})
        )
    intent = result.get("intent") or {}
    metadata = result.get("metadata") or {}
    sales_action = metadata.get("sales_action") or {}
    tag_result = metadata.get("tag_result") or {}
    return {
        "reply": result.get("answer", ""),
        "customer_id": customer_id,
        "conversation_id": result.get("session_id"),
        "sales_stage": intent.get("sales_stage") or tag_result.get("stage"),
        "customer_tags": tag_result.get("tags", []),
        "product_interests": tag_result.get("product_interests", []),
        "next_action": result.get("next_action") or sales_action.get("action"),
        "need_human": bool(result.get("need_human")),
        "route": result.get("route"),
        "trace_id": result.get("trace_id"),
    }


def _reject_corrupted_message(message: str) -> None:
    stripped = message.strip()
    replacement_mark_found = "\ufffd" in stripped
    only_question_marks = len(stripped) >= 2 and all(
        char in {"?", "？"} for char in stripped
    )
    if replacement_mark_found or only_question_marks:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="消息疑似发生编码损坏，请使用 UTF-8 编码后重新发送",
            status_code=422,
        )


async def _handle_opening_message(message: NormalizedMessage) -> dict:
    reply = build_opening_reply()
    result = {
        "answer": reply.answer,
        "answer_segments": [reply.answer] if reply.answer else [],
        "outbound_messages": [item.model_dump() for item in reply.outbound_messages],
        "session_id": message.session_id,
        "sources": [],
        "usage": {},
        "reply_type": reply.reply_type,
        "route": "opening",
        "intent": {"primary_intent": "opening"},
        "template": {},
        "need_human": False,
        "next_action": None,
        "trace_id": message.trace_id,
        "metadata": {},
        "handoff": None,
    }
    await append_conversation_memory(
        user_id=message.user_id,
        tenant_id=message.tenant_id,
        session_id=message.session_id,
        role="user",
        content=message.message,
        intent="opening_trigger",
        route="chitchat",
        trace_id=message.trace_id,
    )
    if reply.answer:
        await append_conversation_memory(
            user_id=message.user_id,
            tenant_id=message.tenant_id,
            session_id=message.session_id,
            role="assistant",
            content=reply.answer,
            intent="opening",
            route="chitchat",
            trace_id=message.trace_id,
        )
    await record_ai_turn(message=message, result=result)
    return result
