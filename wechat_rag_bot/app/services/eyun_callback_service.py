import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

from app.config import get_settings
from app.services.conversation_service import (
    AI_WAITING,
    HANDOFF_PENDING,
    ensure_outbound_conversation_message,
    record_customer_message,
)
from app.services.eyun_contact_service import (
    get_eyun_contact_snapshot,
    schedule_eyun_contact_refresh,
)
from app.services.message_risk_control_service import (
    enqueue_eyun_inbound,
)
from app.services.user_profile_service import ensure_user_profile


logger = logging.getLogger("wechat_rag_bot.eyun_callback")

EYUN_TEST_CALLBACK = "00000"
EYUN_PRIVATE_TEXT = "60001"
EYUN_PRIVATE_IMAGE = "60002"
EYUN_GROUP_TEXT = "80001"


def is_eyun_text_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) in {EYUN_PRIVATE_TEXT, EYUN_GROUP_TEXT}


def is_eyun_workbench_message(payload: dict[str, Any]) -> bool:
    return _message_type_in_range(payload, 60000, 60999)


def is_eyun_group_payload(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    identifiers = (
        data.get("fromGroup"),
        data.get("fromUser"),
        data.get("toUser"),
    )
    return (
        _message_type_in_range(payload, 80000, 89999)
        or bool(str(data.get("fromGroup") or "").strip())
        or any(str(value or "").strip().endswith("@chatroom") for value in identifiers)
    )


def eyun_success() -> dict[str, Any]:
    return {"code": "1000", "message": "success", "data": None}


def should_process_eyun_payload(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return (
        str(payload.get("messageType", "")) != EYUN_TEST_CALLBACK
        and not is_eyun_group_payload(payload)
        and is_eyun_workbench_message(payload)
        and bool(str(data.get("fromGroup") or data.get("fromUser") or "").strip())
    )


def is_eyun_non_text_message(payload: dict[str, Any]) -> bool:
    return not is_eyun_text_message(payload)


def is_eyun_private_text_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) == EYUN_PRIVATE_TEXT


def is_eyun_private_image_message(payload: dict[str, Any]) -> bool:
    return str(payload.get("messageType", "")) == EYUN_PRIVATE_IMAGE


async def handle_eyun_callback(payload: dict[str, Any]) -> dict[str, Any]:
    message_type = str(payload.get("messageType", ""))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if is_eyun_group_payload(payload):
        return eyun_success()
    if message_type == EYUN_TEST_CALLBACK:
        return eyun_success()
    if not is_eyun_workbench_message(payload):
        return eyun_success()
    if not str(data.get("fromGroup") or data.get("fromUser") or "").strip():
        return eyun_success()

    if _is_self_message(data):
        user_id = str(data.get("fromGroup") or data.get("toUser") or "").strip()
        if not user_id:
            return eyun_success()
        metadata = await _eyun_workbench_metadata(payload, data, user_id=user_id)
        _capture_material_group_message(payload, metadata)
        await ensure_outbound_conversation_message(
            channel="wechat",
            user_id=user_id,
            session_id=str(data.get("fromGroup") or "default"),
            content=str(data.get("content") or ""),
            message_type=(
                "text" if is_eyun_text_message(payload) else _eyun_message_kind(message_type)
            ),
            sender_type="human",
            sender_id="wechat_client",
            provider_message_id=_eyun_message_id(data),
            delivery_status="sent",
            route="self_outbound",
            metadata={**metadata, "origin": "wechat_client"},
        )
        return eyun_success()

    metadata = await _eyun_workbench_metadata(payload, data)
    _capture_material_group_message(payload, metadata)
    user_id = _eyun_conversation_user_id(data)
    if not str(data.get("fromGroup") or "").strip():
        await ensure_user_profile(
            user_id,
            tenant_id=str(payload.get("tenant_id") or "tenant_default"),
            channel="wechat",
            basic_info={
                "owner_wc_id": str(payload.get("wcId") or data.get("toUser") or ""),
                **{
                    key: metadata.get(key)
                    for key in (
                        "nickname",
                        "remark_name",
                        "alias_name",
                        "avatar_url",
                        "label_ids",
                    )
                    if metadata.get(key) not in (None, "", [])
                },
            },
        )
    is_private_image = is_eyun_private_image_message(payload)
    await record_customer_message(
        channel="wechat",
        user_id=user_id,
        session_id="default",
        content=_eyun_display_content(payload),
        message_id=_eyun_message_id(data),
        status=(
            AI_WAITING
            if is_eyun_private_text_message(payload) or is_private_image
            else HANDOFF_PENDING
        ),
        route=(
            "inbound_text"
            if is_eyun_text_message(payload)
            else "inbound_image" if is_private_image else "non_text"
        ),
        primary_intent="message" if is_eyun_text_message(payload) else _eyun_message_kind(message_type),
        handoff_reason=(
            None
            if is_eyun_private_text_message(payload) or is_private_image
            else "unsupported_message_type"
        ),
        metadata=metadata,
    )

    if (
        not str(data.get("fromGroup") or "").strip()
        and not _has_contact_display(metadata)
    ):
        schedule_eyun_contact_refresh(
            w_id=str(data.get("wId") or payload.get("wId") or get_settings().eyun_wid or ""),
            wc_id=user_id,
            user_id=user_id,
            tenant_id=str(payload.get("tenant_id") or "tenant_default"),
            channel="wechat",
            session_id="default",
        )

    if is_private_image:
        queued_payload = {**payload, "data": dict(data)}
        media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
        if media.get("url"):
            queued_payload["data"]["_image_url"] = str(media["url"])
        await enqueue_eyun_inbound(queued_payload)
        return eyun_success()

    if not is_eyun_private_text_message(payload):
        return eyun_success()

    content = str(data.get("content") or "").strip()
    if not content:
        return eyun_success()

    await enqueue_eyun_inbound(payload)
    return eyun_success()


def _capture_material_group_message(
    payload: dict[str, Any], metadata: dict[str, Any]
) -> None:
    try:
        from app.services.eyun_material_service import capture_material_group_message

        capture_material_group_message(payload, metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun material-group capture failed: %s", exc)


def _has_contact_display(metadata: dict[str, Any]) -> bool:
    return any(
        metadata.get(key)
        for key in ("remark_name", "display_name", "nickname", "avatar_url")
    )


def _eyun_non_text_label(message_type: str) -> str:
    return {
        "002": "[鍥剧墖]",
        "003": "[瑙嗛]",
        "004": "[璇煶]",
        "005": "[鍚嶇墖]",
        "006": "[琛ㄦ儏]",
        "007": "[閾炬帴]",
        "008": "[鏂囦欢]",
        "009": "[鏂囦欢]",
        "010": "[灏忕▼搴廬",
        "011": "[鑱婂ぉ璁板綍]",
        "020": "[浣嶇疆]",
    }.get(message_type[-3:], "[闈炴枃鏈秷鎭痌")


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


def _eyun_provider_message_id(data: dict[str, Any]) -> str | None:
    return str(data.get("msgId") or data.get("newMsgId") or "") or None


def _eyun_display_content(payload: dict[str, Any]) -> str:
    if is_eyun_text_message(payload):
        return str((payload.get("data") or {}).get("content") or "").strip() or "[绌烘秷鎭痌"
    return _eyun_non_text_label(str(payload.get("messageType", "")))


def _eyun_message_kind(message_type: str) -> str:
    return {
        "002": "image",
        "003": "video",
        "004": "audio",
        "005": "contact",
        "006": "image",
        "007": "link",
        "008": "file",
        "009": "file",
        "010": "mini_program",
        "020": "location",
    }.get(message_type[-3:], "non_text")


async def _eyun_workbench_metadata(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    message_type = str(payload.get("messageType") or "")
    w_id = str(data.get("wId") or payload.get("wId") or "")
    user_id = user_id or _eyun_conversation_user_id(data)
    metadata = {
        "provider": "eyun",
        "account": str(payload.get("account") or ""),
        "message_type": message_type,
        "wc_id": str(payload.get("wcId") or data.get("toUser") or ""),
        "w_id": w_id,
        "from_user": str(data.get("fromUser") or ""),
        "from_group": str(data.get("fromGroup") or ""),
        "raw_content": str(data.get("content") or ""),
        "image_thumb_base64": str(data.get("img") or ""),
        "message_id": _eyun_message_id(data),
        "provider_msg_id": _eyun_provider_message_id(data),
        "skip_customer_record": True,
    }
    metadata.update(await get_eyun_contact_snapshot(w_id=w_id, wc_id=user_id))
    media = await _eyun_media_metadata(message_type, data, w_id=w_id)
    if media:
        metadata["media"] = media
    return metadata


async def _eyun_media_metadata(
    message_type: str, data: dict[str, Any], *, w_id: str
) -> dict[str, Any] | None:
    media = extract_eyun_media_metadata(message_type, data)
    if media is None:
        return None

    content = str(data.get("content") or "")
    if message_type.endswith("002"):
        msg_id = _eyun_provider_message_id(data) or ""
        url = await fetch_eyun_image_url(
            w_id=w_id,
            msg_id=msg_id,
            content=content,
            image_type=1,
        ) or await fetch_eyun_image_url(
            w_id=w_id,
            msg_id=msg_id,
            content=content,
            image_type=0,
        )
        if url:
            media["url"] = url

    media["fallback"] = not bool(media.get("url"))
    return media


def extract_eyun_media_metadata(
    message_type: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    kind = _eyun_message_kind(message_type)
    if kind == "non_text":
        return None

    content = str(data.get("content") or "")
    media: dict[str, Any] = {"type": kind, **_xml_media_metadata(content)}
    direct_url = _first_text(data, ("url", "fileUrl", "downloadUrl", "videoUrl"))
    if direct_url:
        media["url"] = direct_url

    if kind == "video" and media.get("url"):
        media["original_url"] = media.pop("url")
        media["resolve_status"] = "pending"

    if message_type.endswith("002"):
        media["thumb_base64"] = str(data.get("img") or "")

    media["fallback"] = not bool(media.get("url"))
    return media


def _xml_media_metadata(content: str) -> dict[str, str]:
    if not content.lstrip().startswith("<"):
        return {}
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return {}

    result: dict[str, str] = {}
    for element in root.iter():
        if element.tag in {"title", "filename"} and element.text and element.text.strip():
            result.setdefault("file_name", element.text.strip())
        for value in (element.text or "", *element.attrib.values()):
            candidate = unquote(value.strip())
            if candidate.startswith(("http://", "https://")):
                result.setdefault("url", candidate)
    return result


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def fetch_eyun_image_url(
    *, w_id: str, msg_id: str, content: str, image_type: int = 1
) -> str | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id or not msg_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/getMsgImg",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
                json={
                    "wId": w_id,
                    "msgId": msg_id,
                    "content": content,
                    "type": image_type,
                },
            )
        response.raise_for_status()
        result = response.json()
        if str(result.get("code")) != "1000":
            logger.warning("Eyun getMsgImg returned non-success response: %s", result)
            return None
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("url", "imgUrl", "imageUrl", "src"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(data, str) and data.strip():
            return data.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun getMsgImg failed for msgId=%s: %s", msg_id, exc)
    return None


async def download_eyun_video(*, w_id: str, msg_id: str, content: str) -> str:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id or not msg_id or not content:
        raise RuntimeError("Eyun video download parameters are incomplete")

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/asynGetMsgVideo",
            headers=headers,
            json={"wId": w_id, "msgId": msg_id, "content": content},
        )
        response.raise_for_status()
        result = response.json()
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        task_id = str(data.get("id") or "").strip()
        if str(result.get("code")) != "1000" or not task_id:
            raise RuntimeError(f"Eyun video download submission failed: {result}")

        for attempt in range(10):
            if attempt:
                await asyncio.sleep(2)
            poll = await client.post(
                f"{base_url}/getMsgVideoRes",
                headers=headers,
                json={"id": task_id},
            )
            poll.raise_for_status()
            poll_result = poll.json()
            poll_data = (
                poll_result.get("data")
                if isinstance(poll_result.get("data"), dict)
                else {}
            )
            status = int(poll_data.get("type") or 0)
            url = str(poll_data.get("url") or "").strip()
            if str(poll_result.get("code")) == "1000" and status == 1 and url:
                return await persist_eyun_video(
                    client=client,
                    source_url=url,
                    authorization=authorization,
                    msg_id=msg_id,
                )
            if status == 2:
                break

    raise RuntimeError("Eyun video download did not complete")


def video_storage_dir() -> Path:
    return Path(__file__).parents[2] / "data" / "media"


async def persist_eyun_video(
    *,
    client: httpx.AsyncClient,
    source_url: str,
    authorization: str,
    msg_id: str,
) -> str:
    file_key = hashlib.sha256(f"{msg_id}:{source_url}".encode()).hexdigest()[:24]
    storage_dir = video_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    target = storage_dir / f"{file_key}.mp4"
    temporary = target.with_suffix(".tmp")

    async with client.stream(
        "GET",
        source_url,
        headers={"Authorization": authorization},
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type.startswith(("text/", "application/json")):
            raise RuntimeError("Eyun video download result is not a video")
        with temporary.open("wb") as output:
            async for chunk in response.aiter_bytes():
                output.write(chunk)
    temporary.replace(target)
    return f"/static/media/{target.name}"


async def send_eyun_text(
    *, w_id: str, wc_id: str, content: str
) -> dict[str, Any] | None:
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
        raise RuntimeError(f"Eyun sendText failed: {result}")
    return result


async def send_eyun_image(
    *, w_id: str, wc_id: str, content: str
) -> dict[str, Any] | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        logger.warning("Skip Eyun sendImage2 because Eyun configuration is incomplete")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/sendImage2",
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            json={"wId": w_id, "wcId": wc_id, "content": content},
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        logger.warning("Eyun sendImage2 returned non-success response: %s", result)
        raise RuntimeError(f"Eyun sendImage2 failed: {result}")
    return result


async def send_eyun_video(
    *, w_id: str, wc_id: str, path: str, thumb_path: str
) -> dict[str, Any] | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise RuntimeError("Eyun configuration is incomplete")
    if not path or not thumb_path:
        raise ValueError("video path and thumb_path are required")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/sendVideo",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={
                "wId": w_id,
                "wcId": wc_id,
                "path": path,
                "thumbPath": thumb_path,
            },
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        raise RuntimeError(f"Eyun sendVideo failed: {result}")
    return result


async def send_eyun_received_media(
    *, w_id: str, wc_id: str, content: str, message_type: str
) -> dict[str, Any] | None:
    endpoint = {
        "received_image": "/sendRecvImage",
        "received_video": "/sendRecvVideo",
    }.get(message_type)
    if endpoint is None:
        raise ValueError(f"unsupported received media type: {message_type}")
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise RuntimeError("Eyun configuration is incomplete")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}{endpoint}",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={"wId": w_id, "wcId": wc_id, "content": content},
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        raise RuntimeError(f"Eyun received media send failed: {result}")
    return result


async def send_eyun_mini_program(
    *,
    w_id: str,
    wc_id: str,
    card: dict[str, str],
) -> dict[str, Any] | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        logger.warning("Skip Eyun sendApplets because Eyun configuration is incomplete")
        return

    payload = {
        "wId": w_id,
        "wcId": wc_id,
        "displayName": card.get("display_name", ""),
        "iconUrl": card.get("icon_url", ""),
        "appId": card.get("app_id", ""),
        "pagePath": card.get("page_path", ""),
        "thumbUrl": card.get("thumb_url", ""),
        "title": card.get("title", ""),
        "userName": card.get("user_name", ""),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/sendApplets",
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        logger.warning("Eyun sendApplets returned non-success response: %s", result)
        raise RuntimeError(f"Eyun sendApplets failed: {result}")
    return result


async def send_eyun_link_card(
    *,
    w_id: str,
    wc_id: str,
    card: dict[str, str],
) -> dict[str, Any] | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise RuntimeError("Eyun configuration is incomplete")
    url = str(card.get("url") or "").strip()
    title = str(card.get("title") or "").strip() or "鏌ョ湅璇︽儏"
    description = str(card.get("description") or "").strip() or "鐐瑰嚮鏌ョ湅璇︽儏"
    thumb_url = str(card.get("thumb_url") or "").strip()
    if not thumb_url:
        thumb_url = settings.eyun_link_card_default_thumb_url.strip()
    if not url:
        raise RuntimeError("Eyun link card URL is required")
    if not thumb_url:
        raise RuntimeError(
            "Eyun link card thumbnail is required; configure "
            "EYUN_LINK_CARD_DEFAULT_THUMB_URL or provide thumb_url"
        )
    payload = {
        "wId": w_id,
        "wcId": wc_id,
        "title": title,
        "url": url,
        "description": description,
        "thumbUrl": thumb_url,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/sendUrl",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        logger.warning("Eyun sendUrl returned non-success response: %s", result)
        raise RuntimeError(f"Eyun sendUrl failed: {result}")
    return result
