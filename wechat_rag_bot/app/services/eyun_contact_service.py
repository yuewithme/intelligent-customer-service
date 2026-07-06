import logging
import time
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger("wechat_rag_bot.eyun_contact")

_CACHE_TTL_SECONDS = 300
_contact_cache: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}


def _text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_contact_snapshot(response: dict[str, Any]) -> dict[str, str]:
    if str(response.get("code")) != "1000":
        return {}
    data = response.get("data")
    if isinstance(data, list):
        contact = data[0] if data and isinstance(data[0], dict) else {}
    else:
        contact = data if isinstance(data, dict) else {}

    user_name = _text(contact, "userName", "username")
    remark = _text(contact, "remark", "remarkName", "conRemark")
    nickname = _text(contact, "nickName", "nickname")
    avatar_url = _text(
        contact,
        "bigHead",
        "smallHead",
        "bigHeadImgUrl",
        "smallHeadImgUrl",
        "avatarUrl",
        "avatar",
    )

    snapshot: dict[str, str] = {}
    if remark:
        snapshot["remark_name"] = remark
    if nickname:
        snapshot["nickname"] = nickname
    if not remark and not nickname and user_name and user_name.endswith("@openim"):
        suffix = user_name.removesuffix("@openim")[-5:]
        snapshot["display_name"] = f"企业微信用户（{suffix}）"
    if avatar_url:
        snapshot["avatar_url"] = avatar_url
    return snapshot


async def get_eyun_contact_snapshot(*, w_id: str, wc_id: str) -> dict[str, str]:
    if not w_id or not wc_id:
        return {}

    cache_key = (w_id, wc_id)
    cached = _contact_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return dict(cached[1])

    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization:
        return {}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{base_url}/getContact",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
                json={"wId": w_id, "wcId": wc_id},
            )
        response.raise_for_status()
        body = response.json()
        snapshot = parse_contact_snapshot(body)
        if not snapshot:
            logger.warning(
                "Eyun getContact returned no usable contact fields: code=%s message=%s",
                body.get("code"),
                body.get("message"),
            )
            return {}
        _contact_cache[cache_key] = (now, snapshot)
        return dict(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun getContact failed for wcId=%s: %s", wc_id, exc)
        return {}
