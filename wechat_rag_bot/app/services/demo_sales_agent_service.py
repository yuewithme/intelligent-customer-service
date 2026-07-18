import hashlib
import re
from typing import Literal

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat
from app.services.conversation_service import (
    ensure_outbound_conversation_message,
    update_customer_identity,
)
from app.services.reply_builder import build_opening_reply
from app.services.user_profile_service import append_conversation_memory
from app.utils.ids import generate_id


DemoChannel = Literal["web_demo", "mcp_demo"]
DEMO_CHANNELS = ("web_demo", "mcp_demo")


def demo_user_id(customer_id: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_.:@-]+", "_", customer_id.strip())
    digest = hashlib.sha256(customer_id.strip().encode("utf-8")).hexdigest()[:12]
    return f"demo:{value[:96] or 'customer'}:{digest}"


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
    session_id = conversation_id or generate_id("session")
    reply = build_opening_reply()
    identity = {
        "display_name": customer_name or customer_id,
        "demo": True,
        "demo_customer_id": customer_id,
    }

    await ensure_outbound_conversation_message(
        channel=channel,
        user_id=user_id,
        session_id=session_id,
        content=reply.answer,
        provider_message_id=f"demo-opening:{session_id}",
        delivery_status="sent",
        route="opening",
        metadata=identity,
    )
    await update_customer_identity(
        channel=channel,
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
    )

    return {
        "reply": reply.answer,
        "opening_image_url": settings.eyun_opening_image_url or None,
        "customer_id": customer_id,
        "conversation_id": session_id,
        "sales_stage": "greeting",
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
    result = await handle_chat(
        ChatRequest(
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
