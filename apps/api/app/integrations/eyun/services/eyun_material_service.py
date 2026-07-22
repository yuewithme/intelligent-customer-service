from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

import httpx
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    ConversationMessageModel,
    EyunBulkSendJobModel,
    EyunMediaMaterialModel,
    EyunOutboundMessageModel,
)
from app.shared.schemas.common import AppError, ErrorCode


_sessionmakers: dict[str, sessionmaker] = {}
_material_locks: dict[str, asyncio.Lock] = {}
_source_locks: dict[str, asyncio.Lock] = {}
_source_cache: dict[str, tuple[int, float]] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def materialize_eyun_outbound_media(
    *, w_id: str, message_type: str, content: str
) -> dict[str, Any]:
    """Turn a legacy image/video URL payload into one reusable Eyun CDN material."""
    media_type = message_type.strip().lower()
    if media_type == "image":
        source_url = content.strip()
        thumb_url = ""
    elif media_type == "video":
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("video content must contain path and thumb_path") from exc
        source_url = str(payload.get("path") or "").strip()
        thumb_url = str(payload.get("thumb_path") or "").strip()
    else:
        raise ValueError(f"unsupported media type: {message_type}")
    if not source_url or (media_type == "video" and not thumb_url):
        raise ValueError(f"{media_type} source URL is required")

    source_key = f"{media_type}\0{source_url}\0{thumb_url}"
    source_lock = _source_locks.setdefault(source_key, asyncio.Lock())
    async with source_lock:
        cached = _source_cache.get(source_key)
        if cached is not None and asyncio.get_running_loop().time() - cached[1] < 300:
            try:
                return get_ready_material(cached[0])
            except Exception:  # noqa: BLE001 - stale/expired cache falls through
                _source_cache.pop(source_key, None)
        material = await _materialize_source_urls(
            w_id=w_id,
            media_type=media_type,
            source_url=source_url,
            thumb_url=thumb_url,
        )
        _source_cache[source_key] = (
            int(material["id"]),
            asyncio.get_running_loop().time(),
        )
        return material


async def _materialize_source_urls(
    *, w_id: str, media_type: str, source_url: str, thumb_url: str
) -> dict[str, Any]:

    source_bytes, thumb_bytes = await asyncio.gather(
        _fetch_source_bytes(source_url),
        _fetch_source_bytes(thumb_url) if thumb_url else _empty_bytes(),
    )
    fingerprint = _source_fingerprint(media_type, source_bytes, thumb_bytes)
    lock = _material_locks.setdefault(fingerprint, asyncio.Lock())
    async with lock:
        existing = _find_material_by_hash(fingerprint)
        if existing is not None and existing["status"] == "ready":
            return get_ready_material(int(existing["id"]))

        owns_upload = _reserve_generated_material(
            fingerprint=fingerprint,
            media_type=media_type,
            preview_url=source_url,
        )
        if not owns_upload:
            return await _wait_for_generated_material(fingerprint)

        try:
            if media_type == "image":
                image_cdn = await _upload_eyun_cdn_image(w_id=w_id, url=source_url)
                raw_xml = _build_image_xml(image_cdn, source_bytes)
            else:
                video_cdn, thumb_cdn = await asyncio.gather(
                    _upload_eyun_cdn_video(
                        w_id=w_id, path=source_url, thumb_path=thumb_url
                    ),
                    _upload_eyun_cdn_image(w_id=w_id, url=thumb_url),
                )
                raw_xml = _build_video_xml(
                    video_cdn, thumb_cdn, source_bytes, thumb_bytes
                )
            return _complete_generated_material(
                fingerprint=fingerprint,
                raw_xml=raw_xml,
                preview_url=source_url,
            )
        except Exception as exc:
            _fail_generated_material(fingerprint, str(exc))
            raise


async def _empty_bytes() -> bytes:
    return b""


async def _fetch_source_bytes(url: str) -> bytes:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("media source must be an HTTP(S) URL")
    maximum_bytes = 200 * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > maximum_bytes:
                    raise ValueError("media source exceeds 200 MB")
                chunks.append(chunk)
    return b"".join(chunks)


