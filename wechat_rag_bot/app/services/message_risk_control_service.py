import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationModel,
    EyunInboundBatchModel,
    EyunInboundMessageModel,
    EyunOutboundMessageModel,
    EyunSendRateModel,
)
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat
from app.services.conversation_service import HUMAN_ACTIVE, RESOLVED, make_conversation_id


logger = logging.getLogger("wechat_rag_bot.eyun_risk_control")

_sessionmakers: dict[str, sessionmaker] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_eyun_batch_key(*, w_id: str, target_wc_id: str, from_user: str) -> str:
    return f"{w_id}:{target_wc_id or from_user}"


async def enqueue_eyun_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    now = utcnow()
    content = str(data.get("content") or "").strip()
    w_id = str(data.get("wId") or payload.get("wId") or settings.eyun_wid or "")
    from_user = str(data.get("fromUser") or "")
    from_group = str(data.get("fromGroup") or "")
    profile_user_id = str(payload.get("_profile_user_id") or "").strip() or None
    target_wc_id = from_group or from_user
    wc_id = str(payload.get("wcId") or data.get("toUser") or "")
    batch_key = build_eyun_batch_key(
        w_id=w_id, target_wc_id=target_wc_id, from_user=from_user
    )
    due_at = now + timedelta(seconds=settings.eyun_inbound_debounce_seconds)
    provider_message_id = _provider_message_id(payload, batch_key, now)

    with _get_session() as session:
        existing_message = session.scalar(
            select(EyunInboundMessageModel).where(
                EyunInboundMessageModel.provider_message_id == provider_message_id
            )
        )
        if existing_message is not None:
            batch = _get_batch(session, existing_message.batch_key)
            return _inbound_batch_to_dict(batch)

        batch = _get_batch(session, batch_key)
        if batch is None:
            batch = EyunInboundBatchModel(
                batch_key=batch_key,
                w_id=w_id or None,
                wc_id=wc_id,
                target_wc_id=target_wc_id,
                from_user=from_user or None,
                from_group=from_group or None,
                profile_user_id=profile_user_id,
                account=str(payload.get("account") or "") or None,
                message_type=str(payload.get("messageType") or ""),
                content=content,
                message_count=1,
                status="pending",
                due_at=due_at,
                created_at=now,
                updated_at=now,
            )
            session.add(batch)
        elif batch.status != "pending":
            batch.w_id = w_id or None
            batch.wc_id = wc_id
            batch.target_wc_id = target_wc_id
            batch.from_user = from_user or None
            batch.from_group = from_group or None
            batch.profile_user_id = profile_user_id
            batch.account = str(payload.get("account") or "") or None
            batch.message_type = str(payload.get("messageType") or "")
            batch.content = content
            batch.message_count = 1
            batch.status = "pending"
            batch.due_at = due_at
            batch.updated_at = now
        else:
            batch.content = f"{batch.content}\n{content}" if batch.content else content
            batch.message_count = (batch.message_count or 0) + 1
            batch.profile_user_id = profile_user_id or batch.profile_user_id
            batch.due_at = due_at
            batch.updated_at = now

        session.add(
            EyunInboundMessageModel(
                provider_message_id=provider_message_id,
                batch_key=batch_key,
                content=content,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
            )
        )
        session.commit()
        session.refresh(batch)
        return _inbound_batch_to_dict(batch)


async def process_due_eyun_inbound_batches(limit: int = 10) -> int:
    now = utcnow()
    with _get_session() as session:
        rows = session.scalars(
            select(EyunInboundBatchModel)
            .where(
                EyunInboundBatchModel.status == "pending",
                EyunInboundBatchModel.due_at <= now,
            )
            .order_by(EyunInboundBatchModel.due_at.asc(), EyunInboundBatchModel.id.asc())
            .limit(limit)
        ).all()
        for row in rows:
            row.status = "processing"
            row.updated_at = now
        session.commit()
        batch_ids = [row.id for row in rows]

    attempted = 0
    for batch_id in batch_ids:
        attempted += 1
        await _process_inbound_batch(batch_id)
    return attempted


def random_reply_delay_seconds() -> int:
    settings = get_settings()
    return random.randint(
        settings.eyun_reply_jitter_min_seconds,
        settings.eyun_reply_jitter_max_seconds,
    )


