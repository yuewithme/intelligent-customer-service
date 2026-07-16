import hashlib
import re
from typing import Literal

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat


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
