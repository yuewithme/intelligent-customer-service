import logging
from typing import Any

import httpx

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat


logger = logging.getLogger("wechat_rag_bot.eyun_callback")

EYUN_TEST_CALLBACK = "00000"
EYUN_PRIVATE_TEXT = "60001"
EYUN_GROUP_TEXT = "80001"


def is_eyun_text_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) in {EYUN_PRIVATE_TEXT, EYUN_GROUP_TEXT}


def eyun_success() -> dict[str, Any]:
    return {"code": "1000", "message": "success", "data": None}


def should_process_eyun_payload(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return (
        str(payload.get("messageType", "")) != EYUN_TEST_CALLBACK
        and not data.get("self")
        and is_eyun_text_message(payload)
        and bool(str(data.get("content") or "").strip())
    )


async def handle_eyun_callback(payload: dict[str, Any]) -> dict[str, Any]:
    message_type = str(payload.get("messageType", ""))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if message_type == EYUN_TEST_CALLBACK:
        return eyun_success()
    if data.get("self"):
        return eyun_success()
    if not is_eyun_text_message(payload):
        return eyun_success()

    content = str(data.get("content") or "").strip()
    if not content:
        return eyun_success()

    from_group = str(data.get("fromGroup") or "")
    from_user = str(data.get("fromUser") or "")
    target_wc_id = from_group or from_user
    if not target_wc_id:
        return eyun_success()

    chat_result = await handle_chat(
        ChatRequest(
            channel="wechat",
            user_id=from_user or target_wc_id,
            session_id=from_group or None,
            message=content,
            kb_id=get_settings().wechat_default_kb_id,
            metadata={
                "provider": "eyun",
                "account": payload.get("account"),
                "message_type": message_type,
                "wc_id": payload.get("wcId"),
                "w_id": data.get("wId") or payload.get("wId"),
                "from_user": from_user,
                "from_group": from_group,
                "to_user": data.get("toUser"),
                "msg_id": data.get("msgId"),
                "new_msg_id": data.get("newMsgId"),
                "timestamp": data.get("timestamp"),
            },
        )
    )
    answer = str(chat_result.get("answer") or "").strip()
    if answer:
        await send_eyun_text(
            w_id=str(data.get("wId") or payload.get("wId") or get_settings().eyun_wid),
            wc_id=target_wc_id,
            content=answer,
        )
    return eyun_success()


async def send_eyun_text(*, w_id: str, wc_id: str, content: str) -> None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        logger.warning("Skip Eyun sendText because EYUN_BASE_URL/EYUN_AUTHORIZATION/EYUN_WID is incomplete")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/sendText",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={"wId": w_id, "wcId": wc_id, "content": content},
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        logger.warning("Eyun sendText returned non-success response: %s", result)