async def enqueue_eyun_outbound(
    *, w_id: str, wc_id: str, content: str, source_batch_key: str | None
) -> dict[str, Any]:
    now = utcnow()
    row = EyunOutboundMessageModel(
        w_id=w_id,
        wc_id=wc_id,
        content=content,
        source_batch_key=source_batch_key,
        status="queued",
        due_at=now + timedelta(seconds=random_reply_delay_seconds()),
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    with _get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return _outbound_to_dict(row)


async def process_due_eyun_outbound_messages(limit: int = 5) -> int:
    now = utcnow()
    attempted = 0
    with _get_session() as session:
        rows = session.scalars(
            select(EyunOutboundMessageModel)
            .where(
                EyunOutboundMessageModel.status == "queued",
                EyunOutboundMessageModel.due_at <= now,
            )
            .order_by(EyunOutboundMessageModel.due_at.asc(), EyunOutboundMessageModel.id.asc())
            .limit(limit)
        ).all()
        for row in rows:
            rate = session.get(EyunSendRateModel, row.w_id)
            allowed_at = _next_allowed_send_at(rate.last_sent_at if rate else None)
            if allowed_at > now:
                row.due_at = allowed_at + timedelta(milliseconds=random.randint(0, 1000))
                row.updated_at = now
                continue

            row.status = "sending"
            row.updated_at = now
            session.commit()
            attempted += 1
            try:
                from app.services.eyun_callback_service import send_eyun_text

                await send_eyun_text(w_id=row.w_id, wc_id=row.wc_id, content=row.content)
            except Exception as exc:  # noqa: BLE001
                row.status = "queued"
                row.attempts = (row.attempts or 0) + 1
                row.last_error = str(exc)
                row.due_at = utcnow() + timedelta(seconds=30)
                row.updated_at = utcnow()
                session.commit()
                logger.warning("Eyun outbound send failed: %s", exc)
                continue

            sent_at = utcnow()
            row.status = "sent"
            row.updated_at = sent_at
            if rate is None:
                rate = EyunSendRateModel(
                    w_id=row.w_id, last_sent_at=sent_at, updated_at=sent_at
                )
                session.add(rate)
            else:
                rate.last_sent_at = sent_at
                rate.updated_at = sent_at
            session.commit()
            break
    return attempted


async def eyun_worker_tick() -> None:
    await process_due_eyun_inbound_batches(limit=10)
    await process_due_eyun_outbound_messages(limit=5)


async def eyun_risk_control_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await eyun_worker_tick()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eyun risk-control worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.eyun_worker_poll_seconds)
        except asyncio.TimeoutError:
            pass


async def _process_inbound_batch(batch_id: int) -> None:
    with _get_session() as session:
        batch = session.get(EyunInboundBatchModel, batch_id)
        if batch is None:
            return
        batch_data = _inbound_batch_to_dict(batch)
        customer_snapshot = _latest_customer_snapshot(session, batch_data["batch_key"])

    try:
        if batch_data["from_group"]:
            _mark_batch(batch_id, "skipped")
            return

        if _conversation_blocks_ai(batch_data):
            _mark_batch(batch_id, "skipped")
            return

        chat_result = await handle_chat(
            ChatRequest(
                channel="wechat",
                user_id=(
                    batch_data["profile_user_id"]
                    or batch_data["from_user"]
                    or batch_data["target_wc_id"]
                ),
                session_id=batch_data["from_group"],
                message=batch_data["content"],
                kb_id=get_settings().wechat_default_kb_id,
                metadata={
                    "provider": "eyun",
                    "account": batch_data["account"],
                    "message_type": batch_data["message_type"],
                    "wc_id": batch_data["wc_id"],
                    "w_id": batch_data["w_id"],
                    "from_user": batch_data["from_user"],
                    "from_group": batch_data["from_group"],
                    "batch_key": batch_data["batch_key"],
                    "message_count": batch_data["message_count"],
                    **customer_snapshot,
                },
            )
        )
        if batch_data["w_id"] and batch_data["target_wc_id"]:
            for answer in _answer_segments(chat_result):
                await enqueue_eyun_outbound(
                    w_id=batch_data["w_id"],
                    wc_id=batch_data["target_wc_id"],
                    content=answer,
                    source_batch_key=batch_data["batch_key"],
                )
        _mark_batch(batch_id, "processed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun inbound batch processing failed: %s", exc)
        _mark_batch(batch_id, "failed")


