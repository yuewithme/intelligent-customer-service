from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMessageModel,
    EyunBulkSendJobModel,
    EyunMediaMaterialModel,
    EyunOutboundMessageModel,
)
from app.schemas.common import AppError, ErrorCode


_sessionmakers: dict[str, sessionmaker] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
