import logging

import httpx

from app.core.config import get_settings


logger = logging.getLogger("wechat_rag_bot.feishu_alert")


async def send_feishu_webhook_alert(content: str) -> bool:
    """Send one text alert without ever logging the webhook URL."""
    settings = get_settings()
    webhook_url = (
        settings.feishu_alert_webhook_url.strip()
        or settings.feishu_handoff_webhook_url.strip()
    )
    if not webhook_url:
        logger.warning("Skip Feishu alert because webhook is not configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": content}},
            )
        response.raise_for_status()
        result = response.json()
        status_code = result.get("StatusCode", result.get("code"))
        if status_code not in (0, "0"):
            raise RuntimeError(
                str(result.get("StatusMessage") or result.get("msg") or status_code)
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Feishu webhook alert failed: %s", type(exc).__name__)
        return False