def _conversation_blocks_ai(batch: dict[str, Any]) -> bool:
    conversation_id = make_conversation_id(
        "wechat",
        batch["profile_user_id"] or batch["from_user"] or batch["target_wc_id"],
        batch["from_group"],
    )
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        return bool(conversation and conversation.status in {HUMAN_ACTIVE, RESOLVED})


def _answer_segments(chat_result: dict[str, Any]) -> list[str]:
    segments = chat_result.get("answer_segments")
    if isinstance(segments, list):
        return [str(segment).strip() for segment in segments if str(segment).strip()]
    answer = str(chat_result.get("answer") or "").strip()
    return [answer] if answer else []

def _mark_batch(batch_id: int, status: str) -> None:
    with _get_session() as session:
        batch = session.get(EyunInboundBatchModel, batch_id)
        if batch is None:
            return
        batch.status = status
        batch.updated_at = utcnow()
        session.commit()


def _next_allowed_send_at(last_sent_at: datetime | None) -> datetime:
    if last_sent_at is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _ensure_aware(last_sent_at) + timedelta(
        seconds=get_settings().eyun_send_min_interval_seconds
    )


def _provider_message_id(
    payload: dict[str, Any], batch_key: str, now: datetime
) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_id = data.get("newMsgId") or data.get("msgId")
    if raw_id:
        return f"{batch_key}:{raw_id}"
    content = str(data.get("content") or "")
    return f"{batch_key}:{now.timestamp()}:{hash(content)}"


def _latest_customer_snapshot(session: Session, batch_key: str) -> dict[str, str]:
    row = session.scalar(
        select(EyunInboundMessageModel)
        .where(EyunInboundMessageModel.batch_key == batch_key)
        .order_by(EyunInboundMessageModel.created_at.desc(), EyunInboundMessageModel.id.desc())
        .limit(1)
    )
    if row is None:
        return {}
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        return {}

    display_name = _payload_text(
        payload,
        (
            "remark_name",
            "remark",
            "display_name",
            "nickname",
            "nick_name",
            "from_user_name",
            "fromUserName",
            "sender_name",
            "alias",
        ),
    )
    avatar_url = _payload_text(
        payload,
        ("avatar_url", "avatar", "headimgurl", "head_img_url", "head_url"),
    )
    snapshot = {}
    if display_name:
        snapshot["remark_name"] = display_name
    if avatar_url:
        snapshot["avatar_url"] = avatar_url
    return snapshot


def _payload_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    sources = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        sources.append(data)
        for nested_key in ("user", "customer", "contact", "profile"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
    for nested_key in ("user", "customer", "contact", "profile"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _get_session() -> Session:
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ConversationModel.__table__,
                EyunInboundBatchModel.__table__,
                EyunInboundMessageModel.__table__,
                EyunOutboundMessageModel.__table__,
                EyunSendRateModel.__table__,
            ],
        )
        _ensure_profile_user_id_column(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _ensure_profile_user_id_column(engine) -> None:
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("eyun_inbound_batches")
    }
    if "profile_user_id" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE eyun_inbound_batches "
                "ADD COLUMN profile_user_id VARCHAR(256)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_eyun_inbound_batches_profile_user_id "
                "ON eyun_inbound_batches (profile_user_id)"
            )
        )


def _get_batch(session: Session, batch_key: str) -> EyunInboundBatchModel | None:
    return session.scalar(
        select(EyunInboundBatchModel).where(
            EyunInboundBatchModel.batch_key == batch_key
        )
    )


def _inbound_batch_to_dict(row: EyunInboundBatchModel | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "batch_key": row.batch_key,
        "w_id": row.w_id,
        "wc_id": row.wc_id,
        "target_wc_id": row.target_wc_id,
        "from_user": row.from_user,
        "from_group": row.from_group,
        "profile_user_id": row.profile_user_id,
        "account": row.account,
        "message_type": row.message_type,
        "content": row.content,
        "message_count": row.message_count,
        "status": row.status,
        "due_at": _ensure_aware(row.due_at),
        "created_at": _ensure_aware(row.created_at),
        "updated_at": _ensure_aware(row.updated_at),
    }


def _outbound_to_dict(row: EyunOutboundMessageModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "w_id": row.w_id,
        "wc_id": row.wc_id,
        "content": row.content,
        "source_batch_key": row.source_batch_key,
        "status": row.status,
        "due_at": _ensure_aware(row.due_at),
        "attempts": row.attempts,
        "last_error": row.last_error,
    }


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
