import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import get_settings
from app.services.conversation_service import update_customer_identity
from app.services.user_profile_service import ensure_user_profile


logger = logging.getLogger("wechat_rag_bot.eyun_contact")

_CACHE_TTL_SECONDS = 300
_contact_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_contact_refresh_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}


def _text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_contact(contact: dict[str, Any]) -> dict[str, Any]:
    user_name = _text(contact, "userName", "username")
    remark = _text(contact, "remark", "remarkName", "conRemark")
    nickname = _text(contact, "nickName", "nickname")
    alias_name = _text(contact, "aliasName")
    label_list = _text(contact, "labelList")
    avatar_url = _text(
        contact,
        "bigHead",
        "smallHead",
        "bigHeadImgUrl",
        "smallHeadImgUrl",
        "avatarUrl",
        "avatar",
    )

    snapshot: dict[str, Any] = {}
    if remark:
        snapshot["remark_name"] = remark
    if nickname:
        snapshot["nickname"] = nickname
    if alias_name:
        snapshot["alias_name"] = alias_name
    if label_list:
        snapshot["label_ids"] = [
            value.strip() for value in label_list.split(",") if value.strip()
        ]
    if not remark and not nickname and user_name and user_name.endswith("@openim"):
        suffix = user_name.removesuffix("@openim")[-5:]
        snapshot["display_name"] = f"企业微信用户（{suffix}）"
    if avatar_url:
        snapshot["avatar_url"] = avatar_url
    return snapshot


def parse_contact_snapshots(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map an Eyun getContactPlus response by its stable userName field."""
    if str(response.get("code")) != "1000":
        return {}
    data = response.get("data")
    contacts = data if isinstance(data, list) else [data]
    snapshots: dict[str, dict[str, Any]] = {}
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        user_name = _text(contact, "userName", "username")
        if user_name:
            snapshots[user_name] = _parse_contact(contact)
    return snapshots


def parse_contact_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    snapshots = parse_contact_snapshots(response)
    return next(iter(snapshots.values()), {})


async def get_eyun_contact_snapshots(
    *, w_id: str, wc_ids: list[str], force: bool = False
) -> dict[str, dict[str, Any]]:
    """Fetch complete Eyun contact details in documented batches of at most 20."""
    unique_ids = list(dict.fromkeys(value.strip() for value in wc_ids if value.strip()))
    if not w_id or not unique_ids:
        return {}

    now = time.monotonic()
    results: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for wc_id in unique_ids:
        cached = _contact_cache.get((w_id, wc_id))
        if not force and cached and now - cached[0] < _CACHE_TTL_SECONDS:
            results[wc_id] = dict(cached[1])
        else:
            pending.append(wc_id)

    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization:
        return results

    async with httpx.AsyncClient(timeout=20) as client:
        for offset in range(0, len(pending), 20):
            batch = pending[offset : offset + 20]
            try:
                response = await client.post(
                    f"{base_url}/getContactPlus",
                    headers={
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                    },
                    json={"wId": w_id, "wcId": ",".join(batch)},
                )
                response.raise_for_status()
                body = response.json()
                parsed = parse_contact_snapshots(body)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Eyun getContactPlus batch failed: %s", exc)
                parsed = {}
            for wc_id, snapshot in parsed.items():
                _contact_cache[(w_id, wc_id)] = (now, snapshot)
                results[wc_id] = dict(snapshot)
            if offset + 20 < len(pending):
                await asyncio.sleep(0.3)
    return results


async def get_eyun_contact_snapshot(*, w_id: str, wc_id: str) -> dict[str, Any]:
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
            for endpoint in ("getContactPlus", "getContact"):
                try:
                    response = await client.post(
                        f"{base_url}/{endpoint}",
                        headers={
                            "Authorization": authorization,
                            "Content-Type": "application/json",
                        },
                        json={"wId": w_id, "wcId": wc_id},
                    )
                    response.raise_for_status()
                    body = response.json()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Eyun %s failed for wcId=%s: %s", endpoint, wc_id, exc
                    )
                    continue
                snapshot = parse_contact_snapshot(body)
                if snapshot:
                    _contact_cache[cache_key] = (now, snapshot)
                    return dict(snapshot)
                logger.warning(
                    "Eyun %s returned no usable contact fields: code=%s message=%s",
                    endpoint,
                    body.get("code"),
                    body.get("message"),
                )
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun contact lookup failed for wcId=%s: %s", wc_id, exc)
        return {}


async def initialize_eyun_contacts(*, w_id: str) -> bool:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=190) as client:
            response = await client.post(
                f"{base_url}/initAddressList",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
                json={"wId": w_id},
            )
        response.raise_for_status()
        body = response.json()
        if str(body.get("code")) == "1000":
            return True
        logger.warning(
            "Eyun initAddressList returned non-success: code=%s message=%s",
            body.get("code"),
            body.get("message"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun initAddressList failed for wId=%s: %s", w_id, exc)
    return False


async def refresh_eyun_contact(
    *,
    w_id: str,
    wc_id: str,
    user_id: str,
    tenant_id: str = "tenant_default",
    channel: str = "wechat",
    session_id: str | None = "default",
    delay_seconds: float | None = None,
) -> None:
    if not await initialize_eyun_contacts(w_id=w_id):
        return
    delay = (
        get_settings().eyun_contact_refresh_delay_seconds
        if delay_seconds is None
        else delay_seconds
    )
    await asyncio.sleep(delay)
    _contact_cache.pop((w_id, wc_id), None)
    snapshot = await get_eyun_contact_snapshot(w_id=w_id, wc_id=wc_id)
    if not snapshot:
        logger.warning("Eyun contact refresh still empty for wcId=%s", wc_id)
        return
    await ensure_user_profile(
        user_id,
        tenant_id=tenant_id,
        channel=channel,
        basic_info=snapshot,
    )
    await update_customer_identity(
        channel=channel,
        user_id=user_id,
        session_id=session_id,
        metadata=snapshot,
    )


def schedule_eyun_contact_refresh(
    *,
    w_id: str,
    wc_id: str,
    user_id: str,
    tenant_id: str = "tenant_default",
    channel: str = "wechat",
    session_id: str | None = "default",
) -> asyncio.Task[None] | None:
    if not w_id or not wc_id or not user_id:
        return None
    key = (w_id, wc_id)
    existing = _contact_refresh_tasks.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(
        refresh_eyun_contact(
            w_id=w_id,
            wc_id=wc_id,
            user_id=user_id,
            tenant_id=tenant_id,
            channel=channel,
            session_id=session_id,
        )
    )
    _contact_refresh_tasks[key] = task

    def remove_completed(completed: asyncio.Task[None]) -> None:
        if _contact_refresh_tasks.get(key) is completed:
            _contact_refresh_tasks.pop(key, None)

    task.add_done_callback(remove_completed)
    return task
