import hashlib
import json
import re
from datetime import datetime, timezone
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
from app.domains.conversations.services.state_service import get_user_state
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
        message_type = str(item.get("type") or "text")
        await ensure_outbound_conversation_message(
            channel=runtime_channel,
            user_id=user_id,
            session_id=session_id,
            content=str(item.get("content") or ""),
            message_type=message_type,
            provider_message_id=f"demo-opening:{session_id}:{index}",
            delivery_status="sent",
            route="opening",
            metadata={**identity, "message_type": message_type},
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
    user_id = demo_user_id(customer_id)
    await _flush_due_demo_follow_up(user_id=user_id, session_id=session_id)
    detail = await get_conversation_detail(
        make_conversation_id("wechat", user_id, session_id)
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


async def _flush_due_demo_follow_up(*, user_id: str, session_id: str) -> None:
    user_state = await get_user_state(user_id, session_id)
    pending = user_state.metadata.get("pending_service_follow_up")
    if not isinstance(pending, dict):
        return
    try:
        due_at = datetime.fromisoformat(str(pending.get("due_at") or ""))
    except ValueError:
        user_state.metadata.pop("pending_service_follow_up", None)
        return
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    if due_at > datetime.now(timezone.utc):
        return
    messages = pending.get("messages")
    user_state.metadata.pop("pending_service_follow_up", None)
    if not isinstance(messages, list):
        return
    source_trace_id = str(pending.get("source_trace_id") or "demo-follow-up")
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "").strip()
        content = str(item.get("content") or "").strip()
        if not message_type or not content:
            continue
        await ensure_outbound_conversation_message(
            channel="wechat",
            user_id=user_id,
            session_id=session_id,
            content=content,
            message_type=message_type,
            provider_message_id=f"demo-service-follow-up:{source_trace_id}:{index}",
            delivery_status="sent",
            route="service_silence_follow_up",
            metadata={"message_type": message_type, "origin": "service_follow_up"},
        )
    product_id = str(pending.get("product_id") or "").strip()
    if product_id:
        sent_ids = user_state.metadata.get("commerce_sent_card_ids")
        sent_ids = list(sent_ids) if isinstance(sent_ids, list) else []
        if product_id not in sent_ids:
            sent_ids.append(product_id)
        user_state.metadata["commerce_sent_card_ids"] = sent_ids[-20:]
    opportunity = user_state.metadata.get("active_opportunity")
    if isinstance(opportunity, dict):
        opportunity["service_offer_phase"] = "card_sent"


def _demo_history_messages(messages: list[dict]) -> list[dict]:
    history: list[dict] = []
    for message in messages:
        metadata = message.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        sender_type = str(message.get("sender_type") or "")
        role = "customer" if sender_type == "customer" else "agent"
        message_type = _demo_message_type(metadata)
        content = str(message.get("content") or "")
        if role == "agent":
            content = str(metadata.get("outbound_content") or content)
        card = _demo_card_metadata(metadata, message_type)
        media = metadata.get("media")
        media = media if isinstance(media, dict) else None
        if card is not None:
            content = json.dumps(card, ensure_ascii=False)
        elif media is not None:
            source = str(media.get("url") or "").strip()
            if not source and media.get("type") == "image" and media.get("thumb_base64"):
                source = f"data:image/jpeg;base64,{media['thumb_base64']}"
            if source:
                content = source
        history.append(
            {
                "id": int(message.get("id") or 0),
                "role": role,
                "type": message_type,
                "content": content,
                "media": media,
                "card": card,
            }
        )
    return history


def _demo_message_type(metadata: dict) -> str:
    if isinstance(metadata.get("link_card"), dict):
        return "link_card"
    if isinstance(metadata.get("mini_program"), dict):
        return "mini_program"
    media = metadata.get("media")
    if isinstance(media, dict):
        media_type = str(media.get("type") or "").strip()
        if media_type:
            return media_type
    message_type = str(metadata.get("message_type") or "text").strip()
    return message_type if message_type in {
        "text",
        "image",
        "video",
        "audio",
        "emoji",
        "file",
        "material",
        "received_image",
        "received_video",
    } else "text"


def _demo_card_metadata(metadata: dict, message_type: str) -> dict | None:
    key = "mini_program" if message_type == "mini_program" else "link_card"
    value = metadata.get(key)
    return value if isinstance(value, dict) else None


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
        "next_action": result.get("next_action") or sales_action.get("sales_action"),
        "need_human": bool(result.get("need_human")),
        "route": result.get("route"),
        "trace_id": result.get("trace_id"),
        "intent": intent,
        "commerce_action": metadata.get("commerce_action", {}),
    }
