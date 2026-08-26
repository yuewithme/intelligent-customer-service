import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

from sqlalchemy import create_engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domains.conversations.services.conversation_event_service import (
    conversation_event_broker,
)
from app.infrastructure.database.models import (
    Base,
    ConversationMessageModel,
    EyunInboundMediaJobModel,
)


logger = logging.getLogger("wechat_rag_bot.eyun_inbound_media")

SUPPORTED_MEDIA_TYPES = {"video", "audio"}
MAX_ATTEMPTS = 3
STALE_PROCESSING_MINUTES = 5

_sessionmakers: dict[str, sessionmaker] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_eyun_inbound_media(
    *, conversation_id: str, message_id: str
) -> dict[str, Any] | None:
    now = utcnow()
    with _get_session() as session:
        message = session.scalar(
            select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == conversation_id,
                ConversationMessageModel.message_id == message_id,
            )
        )
        if message is None:
            logger.warning(
                "Eyun media message was not found conversation=%s message=%s",
                conversation_id,
                message_id,
            )
            return None
        media_type, payload = _job_payload_from_message(message)
        if media_type not in SUPPORTED_MEDIA_TYPES:
            return None

        dedup_key = f"{message.id}:{media_type}"
        job = session.scalar(
            select(EyunInboundMediaJobModel).where(
                EyunInboundMediaJobModel.dedup_key == dedup_key
            )
        )
        if job is None:
            job = EyunInboundMediaJobModel(
                conversation_message_id=message.id,
                dedup_key=dedup_key,
                media_type=media_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            _update_message_media(
                message,
                media_type=media_type,
                resolve_status="pending",
                resolve_error=None,
                job_key=dedup_key,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                job = session.scalar(
                    select(EyunInboundMediaJobModel).where(
                        EyunInboundMediaJobModel.dedup_key == dedup_key
                    )
                )
                if job is None:
                    raise
            _publish_media_change(message.conversation_id)
        return _job_to_dict(job)


async def retry_eyun_message_media(message_id: int) -> None:
    now = utcnow()
    with _get_session() as session:
        message = session.get(ConversationMessageModel, message_id)
        if message is None:
            raise ValueError("消息不存在")
        media_type, payload = _job_payload_from_message(message)
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("当前消息不支持媒体解析")

        dedup_key = f"{message.id}:{media_type}"
        job = session.scalar(
            select(EyunInboundMediaJobModel).where(
                EyunInboundMediaJobModel.dedup_key == dedup_key
            )
        )
        if job is None:
            job = EyunInboundMediaJobModel(
                conversation_message_id=message.id,
                dedup_key=dedup_key,
                media_type=media_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
            )
            session.add(job)
        job.payload_json = json.dumps(payload, ensure_ascii=False)
        job.status = "pending"
        job.attempts = 0
        job.available_at = now
        job.locked_at = None
        job.last_error = None
        job.updated_at = now
        job.completed_at = None
        _update_message_media(
            message,
            media_type=media_type,
            resolve_status="pending",
            resolve_error=None,
            clear_url=True,
            job_key=dedup_key,
        )
        conversation_id = message.conversation_id
        session.commit()
    _publish_media_change(conversation_id)


async def process_due_eyun_media_jobs(limit: int = 2) -> int:
    now = utcnow()
    stale_before = now - timedelta(minutes=STALE_PROCESSING_MINUTES)
    with _get_session() as session:
        session.execute(
            update(EyunInboundMediaJobModel)
            .where(
                EyunInboundMediaJobModel.status == "processing",
                or_(
                    EyunInboundMediaJobModel.locked_at.is_(None),
                    EyunInboundMediaJobModel.locked_at < stale_before,
                ),
            )
            .values(status="pending", locked_at=None, available_at=now, updated_at=now)
        )
        jobs = session.scalars(
            select(EyunInboundMediaJobModel)
            .where(
                EyunInboundMediaJobModel.status == "pending",
                EyunInboundMediaJobModel.available_at <= now,
            )
            .order_by(
                EyunInboundMediaJobModel.available_at.asc(),
                EyunInboundMediaJobModel.id.asc(),
            )
            .limit(limit)
        ).all()
        job_ids: list[int] = []
        conversation_ids: list[str] = []
        for job in jobs:
            job.status = "processing"
            job.attempts = (job.attempts or 0) + 1
            job.locked_at = now
            job.updated_at = now
            message = session.get(
                ConversationMessageModel,
                job.conversation_message_id,
            )
            if message is not None:
                _update_message_media(
                    message,
                    media_type=job.media_type,
                    resolve_status="processing",
                    resolve_error=None,
                    job_key=job.dedup_key,
                )
                conversation_ids.append(message.conversation_id)
            job_ids.append(job.id)
        session.commit()

    for conversation_id in conversation_ids:
        _publish_media_change(conversation_id)
    for job_id in job_ids:
        await _process_media_job(job_id)
    return len(job_ids)


async def eyun_inbound_media_worker(stop_event: asyncio.Event) -> None:
    poll_seconds = get_settings().eyun_worker_poll_seconds
    while not stop_event.is_set():
        try:
            await process_due_eyun_media_jobs()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eyun inbound media worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass


async def _process_media_job(job_id: int) -> None:
    with _get_session() as session:
        job = session.get(EyunInboundMediaJobModel, job_id)
        if job is None or job.status != "processing":
            return
        payload = _load_json(job.payload_json)
        media_type = job.media_type

    try:
        from app.integrations.eyun.services.eyun_callback_service import (
            download_eyun_video,
            download_eyun_voice,
        )

        if media_type == "video":
            url = await download_eyun_video(
                w_id=str(payload.get("w_id") or ""),
                msg_id=str(payload.get("provider_msg_id") or ""),
                content=str(payload.get("raw_content") or ""),
            )
        elif media_type == "audio":
            url = await download_eyun_voice(
                w_id=str(payload.get("w_id") or ""),
                msg_id=str(payload.get("provider_msg_id") or ""),
                from_user=str(payload.get("from_user") or ""),
                buf_id=str(payload.get("buf_id") or ""),
                length=_positive_int(payload.get("length")),
            )
        else:
            raise RuntimeError(f"unsupported Eyun media type: {media_type}")
    except Exception as exc:  # noqa: BLE001
        _fail_media_job(job_id, exc)
        return

    _complete_media_job(job_id, url)


def _complete_media_job(job_id: int, url: str) -> None:
    now = utcnow()
    with _get_session() as session:
        job = session.get(EyunInboundMediaJobModel, job_id)
        if job is None:
            return
        job.status = "succeeded"
        job.locked_at = None
        job.last_error = None
        job.updated_at = now
        job.completed_at = now
        message = session.get(ConversationMessageModel, job.conversation_message_id)
        conversation_id = ""
        if message is not None:
            _update_message_media(
                message,
                media_type=job.media_type,
                resolve_status="succeeded",
                resolve_error=None,
                url=url,
                job_key=job.dedup_key,
            )
            conversation_id = message.conversation_id
        session.commit()
    if conversation_id:
        _publish_media_change(conversation_id)


def _fail_media_job(job_id: int, error: Exception) -> None:
    now = utcnow()
    error_text = str(error).strip()[:1000] or error.__class__.__name__
    with _get_session() as session:
        job = session.get(EyunInboundMediaJobModel, job_id)
        if job is None:
            return
        exhausted = job.attempts >= MAX_ATTEMPTS
        attempts = job.attempts
        job.status = "failed" if exhausted else "pending"
        job.available_at = now + timedelta(seconds=min(30, 2 ** job.attempts))
        job.locked_at = None
        job.last_error = error_text
        job.updated_at = now
        message = session.get(ConversationMessageModel, job.conversation_message_id)
        conversation_id = ""
        if message is not None:
            _update_message_media(
                message,
                media_type=job.media_type,
                resolve_status="failed" if exhausted else "pending",
                resolve_error=error_text,
                job_key=job.dedup_key,
            )
            conversation_id = message.conversation_id
        session.commit()
    logger.warning(
        "Eyun inbound media job failed id=%s attempt=%s exhausted=%s error=%s",
        job_id,
        attempts,
        exhausted,
        error_text,
    )
    if conversation_id:
        _publish_media_change(conversation_id)


def _job_payload_from_message(
    message: ConversationMessageModel,
) -> tuple[str, dict[str, Any]]:
    metadata = _load_json(message.metadata_json)
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    message_type = str(metadata.get("message_type") or "")
    media_type = str(media.get("type") or "")
    if not media_type:
        media_type = {"60003": "video", "60004": "audio"}.get(message_type, "")
    raw_content = str(metadata.get("raw_content") or "")
    voice_xml = _voice_xml_metadata(raw_content) if media_type == "audio" else {}
    return media_type, {
        "w_id": str(metadata.get("w_id") or ""),
        "provider_msg_id": str(
            metadata.get("provider_msg_id") or metadata.get("message_id") or ""
        ),
        "from_user": str(metadata.get("from_user") or ""),
        "raw_content": raw_content,
        "buf_id": str(media.get("buf_id") or voice_xml.get("buf_id") or ""),
        "length": str(media.get("length") or voice_xml.get("length") or ""),
    }


def _voice_xml_metadata(content: str) -> dict[str, str]:
    if not content.lstrip().startswith("<"):
        return {}
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "voicemsg":
            continue
        return {
            "buf_id": str(element.attrib.get("bufid") or ""),
            "length": str(
                element.attrib.get("length")
                or element.attrib.get("silklength")
                or ""
            ),
        }
    return {}


def _update_message_media(
    message: ConversationMessageModel,
    *,
    media_type: str,
    resolve_status: str,
    resolve_error: str | None,
    url: str | None = None,
    clear_url: bool = False,
    job_key: str | None = None,
) -> None:
    metadata = _load_json(message.metadata_json)
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    media["type"] = media_type
    media["resolve_status"] = resolve_status
    if job_key:
        media["job_key"] = job_key
    if url:
        media["url"] = url
        media["fallback"] = False
        media.pop("original_url", None)
    elif clear_url:
        media.pop("url", None)
        media["fallback"] = True
    if resolve_error:
        media["resolve_error"] = resolve_error
    else:
        media.pop("resolve_error", None)
    metadata["media"] = media
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)


def _positive_int(value: Any) -> int:
    try:
        parsed = int(str(value or "0"))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _load_json(value: str | None) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def _publish_media_change(conversation_id: str) -> None:
    conversation_event_broker.publish(
        {
            "conversation_id": conversation_id,
            "reason": "media",
            "updated_at": utcnow().isoformat(),
        }
    )


def _job_to_dict(job: EyunInboundMediaJobModel) -> dict[str, Any]:
    return {
        "id": job.id,
        "conversation_message_id": job.conversation_message_id,
        "media_type": job.media_type,
        "status": job.status,
        "attempts": job.attempts,
        "available_at": job.available_at.isoformat(),
        "last_error": job.last_error,
    }


def _get_session() -> Session:
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[EyunInboundMediaJobModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()
