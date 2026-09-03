from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domains.catalog.services.agent_media_library_service import get_agent_media
from app.infrastructure.database.models import (
    Base,
    EyunOutboundMessageModel,
    VideoTouchLinkModel,
    VideoTouchOpenEventModel,
)


_SOURCE_TYPE = "video_touch_test"
_session_factories: dict[str, sessionmaker] = {}


async def enqueue_video_touch_test(
    *,
    material_ref: str,
    wc_id: str,
    w_id: str = "",
    title: str = "",
    description: str = "",
    public_base_url: str = "",
) -> dict[str, Any]:
    normalized_ref = str(material_ref or "").strip().removeprefix("material:")
    media = get_agent_media(normalized_ref)
    if media is None:
        raise ValueError("视频素材不存在")
    if media.get("format") != "video":
        raise ValueError("测试触达只支持视频素材")
    target_url = str(media.get("url") or "").strip()
    thumb_url = str(media.get("thumb_url") or "").strip()
    copy_text = str(media.get("copy_text") or "").strip()
    if not target_url or not thumb_url:
        raise ValueError("视频素材缺少播放地址或封面")
    if not copy_text:
        raise ValueError("视频素材缺少已审核文案")
    _validate_target_url(target_url)

    settings = get_settings()
    resolved_w_id = str(w_id or settings.eyun_wid or "").strip()
    resolved_wc_id = str(wc_id or "").strip()
    if not resolved_w_id:
        raise ValueError("易云微信实例 w_id 未配置")
    if not resolved_wc_id:
        raise ValueError("测试接收方 wc_id 不能为空")

    source_batch_key = f"{_SOURCE_TYPE}:{uuid4().hex}"
    delivery_key = f"{source_batch_key}:card"
    link = create_video_touch_link(
        delivery_key=delivery_key,
        w_id=resolved_w_id,
        wc_id=resolved_wc_id,
        material_ref=str(media.get("material_ref") or material_ref),
        target_url=target_url,
        thumb_url=thumb_url,
        title=str(title or media.get("title") or "视频资料").strip(),
        source_type=_SOURCE_TYPE,
        source_batch_key=source_batch_key,
        public_base_url=public_base_url,
    )
    card = {
        "title": link["title"],
        "url": link["tracking_url"],
        "description": str(description or "点击播放视频").strip(),
        "thumb_url": thumb_url,
    }

    from app.integrations.eyun.services.message_risk_control_service import (
        enqueue_wechat_outbound,
    )

    try:
        copy_outbound = await enqueue_wechat_outbound(
            w_id=resolved_w_id,
            wc_id=resolved_wc_id,
            content=copy_text,
            source_batch_key=source_batch_key,
            delivery_key=f"{source_batch_key}:copy",
            message_type="text",
            channel="wechat",
            user_id=resolved_wc_id,
            session_id="default",
            sender_type="system",
            sender_id=_SOURCE_TYPE,
            source_type=_SOURCE_TYPE,
            source_id=str(link["id"]),
            delivery_metadata={
                "message_role": "copy",
                "material_ref": link["material_ref"],
                "video_touch_link_id": link["id"],
            },
        )
        outbound = await enqueue_wechat_outbound(
            w_id=resolved_w_id,
            wc_id=resolved_wc_id,
            content=json.dumps(card, ensure_ascii=False),
            source_batch_key=source_batch_key,
            delivery_key=delivery_key,
            message_type="link_card",
            channel="wechat",
            user_id=resolved_wc_id,
            session_id="default",
            sender_type="system",
            sender_id=_SOURCE_TYPE,
            source_type=_SOURCE_TYPE,
            source_id=str(link["id"]),
            depends_on_outbound_id=int(copy_outbound["id"]),
            delivery_metadata={
                "message_role": "video_card",
                "material_ref": link["material_ref"],
                "video_touch_link_id": link["id"],
            },
        )
    except Exception as exc:
        mark_video_touch_enqueue_failed(link["id"], str(exc))
        raise
    bind_video_touch_delivery(
        link["id"],
        outbound_message_id=int(outbound["id"]),
        conversation_message_id=int(outbound["conversation_message_id"]),
    )
    result = get_video_touch_test(link["id"])
    if result is None:
        raise RuntimeError("视频触达测试记录创建失败")
    result["copy_outbound_message_id"] = int(copy_outbound["id"])
    result["copy_conversation_message_id"] = int(
        copy_outbound["conversation_message_id"]
    )
    return result