def _source_fingerprint(media_type: str, source: bytes, thumb: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(b"eyun-generated-material-v1\0")
    digest.update(media_type.encode("ascii"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(source).digest())
    digest.update(hashlib.sha256(thumb).digest())
    return digest.hexdigest()


async def _post_eyun_cdn(endpoint: str, payload: dict[str, str]) -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not payload.get("wId"):
        raise RuntimeError("Eyun configuration is incomplete")
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{base_url}/{endpoint}",
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000" or not isinstance(result.get("data"), dict):
        raise RuntimeError(f"Eyun {endpoint} failed: {result}")
    return result["data"]


async def _upload_eyun_cdn_image(*, w_id: str, url: str) -> dict[str, Any]:
    return await _post_eyun_cdn("uploadCdnImage", {"wId": w_id, "content": url})


async def _upload_eyun_cdn_video(
    *, w_id: str, path: str, thumb_path: str
) -> dict[str, Any]:
    return await _post_eyun_cdn(
        "sendCdnVideo", {"wId": w_id, "path": path, "thumbPath": thumb_path}
    )


def _build_image_xml(cdn: dict[str, Any], source: bytes) -> str:
    cdn_url = str(cdn.get("cdnUrl") or "")
    aes_key = str(cdn.get("aesKey") or "")
    length = str(int(cdn.get("hdLength") or len(source)))
    if not cdn_url or not aes_key:
        raise RuntimeError("Eyun uploadCdnImage response is missing CDN fields")
    attributes = {
        "aeskey": aes_key,
        "encryver": "0",
        "cdnthumbaeskey": aes_key,
        "cdnthumburl": cdn_url,
        "cdnthumblength": length,
        "cdnthumbheight": "0",
        "cdnthumbwidth": "0",
        "cdnmidheight": "0",
        "cdnmidwidth": "0",
        "cdnhdheight": "0",
        "cdnhdwidth": "0",
        "cdnmidimgurl": cdn_url,
        "length": length,
        "cdnbigimgurl": cdn_url,
        "hdlength": length,
        "md5": hashlib.md5(source).hexdigest(),  # noqa: S324 - provider XML field
        "hevc_mid_size": "0",
    }
    return _xml_message("img", attributes)


def _build_video_xml(
    video_cdn: dict[str, Any],
    thumb_cdn: dict[str, Any],
    source: bytes,
    thumb: bytes,
) -> str:
    video_url = str(video_cdn.get("cdnUrl") or "")
    video_key = str(video_cdn.get("aesKey") or "")
    thumb_url = str(thumb_cdn.get("cdnUrl") or "")
    thumb_key = str(thumb_cdn.get("aesKey") or "")
    if not all((video_url, video_key, thumb_url, thumb_key)):
        raise RuntimeError("Eyun CDN video response is missing CDN fields")
    attributes = {
        "aeskey": video_key,
        "cdnthumbaeskey": thumb_key,
        "cdnvideourl": video_url,
        "cdnthumburl": thumb_url,
        "length": str(int(video_cdn.get("length") or len(source))),
        "playlength": "0",
        "cdnthumblength": str(int(thumb_cdn.get("hdLength") or len(thumb))),
        "cdnthumbwidth": "0",
        "cdnthumbheight": "0",
        "fromusername": "",
        "md5": hashlib.md5(source).hexdigest(),  # noqa: S324 - provider XML field
        "newmd5": hashlib.md5(source).hexdigest(),  # noqa: S324 - provider XML field
        "isad": "0",
    }
    return _xml_message("videomsg", attributes)


def _xml_message(tag: str, attributes: dict[str, str]) -> str:
    root = ElementTree.Element("msg")
    ElementTree.SubElement(root, tag, attributes)
    return '<?xml version="1.0"?>\n' + ElementTree.tostring(
        root, encoding="unicode", short_empty_elements=True
    )


def _find_material_by_hash(content_hash: str) -> dict[str, Any] | None:
    with _get_session() as session:
        row = session.scalar(
            select(EyunMediaMaterialModel).where(
                EyunMediaMaterialModel.content_hash == content_hash
            )
        )
        return _material_to_dict(row) if row is not None else None


def _reserve_generated_material(
    *, fingerprint: str, media_type: str, preview_url: str
) -> bool:
    now = utcnow()
    with _get_session() as session:
        row = session.scalar(
            select(EyunMediaMaterialModel).where(
                EyunMediaMaterialModel.content_hash == fingerprint
            )
        )
        if row is None:
            row = EyunMediaMaterialModel(
                name=f"自动生成的微信{'图片' if media_type == 'image' else '视频'}素材",
                media_type=media_type,
                content_hash=fingerprint,
                raw_xml="",
                preview_url=preview_url,
                status="creating",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False
        if row.status == "creating":
            updated_at = row.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if now - updated_at < timedelta(minutes=2):
                return False
        row.status = "creating"
        row.last_error = None
        row.preview_url = preview_url
        row.updated_at = now
        session.commit()
        return True


async def _wait_for_generated_material(fingerprint: str) -> dict[str, Any]:
    for _ in range(120):
        await asyncio.sleep(0.25)
        row = _find_material_by_hash(fingerprint)
        if row is not None and row["status"] == "ready":
            return get_ready_material(int(row["id"]))
        if row is not None and row["status"] == "failed":
            raise RuntimeError(str(row.get("last_error") or "material creation failed"))
    raise TimeoutError("timed out waiting for Eyun material creation")


def _complete_generated_material(
    *, fingerprint: str, raw_xml: str, preview_url: str
) -> dict[str, Any]:
    now = utcnow()
    with _get_session() as session:
        row = session.scalar(
            select(EyunMediaMaterialModel).where(
                EyunMediaMaterialModel.content_hash == fingerprint
            )
        )
        if row is None:
            raise RuntimeError("reserved Eyun material disappeared")
        row.raw_xml = raw_xml
        row.preview_url = preview_url
        row.status = "ready"
        row.last_error = None
        row.last_verified_at = now
        row.updated_at = now
        session.execute(
            update(EyunOutboundMessageModel)
            .where(
                EyunOutboundMessageModel.material_id == row.id,
                EyunOutboundMessageModel.status == "waiting_material",
            )
            .values(status="queued", due_at=now, last_error=None, updated_at=now)
        )
        session.commit()
        session.refresh(row)
        return _material_to_dict(row, include_xml=True)


def _fail_generated_material(fingerprint: str, error: str) -> None:
    with _get_session() as session:
        row = session.scalar(
            select(EyunMediaMaterialModel).where(
                EyunMediaMaterialModel.content_hash == fingerprint
            )
        )
        if row is None:
            return
        row.status = "failed"
        row.last_error = error
        row.updated_at = utcnow()
        session.commit()


def capture_eyun_material(
    *,
    media_type: str,
    raw_xml: str,
    preview_url: str | None = None,
    source_w_id: str | None = None,
    source_wc_id: str | None = None,
    source_message_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    media_type = media_type.strip().lower()
    raw_xml = raw_xml.strip()
    if media_type not in {"image", "video"} or not raw_xml:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="只能保存包含原始 XML 的图片或视频素材",
            status_code=422,
        )
    digest = hashlib.sha256(raw_xml.encode("utf-8")).hexdigest()
    now = utcnow()
    with _get_session() as session:
        row = session.scalar(
            select(EyunMediaMaterialModel).where(
                EyunMediaMaterialModel.content_hash == digest
            )
        )
        if row is None:
            row = EyunMediaMaterialModel(
                name=(name or f"微信{'图片' if media_type == 'image' else '视频'}素材").strip(),
                media_type=media_type,
                content_hash=digest,
                raw_xml=raw_xml,
                preview_url=(preview_url or "").strip() or None,
                source_w_id=(source_w_id or "").strip() or None,
                source_wc_id=(source_wc_id or "").strip() or None,
                source_message_id=(source_message_id or "").strip() or None,
                status="ready",
                last_verified_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            if preview_url:
                row.preview_url = preview_url.strip()
            if name:
                row.name = name.strip()
            row.status = "ready"
            row.last_error = None
            row.last_verified_at = now
            row.updated_at = now
        session.flush()
        session.execute(
            update(EyunOutboundMessageModel)
            .where(
                EyunOutboundMessageModel.material_id == row.id,
                EyunOutboundMessageModel.status == "waiting_material",
            )
            .values(status="queued", due_at=now, last_error=None, updated_at=now)
        )
        session.commit()
        session.refresh(row)
        return _material_to_dict(row)


def capture_material_group_message(payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any] | None:
    settings = get_settings()
    material_group = settings.eyun_material_group_wc_id.strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    from_group = str(data.get("fromGroup") or "").strip()
    if not material_group or from_group != material_group:
        return None
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    media_type = str(media.get("type") or "").strip()
    if media_type not in {"image", "video"}:
        return None
    return capture_eyun_material(
        media_type=media_type,
        raw_xml=str(metadata.get("raw_content") or data.get("content") or ""),
        preview_url=str(media.get("url") or "") or None,
        source_w_id=str(data.get("wId") or payload.get("wId") or "") or None,
        source_wc_id=from_group,
        source_message_id=str(data.get("newMsgId") or data.get("msgId") or "") or None,
    )


def create_material_from_conversation_message(
    conversation_message_id: int, name: str | None = None
) -> dict[str, Any]:
    with _get_session() as session:
        message = session.get(ConversationMessageModel, conversation_message_id)
        if message is None:
            raise AppError(ErrorCode.REQUEST_INVALID, message="会话消息不存在", status_code=404)
        try:
            metadata = json.loads(message.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    return capture_eyun_material(
        media_type=str(media.get("type") or ""),
        raw_xml=str(metadata.get("raw_content") or ""),
        preview_url=str(media.get("url") or "") or None,
        source_w_id=str(metadata.get("w_id") or "") or None,
        source_wc_id=str(metadata.get("from_group") or metadata.get("from_user") or "") or None,
        source_message_id=str(metadata.get("provider_msg_id") or metadata.get("message_id") or "") or None,
        name=name,
    )


def get_ready_material(material_id: int) -> dict[str, Any]:
    with _get_session() as session:
        row = session.get(EyunMediaMaterialModel, material_id)
        if row is None:
            raise AppError(ErrorCode.REQUEST_INVALID, message="微信素材不存在", status_code=404)
        if row.status != "ready":
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message=f"微信素材当前不可用：{row.status}",
                status_code=409,
            )
        return _material_to_dict(row, include_xml=True)


def list_materials(
    *, page: int = 1, page_size: int = 50, status: str | None = None, keyword: str = ""
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    with _get_session() as session:
        query = select(EyunMediaMaterialModel)
        count_query = select(func.count()).select_from(EyunMediaMaterialModel)
        if status:
            query = query.where(EyunMediaMaterialModel.status == status)
            count_query = count_query.where(EyunMediaMaterialModel.status == status)
        if keyword.strip():
            pattern = f"%{keyword.strip()}%"
            query = query.where(EyunMediaMaterialModel.name.ilike(pattern))
            count_query = count_query.where(EyunMediaMaterialModel.name.ilike(pattern))
        total = int(session.scalar(count_query) or 0)
        rows = session.scalars(
            query.order_by(EyunMediaMaterialModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {"items": [_material_to_dict(row) for row in rows], "total": total}


def update_material(material_id: int, *, name: str, status: str) -> dict[str, Any]:
    with _get_session() as session:
        row = session.get(EyunMediaMaterialModel, material_id)
        if row is None:
            raise AppError(ErrorCode.REQUEST_INVALID, message="微信素材不存在", status_code=404)
        row.name = name.strip()
        row.status = status
        now = utcnow()
        row.updated_at = now
        if status == "ready":
            session.execute(
                update(EyunOutboundMessageModel)
                .where(
                    EyunOutboundMessageModel.material_id == row.id,
                    EyunOutboundMessageModel.status == "waiting_material",
                )
                .values(status="queued", due_at=now, last_error=None, updated_at=now)
            )
        session.commit()
        session.refresh(row)
        return _material_to_dict(row)


def mark_material_expired(material_id: int, error: str) -> None:
    with _get_session() as session:
        row = session.get(EyunMediaMaterialModel, material_id)
        if row is None:
            return
        row.status = "expired"
        row.last_error = error
        row.updated_at = utcnow()
        session.commit()


def list_bulk_jobs(*, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    with _get_session() as session:
        total = int(session.scalar(select(func.count()).select_from(EyunBulkSendJobModel)) or 0)
        jobs = session.scalars(
            select(EyunBulkSendJobModel)
            .order_by(EyunBulkSendJobModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = []
        for job in jobs:
            statuses = dict(
                session.execute(
                    select(EyunOutboundMessageModel.status, func.count())
                    .where(EyunOutboundMessageModel.bulk_job_id == job.id)
                    .group_by(EyunOutboundMessageModel.status)
                ).all()
            )
            sent = int(statuses.get("sent", 0))
            failed = int(statuses.get("failed", 0))
            cancelled = int(statuses.get("cancelled", 0))
            completed = sent + failed + cancelled
            status_value = "completed" if completed >= job.total_count else "sending" if sent else job.status
            items.append(
                {
                    "id": job.id,
                    "source_type": job.source_type,
                    "source_id": job.source_id,
                    "w_id": job.w_id,
                    "status": status_value,
                    "total_count": job.total_count,
                    "queued_count": job.total_count - completed,
                    "sent_count": sent,
                    "failed_count": failed + cancelled,
                    "created_by": job.created_by,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                }
            )
        return {"items": items, "total": total}


def _material_to_dict(row: EyunMediaMaterialModel, *, include_xml: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id,
        "name": row.name,
        "media_type": row.media_type,
        "preview_url": row.preview_url,
        "source_w_id": row.source_w_id,
        "source_wc_id": row.source_wc_id,
        "source_message_id": row.source_message_id,
        "status": row.status,
        "last_error": row.last_error,
        "last_verified_at": row.last_verified_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if include_xml:
        result["raw_xml"] = row.raw_xml
    return result


def _get_session() -> Session:
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ConversationMessageModel.__table__,
                EyunMediaMaterialModel.__table__,
                EyunBulkSendJobModel.__table__,
                EyunOutboundMessageModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()
