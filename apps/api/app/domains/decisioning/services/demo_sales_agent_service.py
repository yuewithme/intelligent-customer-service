import hashlib
import re
from typing import Literal

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.services.chat_orchestrator import handle_chat
from app.domains.conversations.services.conversation_service import (
    ensure_outbound_conversation_message,
    get_conversation_detail,
    get_demo_platform_state,
    make_conversation_id,
    set_demo_platform_state,
    update_customer_identity,
    user_has_conversation_in_channels,
)
from app.domains.decisioning.services.reply_builder import build_opening_reply
from app.domains.customers.services.user_profile_service import append_conversation_memory
from app.core.ids import generate_id
from app.shared.schemas.common import AppError, ErrorCode


DemoChannel = Literal["web_demo", "mcp_demo"]
DEMO_USER_PREFIX = "demo:"
DEMO_CHANNELS = ("web_demo", "mcp_demo", "wechat")
DEMO_PLATFORM_STATE_KEY = "web_demo_active"
DEFAULT_DEMO_CUSTOMER_ID = "shared-test-customer"
DEFAULT_DEMO_CUSTOMER_NAME = "共享测试客户"


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
        source_id=f"demo-opening:{user_id}:{session_id}",
    )
    if channel == "web_demo":
        set_demo_platform_state(
            DEMO_PLATFORM_STATE_KEY,
            customer_id=customer_id,
            customer_name=customer_name or customer_id,
            session_id=session_id,
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


async def get_active_demo_sales_conversation() -> dict:
    state = get_demo_platform_state(DEMO_PLATFORM_STATE_KEY)
    if state is None:
        await open_demo_sales_conversation(
            channel="web_demo",
            customer_id=DEFAULT_DEMO_CUSTOMER_ID,
            customer_name=DEFAULT_DEMO_CUSTOMER_NAME,
        )
        state = get_demo_platform_state(DEMO_PLATFORM_STATE_KEY)
    if state is None:
        raise RuntimeError("failed to initialize shared demo conversation")

    customer_id = str(state["customer_id"] or DEFAULT_DEMO_CUSTOMER_ID)
    session_id = str(state["session_id"] or "default")
    detail = await get_conversation_detail(
        make_conversation_id("wechat", demo_user_id(customer_id), session_id)
    )
    return {
        "customer_id": customer_id,
        "customer_name": str(
            detail.get("conversation", {}).get("user_display_name")
            or state.get("customer_name")
            or customer_id
        ),
        "conversation_id": session_id,
        "messages": _demo_history_messages(detail.get("messages") or []),
    }


def _demo_history_messages(messages: list[dict]) -> list[dict]:
    history: list[dict] = []
    for message in messages:
        metadata = message.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        sender_type = str(message.get("sender_type") or "")
        role = "customer" if sender_type == "customer" else "agent"
        message_type = "text"
        content = str(message.get("content") or "")
        if role == "agent":
            message_type = str(metadata.get("message_type") or "text")
            content = str(metadata.get("outbound_content") or content)
        history.append(
            {
                "id": int(message.get("id") or 0),
                "role": role,
                "type": message_type,
                "content": content,
            }
        )
    return history


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
    active = None
    if channel == "web_demo":
        active = get_demo_platform_state(DEMO_PLATFORM_STATE_KEY)
        requested_session_id = conversation_id or "default"
        if active is not None and (
            active.get("customer_id") != customer_id
            or active.get("session_id") != requested_session_id
        ):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                "当前测试会话已在其他端切换，请刷新后继续",
                status_code=409,
            )
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
    if (
        channel == "web_demo"
        and active is None
        and get_demo_platform_state(DEMO_PLATFORM_STATE_KEY) is None
    ):
        set_demo_platform_state(
            DEMO_PLATFORM_STATE_KEY,
            customer_id=customer_id,
            customer_name=customer_name or customer_id,
            session_id=str(result.get("session_id") or "default"),
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