def create_video_touch_link(
    *,
    delivery_key: str,
    w_id: str,
    wc_id: str,
    material_ref: str,
    target_url: str,
    thumb_url: str,
    title: str,
    source_type: str,
    source_batch_key: str,
    public_base_url: str = "",
) -> dict[str, Any]:
    resolved_public_base_url = _public_base_url(public_base_url)
    with _session() as session:
        existing = session.scalar(
            select(VideoTouchLinkModel).where(
                VideoTouchLinkModel.delivery_key == delivery_key
            )
        )
        if existing is not None:
            return _link_to_dict(existing)
        now = _utcnow()
        row = VideoTouchLinkModel(
            token=secrets.token_urlsafe(24),
            delivery_key=delivery_key,
            w_id=w_id,
            wc_id=wc_id,
            material_ref=material_ref,
            public_base_url=resolved_public_base_url,
            target_url=target_url,
            thumb_url=thumb_url,
            title=title[:256],
            source_type=source_type,
            source_batch_key=source_batch_key,
            open_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _link_to_dict(row)


def bind_video_touch_delivery(
    link_id: int,
    *,
    outbound_message_id: int,
    conversation_message_id: int,
) -> None:
    with _session() as session:
        row = session.get(VideoTouchLinkModel, link_id)
        if row is None:
            raise LookupError("video touch link not found")
        row.outbound_message_id = outbound_message_id
        row.conversation_message_id = conversation_message_id
        row.last_error = None
        row.updated_at = _utcnow()
        session.commit()


def mark_video_touch_enqueue_failed(link_id: int, error: str) -> None:
    with _session() as session:
        row = session.get(VideoTouchLinkModel, link_id)
        if row is None:
            return
        row.last_error = str(error)[:4000]
        row.updated_at = _utcnow()
        session.commit()


def get_video_touch_landing(token: str) -> dict[str, Any] | None:
    with _session() as session:
        row = session.scalar(
            select(VideoTouchLinkModel).where(VideoTouchLinkModel.token == token)
        )
        if row is None:
            return None
        return {
            "id": row.id,
            "token": row.token,
            "title": row.title,
            "thumb_url": row.thumb_url,
        }


def record_video_touch_open(
    *, token: str, play_session: str, user_agent: str = ""
) -> str | None:
    if len(play_session) < 16:
        return None
    session_hash = hashlib.sha256(play_session.encode("utf-8")).hexdigest()
    with _session() as session:
        row = session.scalar(
            select(VideoTouchLinkModel).where(VideoTouchLinkModel.token == token)
        )
        if row is None:
            return None
        target_url = row.target_url
        existing = session.scalar(
            select(VideoTouchOpenEventModel.id).where(
                VideoTouchOpenEventModel.video_touch_link_id == row.id,
                VideoTouchOpenEventModel.session_hash == session_hash,
            )
        )
        if existing is not None:
            return target_url
        now = _utcnow()
        session.add(
            VideoTouchOpenEventModel(
                video_touch_link_id=row.id,
                session_hash=session_hash,
                user_agent=str(user_agent or "")[:512] or None,
                created_at=now,
            )
        )
        row.open_count = int(row.open_count or 0) + 1
        row.first_opened_at = row.first_opened_at or now
        row.last_opened_at = now
        row.updated_at = now
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        return target_url


def get_video_touch_test(link_id: int) -> dict[str, Any] | None:
    with _session() as session:
        row = session.get(VideoTouchLinkModel, link_id)
        if row is None or row.source_type != _SOURCE_TYPE:
            return None
        outbound = (
            session.get(EyunOutboundMessageModel, row.outbound_message_id)
            if row.outbound_message_id
            else None
        )
        return _test_to_dict(row, outbound)


def list_video_touch_tests(*, limit: int = 50) -> dict[str, Any]:
    with _session() as session:
        total, opened, open_count = session.execute(
            select(
                func.count(VideoTouchLinkModel.id),
                func.count(VideoTouchLinkModel.first_opened_at),
                func.coalesce(func.sum(VideoTouchLinkModel.open_count), 0),
            ).where(VideoTouchLinkModel.source_type == _SOURCE_TYPE)
        ).one()
        rows = list(
            session.scalars(
                select(VideoTouchLinkModel)
                .where(VideoTouchLinkModel.source_type == _SOURCE_TYPE)
                .order_by(VideoTouchLinkModel.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        )
        outbound_ids = [
            row.outbound_message_id for row in rows if row.outbound_message_id
        ]
        outbound_by_id = {
            row.id: row
            for row in (
                session.scalars(
                    select(EyunOutboundMessageModel).where(
                        EyunOutboundMessageModel.id.in_(outbound_ids)
                    )
                )
                if outbound_ids
                else []
            )
        }
        items = [
            _test_to_dict(row, outbound_by_id.get(row.outbound_message_id))
            for row in rows
        ]
    return {
        "total": int(total),
        "opened": int(opened),
        "unique_open_rate": round(int(opened) / int(total), 4) if total else 0.0,
        "open_count": int(open_count),
        "items": items,
    }


def reset_video_touch_storage_cache() -> None:
    _session_factories.clear()


def _session() -> Session:
    settings = get_settings()
    url = settings.chat_log_db_url
    factory = _session_factories.get(url)
    if factory is None:
        from app.integrations.eyun.services.message_risk_control_service import (
            ensure_eyun_delivery_storage,
        )

        ensure_eyun_delivery_storage()
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[
                VideoTouchLinkModel.__table__,
                VideoTouchOpenEventModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _session_factories[url] = factory
    return factory()


def _public_base_url(override: str = "") -> str:
    settings = get_settings()
    configured = str(settings.video_touch_public_base_url or "").strip()
    if settings.app_env == "prod":
        value = configured or "https://sales-agent.hzwohu.com"
    else:
        value = str(
            override or configured or settings.app_public_base_url or ""
        ).strip()
    value = value.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("VIDEO_TOUCH_PUBLIC_BASE_URL 未配置为可用的 HTTP(S) 地址")
    if parsed.hostname == "sales-agent.hzwohu.com":
        value = f"https://{parsed.netloc}"
    return value


def _validate_target_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("视频播放地址无效")


def _link_to_dict(row: VideoTouchLinkModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "token": row.token,
        "tracking_url": f"{row.public_base_url.rstrip('/')}/v/{row.token}",
        "material_ref": row.material_ref,
        "title": row.title,
        "target_url": row.target_url,
    }


def _test_to_dict(
    row: VideoTouchLinkModel, outbound: EyunOutboundMessageModel | None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "tracking_url": f"{row.public_base_url.rstrip('/')}/v/{row.token}",
        "wc_id": row.wc_id,
        "material_ref": row.material_ref,
        "title": row.title,
        "source_batch_key": row.source_batch_key,
        "outbound_message_id": row.outbound_message_id,
        "conversation_message_id": row.conversation_message_id,
        "delivery_status": outbound.status if outbound is not None else "not_queued",
        "delivery_error": (
            outbound.last_error if outbound is not None else row.last_error
        ),
        "opened": bool(row.first_opened_at),
        "first_opened_at": _iso(row.first_opened_at),
        "last_opened_at": _iso(row.last_opened_at),
        "open_count": int(row.open_count or 0),
        "created_at": _iso(row.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
