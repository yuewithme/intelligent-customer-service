import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.conversation_service import AI_WAITING, HANDOFF_PENDING, record_customer_message
from app.services.message_risk_control_service import enqueue_eyun_inbound


logger = logging.getLogger("wechat_rag_bot.eyun_callback")

EYUN_TEST_CALLBACK = "00000"
EYUN_PRIVATE_TEXT = "60001"
EYUN_PRIVATE_IMAGE = "60002"
EYUN_GROUP_TEXT = "80001"


def is_eyun_text_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) in {EYUN_PRIVATE_TEXT, EYUN_GROUP_TEXT}


def is_eyun_workbench_message(payload: dict[str, Any]) -> bool:
    return _message_type_in_range(payload, 60000, 60999) or _message_type_in_range(
        payload, 80000, 80999
    )


def eyun_success() -> dict[str, Any]:
    return {"code": "1000", "message": "success", "data": None}


def should_process_eyun_payload(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return (
        str(payload.get("messageType", "")) != EYUN_TEST_CALLBACK
        and not _is_self_message(data)
        and is_eyun_workbench_message(payload)
        and bool(str(data.get("fromGroup") or data.get("fromUser") or "").strip())
    )


def is_eyun_non_text_message(payload: dict[str, Any]) -> bool:
    return not is_eyun_text_message(payload)


def is_eyun_private_text_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) == EYUN_PRIVATE_TEXT


async def handle_eyun_callback(payload: dict[str, Any]) -> dict[str, Any]:
    message_type = str(payload.get("messageType", ""))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if message_type == EYUN_TEST_CALLBACK:
        return eyun_success()
    if _is_self_message(data):
        return eyun_success()
    if not str(data.get("fromGroup") or data.get("fromUser") or "").strip():
        return eyun_success()

    await record_customer_message(
        channel="wechat",
        user_id=_eyun_conversation_user_id(data),
        session_id="default",
        content=_eyun_display_content(payload),
        message_id=_eyun_message_id(data),
        status=AI_WAITING if is_eyun_private_text_message(payload) else HANDOFF_PENDING,
        route="inbound_text" if is_eyun_text_message(payload) else "non_text",
        primary_intent="message" if is_eyun_text_message(payload) else _eyun_message_kind(message_type),
        handoff_reason=None if is_eyun_private_text_message(payload) else "unsupported_message_type",
        metadata=_eyun_workbench_metadata(payload, data),
    )

    if not is_eyun_private_text_message(payload):
        return eyun_success()

    content = str(data.get("content") or "").strip()
    if not content:
        return eyun_success()

    await enqueue_eyun_inbound(payload)
    return eyun_success()


def _eyun_non_text_label(message_type: str) -> str:
    return {
        "60002": "[图片]",
        "60003": "[语音]",
        "60004": "[视频]",
        "60005": "[文件]",
        "60006": "[位置]",
        "60007": "[链接]",
        "60008": "[名片]",
        "60009": "[表情]",
        "60010": "[小程序]",
        "80002": "[图片]",
        "80003": "[语音]",
        "80004": "[视频]",
        "80005": "[文件]",
        "80006": "[位置]",
        "80007": "[链接]",
        "80009": "[表情]",
        "80010": "[小程序]",
    }.get(message_type, "[非文本消息]")


def _message_type_in_range(payload: dict[str, Any], start: int, end: int) -> bool:
    try:
        message_type = int(str(payload.get("messageType", "")))
    except ValueError:
        return False
    return start <= message_type <= end


def _is_self_message(data: dict[str, Any]) -> bool:
    value = data.get("self")
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _eyun_conversation_user_id(data: dict[str, Any]) -> str:
    return str(data.get("fromGroup") or data.get("fromUser") or "")


def _eyun_message_id(data: dict[str, Any]) -> str | None:
    return str(data.get("newMsgId") or data.get("msgId") or "") or None


def _eyun_display_content(payload: dict[str, Any]) -> str:
    if is_eyun_text_message(payload):
        return str((payload.get("data") or {}).get("content") or "").strip() or "[空消息]"
    return _eyun_non_text_label(str(payload.get("messageType", "")))


def _eyun_message_kind(message_type: str) -> str:
    if message_type.endswith("002"):
        return "image"
    return "non_text"


def _eyun_workbench_metadata(payload: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "eyun",
        "account": str(payload.get("account") or ""),
        "message_type": str(payload.get("messageType") or ""),
        "wc_id": str(payload.get("wcId") or data.get("toUser") or ""),
        "w_id": str(data.get("wId") or payload.get("wId") or ""),
        "from_user": str(data.get("fromUser") or ""),
        "from_group": str(data.get("fromGroup") or ""),
        "raw_content": str(data.get("content") or ""),
        "image_thumb_base64": str(data.get("img") or ""),
        "message_id": _eyun_message_id(data),
        "skip_customer_record": True,
    }


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
