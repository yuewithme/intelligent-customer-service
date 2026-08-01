import hashlib
import re
from typing import Literal

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.services.chat_orchestrator import handle_chat
from app.domains.conversations.services.conversation_service import (
    ensure_outbound_conversation_message,
    update_customer_identity,
    user_has_conversation_in_channels,
)
from app.domains.decisioning.services.reply_builder import build_opening_reply
from app.domains.customers.services.user_profile_service import append_conversation_memory
from app.core.ids import generate_id


DemoChannel = Literal["web_demo", "mcp_demo"]
DEMO_USER_PREFIX = "demo:"
DEMO_CHANNELS = ("web_demo", "mcp_demo", "wechat")


def demo_user_id(customer_id: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_.:@-]+", "_", customer_id.strip())
    digest = hashlib.sha256(customer_id.strip().encode("utf-8")).hexdigest()[:12]
    return f"demo:{value[:96] or 'customer'}:{digest}"


def _runtime_channel(entry_channel: DemoChannel) -> str:
    """The web test entry behaves as another Eyun/WeChat inbound adapter."""
    return "wechat" if entry_channel == "web_demo" else entry_channel


def _entry_metadata(
    *, entry_channel: DemoChannel, customer_id: str, customer_name: str | None
) -> dict:
    identity = {
        "tenant_id": "tenant_default",
        "permission": "public",
        "demo_customer_id": customer_id,
        "display_name": customer_name or customer_id,
    }
    if entry_channel != "web_demo":
        return {**identity, "demo": True, "test_entry": entry_channel}
    return {
        **identity,
        "provider": "eyun",
        "provider_delivery_mode": "simulated",
        "test_entry": entry_channel,
        "message_type": "60001",
        "from_user": demo_user_id(customer_id),
        "from_group": "",
        "suppress_handoff_notification": True,
    }


async def open_demo_sales_conversation(
    *,
    channel: DemoChannel,
    customer_id: str,
    conversation_id: str | None = None,
    customer_name: str | None = None,
) -> dict:
    """Start a demo conversation with the opening used for new WeChat contacts."""
    settings = get_settings()
    user_id = demo_user_id(customer_id)
    runtime_channel = _runtime_channel(channel)
    session_id = conversation_id or (
        "default" if runtime_channel == "wechat" else generate_id("session")
    )
    reply = build_opening_reply()
    identity = _entry_metadata(
        entry_channel=channel,
        customer_id=customer_id,
        customer_name=customer_name,
    )
    outbound_messages = [item.model_dump() for item in reply.outbound_messages]

    for index, item in enumerate(outbound_messages):
        await ensure_outbound_conversation_message(
            channel=runtime_channel,
            user_id=user_id,
            session_id=session_id,
            content=str(item.get("content") or ""),
            message_type=str(item.get("type") or "text"),
            provider_message_id=f"demo-opening:{session_id}:{index}",
            delivery_status="sent",
            route="opening",
            metadata=identity,
        )
    await update_customer_identity(
        channel=runtime_channel,
        user_id=user_id,
        session_id=session_id,
        metadata=identity,
    )
    await append_conversation_memory(
        user_id=user_id,
        tenant_id="tenant_default",
        session_id=session_id,
        role="assistant",
        content=reply.answer,
        intent="opening",
        route="chitchat",
        channel=runtime_channel,
        source_id=f"demo-opening:{session_id}",
    )

    return {
        "reply": reply.answer,
        "answer_segments": [reply.answer] if reply.answer else [],
        "outbound_messages": outbound_messages,
        "opening_image_url": settings.eyun_opening_image_url or None,
        "customer_id": customer_id,
        "conversation_id": session_id,
        "sales_stage": "rapport",
        "customer_tags": [],
        "product_interests": [],
        "next_action": "继续了解客户需求",
        "need_human": False,
        "route": "opening",
        "trace_id": None,
    }


async def chat_with_demo_sales_agent(
    *,
    channel: DemoChannel,
    customer_id: str,
    message: str,
    conversation_id: str | None = None,
    customer_name: str | None = None,
) -> dict:
    runtime_channel = _runtime_channel(channel)
    user_id = demo_user_id(customer_id)
    metadata = _entry_metadata(
        entry_channel=channel,
        customer_id=customer_id,
        customer_name=customer_name,
    )
    if channel == "web_demo" and not await user_has_conversation_in_channels(
        user_id, (runtime_channel,)
    ):
        metadata["prepend_opening"] = True
    result = await handle_chat(
        ChatRequest(
            channel=runtime_channel,
            user_id=user_id,
            session_id=conversation_id,
            message=message,
            kb_id=get_settings().wechat_default_kb_id,
            metadata=metadata,
        )
    )
    intent = result.get("intent") or {}
    metadata = result.get("metadata") or {}
    sales_action = metadata.get("sales_action") or {}
    tag_result = metadata.get("tag_result") or {}
    return {
        "reply": result.get("answer", ""),
        "answer_segments": result.get("answer_segments", []),
        "outbound_messages": result.get("outbound_messages", []),
        "customer_id": customer_id,
        "conversation_id": result.get("session_id"),
        "sales_stage": intent.get("sales_stage") or tag_result.get("stage"),
        "customer_tags": tag_result.get("tags", []),
        "product_interests": tag_result.get("product_interests", []),
        "next_action": result.get("next_action") or sales_action.get("action"),
        "need_human": bool(result.get("need_human")),
        "route": result.get("route"),
        "trace_id": result.get("trace_id"),
        "intent": intent,
        "commerce_action": metadata.get("commerce_action", {}),
    }
