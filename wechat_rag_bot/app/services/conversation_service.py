import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ConversationMessageModel, ConversationModel
from app.schemas.common import AppError, ErrorCode
from app.services.conversation_event_service import conversation_event_broker
from app.utils.ids import generate_id


AI_ACTIVE = "ai_active"
AI_WAITING = "ai_waiting"
HANDOFF_PENDING = "handoff_pending"
HUMAN_ACTIVE = "human_active"
RESOLVED = "resolved"

_sessionmakers: dict[str, sessionmaker] = {}
_initialized_urls: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_conversation_id(channel: str, user_id: str, session_id: str | None) -> str:
    return f"{channel}:{user_id}:{session_id or 'default'}"


async def list_conversations(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    with _get_session() as session:
        filters = []
        if status:
            filters.append(ConversationModel.status == status)
        if owner_id:
            filters.append(ConversationModel.owner_id == owner_id)
        if keyword:
            filters.append(ConversationModel.last_message.like(f"%{keyword}%"))
        total = session.scalar(
            select(func.count()).select_from(ConversationModel).where(*filters)
        )
        rows = session.scalars(
            select(ConversationModel)
            .where(*filters)
            .order_by(ConversationModel.updated_at.desc(), ConversationModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {
        "items": [_conversation_to_dict(row) for row in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


async def get_conversation_detail(conversation_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        messages = session.scalars(
            select(ConversationMessageModel)
            .where(ConversationMessageModel.conversation_id == conversation_id)
            .order_by(
                ConversationMessageModel.created_at.asc(),
                ConversationMessageModel.id.asc(),
            )
        ).all()
    return {
        "conversation": _conversation_to_dict(conversation),
        "messages": [_message_to_dict(row) for row in messages],
    }


async def record_ai_turn(*, message, result: dict) -> None:
    conversation_id = make_conversation_id(
        message.channel, message.user_id, message.session_id
    )
    status = HANDOFF_PENDING if result.get("need_human") else AI_WAITING
    handoff = result.get("handoff") or {}
    intent = result.get("intent") or {}
    skip_customer_record = bool(message.metadata.get("skip_customer_record"))
    now = _now()
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if (
            not skip_customer_record
            and message.metadata.get("provider") == "eyun"
            and conversation is not None
        ):
            existing_customer_message = session.scalar(
                select(ConversationMessageModel.id)
                .where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.sender_type == "customer",
                )
                .limit(1)
            )
            skip_customer_record = existing_customer_message is not None
        if conversation is None:
            conversation = ConversationModel(
                conversation_id=conversation_id,
                channel=message.channel,
                user_id=message.user_id,
                user_display_name=_metadata_text(
                    message.metadata,
                    (
                        "remark_name",
                        "remark",
                        "display_name",
                        "nickname",
                        "nick_name",
                        "user_nickname",
                        "from_user_name",
                        "fromUserName",
                        "sender_name",
                        "alias",
                    ),
                ),
                user_avatar_url=_metadata_text(
                    message.metadata,
                    (
                        "avatar_url",
                        "avatar",
                        "headimgurl",
                        "head_img_url",
                        "head_url",
                    ),
                ),
                session_id=message.session_id,
                tenant_id=message.tenant_id,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            session.flush()
        else:
            display_name = _metadata_text(
                message.metadata,
                (
                    "remark_name",
                    "remark",
                    "display_name",
                    "nickname",
                    "nick_name",
                    "user_nickname",
                    "from_user_name",
                    "fromUserName",
                    "sender_name",
                    "alias",
                ),
            )
            avatar_url = _metadata_text(
                message.metadata,
                (
                    "avatar_url",
                    "avatar",
                    "headimgurl",
                    "head_img_url",
                    "head_url",
                ),
            )
            if display_name:
                conversation.user_display_name = display_name
            if avatar_url:
                conversation.user_avatar_url = avatar_url

        conversation.status = status
        conversation.owner_id = None
        conversation.last_message = message.message
        conversation.last_route = result.get("route")
        conversation.last_intent = intent.get("primary_intent")
        conversation.handoff_reason = handoff.get("reason")
        conversation.handoff_ticket_id = handoff.get("ticket_id")
        if not skip_customer_record:
            conversation.unread_count = (conversation.unread_count or 0) + 1
        conversation.updated_at = now

        if not skip_customer_record:
            session.add(
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    trace_id=message.trace_id,
                    message_id=message.message_id,
                    sender_type="customer",
                    sender_id=message.user_id,
                    content=message.message,
                    route=result.get("route"),
                    primary_intent=conversation.last_intent,
                    metadata_json=json.dumps(
                        {"channel": message.channel, "tenant_id": message.tenant_id},
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
            )
        answer_segments = _answer_segments(result)
        for answer in answer_segments:
            session.add(
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    trace_id=message.trace_id,
                    sender_type="ai",
                    sender_id="ai",
                    content=answer,
                    route=result.get("route"),
                    primary_intent=conversation.last_intent,
                    metadata_json=json.dumps(
                        {
                            "sources": result.get("sources", []),
                            "template": result.get("template", {}),
                        },
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
            )
        session.commit()
    _publish_change(conversation_id, "message")


def _answer_segments(result: dict) -> list[str]:
    segments = result.get("answer_segments")
    if isinstance(segments, list):
        return [str(segment).strip() for segment in segments if str(segment).strip()]
    answer = str(result.get("answer") or "").strip()
    return [answer] if answer else []


async def record_customer_message(
    *,
    channel: str,
    user_id: str,
    session_id: str | None,
    content: str,
    message_id: str | None = None,
    tenant_id: str | None = None,
    status: str = HANDOFF_PENDING,
    route: str | None = None,
    primary_intent: str | None = None,
    metadata: dict[str, Any] | None = None,
    handoff_reason: str | None = None,
) -> dict:
    metadata = metadata or {}
    tenant_id = tenant_id or "tenant_default"
    conversation_id = make_conversation_id(channel, user_id, session_id)
    now = _now()

    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if conversation is not None and message_id:
            existing_message = session.scalar(
                select(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.message_id == message_id,
                )
            )
            if existing_message is not None:
                return _conversation_to_dict(conversation)
        if conversation is None:
            conversation = ConversationModel(
                conversation_id=conversation_id,
                channel=channel,
                user_id=user_id,
                user_display_name=_metadata_text(
                    metadata,
                    (
                        "remark_name",
                        "remark",
                        "display_name",
                        "nickname",
                        "nick_name",
                        "user_nickname",
                        "from_user_name",
                        "fromUserName",
                        "sender_name",
                        "alias",
                    ),
                ),
                user_avatar_url=_metadata_text(
                    metadata,
                    (
                        "avatar_url",
                        "avatar",
                        "headimgurl",
                        "head_img_url",
                        "head_url",
                    ),
                ),
                session_id=session_id,
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            session.flush()
        else:
            display_name = _metadata_text(
                metadata,
                (
                    "remark_name",
                    "remark",
                    "display_name",
                    "nickname",
                    "nick_name",
                    "user_nickname",
                    "from_user_name",
                    "fromUserName",
                    "sender_name",
                    "alias",
                ),
            )
            avatar_url = _metadata_text(
                metadata,
                (
                    "avatar_url",
                    "avatar",
                    "headimgurl",
                    "head_img_url",
                    "head_url",
                ),
            )
            if display_name:
                conversation.user_display_name = display_name
            if avatar_url:
                conversation.user_avatar_url = avatar_url

        conversation.status = status
        conversation.owner_id = None
        conversation.last_message = content
        conversation.last_route = route
        conversation.last_intent = primary_intent
        conversation.handoff_reason = handoff_reason
        conversation.handoff_ticket_id = conversation.handoff_ticket_id or generate_id(
            "handoff"
        )
        conversation.unread_count = (conversation.unread_count or 0) + 1
        conversation.updated_at = now

        session.add(
            ConversationMessageModel(
                conversation_id=conversation_id,
                message_id=message_id,
                sender_type="customer",
                sender_id=user_id,
                content=content,
                route=route,
                primary_intent=primary_intent,
                metadata_json=json.dumps(
                    {"channel": channel, "tenant_id": tenant_id, **metadata},
                    ensure_ascii=False,
                ),
                created_at=now,
            )
        )
        session.commit()
        result = _conversation_to_dict(conversation)

    _publish_change(conversation_id, "message")
    return result


async def claim_conversation(conversation_id: str, operator_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HANDOFF_PENDING:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="只有待人工接管的会话可以领取",
                status_code=409,
            )
        conversation.status = HUMAN_ACTIVE
        conversation.owner_id = operator_id
        conversation.unread_count = 0
        conversation.updated_at = _now()
        session.commit()
        _publish_change(conversation_id, "claimed")
        return _conversation_to_dict(conversation)


async def reply_conversation(
    conversation_id: str, operator_id: str, content: str
) -> dict:
    content = content.strip()
    if not content:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="回复内容不能为空",
            status_code=400,
        )
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话未接管，不能人工回复",
                status_code=409,
            )
        eyun_target = _latest_eyun_reply_target(session, conversation)
        if eyun_target:
            try:
                from app.services.eyun_callback_service import send_eyun_text

                await send_eyun_text(
                    w_id=eyun_target["w_id"],
                    wc_id=eyun_target["wc_id"],
                    content=content,
                )
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    ErrorCode.WECHAT_REPLY_FAILED,
                    message="Eyun 消息发送失败，人工回复未写入本地会话",
                    status_code=502,
                ) from exc
        now = _now()
        session.add(
            ConversationMessageModel(
                conversation_id=conversation_id,
                sender_type="human",
                sender_id=operator_id,
                content=content,
                metadata_json="{}",
                created_at=now,
            )
        )
        conversation.last_message = content
        conversation.updated_at = now
        memory_context = {
            "user_id": conversation.user_id,
            "tenant_id": conversation.tenant_id,
            "session_id": conversation.session_id,
        }
        session.commit()
        from app.services.user_profile_service import append_conversation_memory

        await append_conversation_memory(
            user_id=memory_context["user_id"],
            tenant_id=memory_context["tenant_id"],
            session_id=memory_context["session_id"],
            role="human",
            content=content,
        )
        _publish_change(conversation_id, "reply")
        return _conversation_to_dict(conversation)


async def mark_conversation_read(conversation_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        changed = bool(conversation.unread_count)
        conversation.unread_count = 0
        session.commit()
        result = _conversation_to_dict(conversation)
    if changed:
        _publish_change(conversation_id, "read")
    return result


async def resolve_message_media(message_id: int) -> dict:
    with _get_session() as session:
        message = session.get(ConversationMessageModel, message_id)
        if message is None:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="消息不存在",
                status_code=404,
            )
        try:
            metadata = json.loads(message.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if (
            metadata.get("provider") != "eyun"
            or str(metadata.get("message_type") or "") != "60003"
        ):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前消息不支持媒体解析",
                status_code=400,
            )

        from app.services.eyun_callback_service import download_eyun_video

        try:
            url = await download_eyun_video(
                w_id=str(metadata.get("w_id") or ""),
                msg_id=str(
                    metadata.get("provider_msg_id")
                    or metadata.get("message_id")
                    or message.message_id
                    or ""
                ),
                content=str(metadata.get("raw_content") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="视频解析失败，请稍后重试或打开原链接",
                status_code=502,
            ) from exc

        media = metadata.get("media")
        if not isinstance(media, dict):
            media = {"type": "video"}
        media.update({"type": "video", "url": url, "fallback": False})
        metadata["media"] = media
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        conversation_id = message.conversation_id
        session.commit()
        result = _message_to_dict(message)
    _publish_change(conversation_id, "media")
    return result


async def force_handoff(
    conversation_id: str, operator_id: str, reason: str | None
) -> dict:
    del operator_id
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status == RESOLVED:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="已结束会话不能强制转人工",
                status_code=409,
            )
        conversation.status = HANDOFF_PENDING
        conversation.owner_id = None
        conversation.handoff_reason = reason or "manual_force_handoff"
        conversation.handoff_ticket_id = conversation.handoff_ticket_id or generate_id(
            "handoff"
        )
        conversation.updated_at = _now()
        session.commit()
        _publish_change(conversation_id, "handoff")
        return _conversation_to_dict(conversation)


async def release_to_ai(conversation_id: str, operator_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="只有当前接管人可以交回 AI",
                status_code=409,
            )
        conversation.status = AI_ACTIVE
        conversation.owner_id = None
        conversation.updated_at = _now()
        session.commit()
        _publish_change(conversation_id, "released")
        return _conversation_to_dict(conversation)


async def resolve_conversation(
    conversation_id: str, operator_id: str, reason: str | None
) -> dict:
    del operator_id
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        conversation.status = RESOLVED
        conversation.owner_id = None
        conversation.handoff_reason = reason or conversation.handoff_reason
        conversation.updated_at = _now()
        session.commit()
        _publish_change(conversation_id, "resolved")
        return _conversation_to_dict(conversation)


def _publish_change(conversation_id: str, reason: str) -> None:
    conversation_event_broker.publish(
        {
            "conversation_id": conversation_id,
            "reason": reason,
            "updated_at": _now().isoformat(),
        }
    )


def _get_session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(f"unsupported chat log provider: {settings.chat_log_provider}")
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ConversationModel.__table__,
                ConversationMessageModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    if settings.chat_log_db_url not in _initialized_urls:
        _ensure_conversation_columns(factory)
        _initialized_urls.add(settings.chat_log_db_url)
    return factory()


def _get_conversation_or_error(
    session: Session, conversation_id: str
) -> ConversationModel:
    conversation = session.scalar(
        select(ConversationModel).where(
            ConversationModel.conversation_id == conversation_id
        )
    )
    if conversation is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="会话不存在",
            status_code=404,
        )
    return conversation


def _conversation_to_dict(row: ConversationModel) -> dict:
    return {
        "conversation_id": row.conversation_id,
        "channel": row.channel,
        "user_id": row.user_id,
        "user_display_name": row.user_display_name,
        "user_avatar_url": row.user_avatar_url,
        "session_id": row.session_id,
        "tenant_id": row.tenant_id,
        "status": row.status,
        "owner_id": row.owner_id,
        "last_message": row.last_message,
        "last_route": row.last_route,
        "last_intent": row.last_intent,
        "handoff_reason": row.handoff_reason,
        "handoff_ticket_id": row.handoff_ticket_id,
        "unread_count": row.unread_count,
        "created_at": _utc_isoformat(row.created_at),
        "updated_at": _utc_isoformat(row.updated_at),
    }


def _message_to_dict(row: ConversationMessageModel) -> dict:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if metadata.get("provider") == "eyun" and not metadata.get("media"):
        from app.services.eyun_callback_service import extract_eyun_media_metadata

        media = extract_eyun_media_metadata(
            str(metadata.get("message_type") or ""),
            {
                "content": metadata.get("raw_content"),
                "img": metadata.get("image_thumb_base64"),
            },
        )
        if media:
            metadata["media"] = media
    media = metadata.get("media")
    if (
        metadata.get("provider") == "eyun"
        and isinstance(media, dict)
        and media.get("type") == "video"
        and isinstance(media.get("url"), str)
        and not media["url"].startswith("/static/media/")
    ):
        media["original_url"] = media.pop("url")
        media["fallback"] = True
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "trace_id": row.trace_id,
        "message_id": row.message_id,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "content": row.content,
        "route": row.route,
        "primary_intent": row.primary_intent,
        "metadata": metadata,
        "created_at": _utc_isoformat(row.created_at),
    }


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _metadata_text(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for source in _metadata_sources(metadata):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _metadata_sources(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [metadata]
    for key in ("user", "customer", "contact", "profile"):
        value = metadata.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _latest_eyun_reply_target(
    session: Session, conversation: ConversationModel
) -> dict[str, str] | None:
    rows = session.scalars(
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.conversation_id == conversation.conversation_id,
            ConversationMessageModel.sender_type == "customer",
        )
        .order_by(
            ConversationMessageModel.created_at.desc(),
            ConversationMessageModel.id.desc(),
        )
    ).all()
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        if metadata.get("provider") != "eyun":
            continue
        w_id = str(metadata.get("w_id") or "").strip()
        wc_id = str(
            metadata.get("from_group") or metadata.get("from_user") or conversation.user_id
        ).strip()
        if w_id and wc_id:
            return {"w_id": w_id, "wc_id": wc_id}
    return None


def _ensure_conversation_columns(factory: sessionmaker) -> None:
    with factory() as session:
        bind = session.get_bind()
        columns = {column["name"] for column in inspect(bind).get_columns("conversations")}
        if "user_display_name" not in columns:
            session.execute(text("ALTER TABLE conversations ADD COLUMN user_display_name VARCHAR(256)"))
        if "user_avatar_url" not in columns:
            session.execute(text("ALTER TABLE conversations ADD COLUMN user_avatar_url TEXT"))
        session.commit()
