import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.message_risk_control_service import enqueue_eyun_inbound


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

    if not str(data.get("fromGroup") or data.get("fromUser") or "").strip():
        return eyun_success()

    await enqueue_eyun_inbound(payload)
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
