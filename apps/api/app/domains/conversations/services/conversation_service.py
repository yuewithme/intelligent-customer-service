import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, ConversationMessageModel, ConversationModel
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.services.conversation_event_service import conversation_event_broker
from app.core.ids import generate_id


AI_ACTIVE = "ai_active"
AI_WAITING = "ai_waiting"
HANDOFF_PENDING = "handoff_pending"
HUMAN_ACTIVE = "human_active"
RESOLVED = "resolved"
AI_BLOCKED_STATUSES = frozenset({HANDOFF_PENDING, HUMAN_ACTIVE, RESOLVED})

_sessionmakers: dict[str, sessionmaker] = {}
_initialized_urls: set[str] = set()
logger = logging.getLogger("wechat_rag_bot.conversation")


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
    channels: tuple[str, ...] | None = None,
    wechat_group_allowlist: tuple[str, ...] | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    with _get_session() as session:
        filters = [ConversationModel.hidden_at.is_(None)]
        if status:
            filters.append(ConversationModel.status == status)
        if owner_id:
            filters.append(ConversationModel.owner_id == owner_id)
        if keyword:
            filters.append(ConversationModel.last_message.like(f"%{keyword}%"))
        if channels:
            filters.append(ConversationModel.channel.in_(channels))
        if wechat_group_allowlist is not None:
            filters.append(
                or_(
                    ConversationModel.channel != "wechat",
                    ConversationModel.user_id.not_like("%@chatroom"),
                    ConversationModel.user_id.in_(wechat_group_allowlist),
                )
            )
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


async def user_has_conversation_in_channels(
    user_id: str, channels: tuple[str, ...]
) -> bool:
    with _get_session() as session:
        count = session.scalar(
            select(func.count())
            .select_from(ConversationModel)
            .where(
                ConversationModel.user_id == user_id,
                ConversationModel.channel.in_(channels),
            )
        )
    return bool(count)


def conversation_blocks_ai(
    *, channel: str, user_id: str, session_id: str | None
) -> bool:
    conversation_id = make_conversation_id(channel, user_id, session_id)
    with _get_session() as session:
        status = session.scalar(
            select(ConversationModel.status).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
    return status in AI_BLOCKED_STATUSES


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
    from app.domains.customers.services.user_profile_service import get_sales_opportunity

    sales_opportunity = await get_sales_opportunity(conversation.user_id)
    return {
        "conversation": _conversation_to_dict(conversation),
        "messages": [_message_to_dict(row) for row in messages],
        "sales_opportunity": sales_opportunity,
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
    should_notify_handoff = status == HANDOFF_PENDING
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
            should_notify_handoff = (
                status == HANDOFF_PENDING and conversation.status != HANDOFF_PENDING
            )
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

        if conversation.status in AI_BLOCKED_STATUSES:
            conversation.last_message = message.message
            if not skip_customer_record:
                conversation.unread_count = (conversation.unread_count or 0) + 1
                session.add(
                    ConversationMessageModel(
                        conversation_id=conversation_id,
                        trace_id=message.trace_id,
                        message_id=message.message_id,
                        sender_type="customer",
                        sender_id=message.user_id,
                        content=message.message,
                        metadata_json=json.dumps(
                            {
                                "channel": message.channel,
                                "tenant_id": message.tenant_id,
                                "ai_blocked": True,
                            },
                            ensure_ascii=False,
                        ),
                        created_at=now,
                    )
                )
            conversation.updated_at = now
            session.commit()
            _publish_change(conversation_id, "message")
            return

        conversation.status = status
        conversation.hidden_at = None
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
        fallback_nickname = conversation.user_display_name
        session.commit()
    _publish_change(conversation_id, "message")
    if should_notify_handoff:
        await _notify_handoff_safely(
            customer_wc_id=message.user_id,
            metadata=message.metadata,
            fallback_nickname=fallback_nickname,
            handoff_reason=handoff.get("reason") or "human_required",
            trigger_message=message.message,
            source_reference=message.trace_id or conversation_id,
        )


async def _notify_handoff_safely(
    *,
    customer_wc_id: str,
    metadata: dict[str, Any],
    fallback_nickname: str | None,
    handoff_reason: str,
    trigger_message: str,
    source_reference: str,
) -> None:
    try:
        from app.domains.handoff.services.handoff_notification_service import (
            enqueue_handoff_notification,
        )

        await enqueue_handoff_notification(
            customer_wc_id=customer_wc_id,
            nickname=_metadata_text(
                metadata,
                ("nickname", "nick_name", "user_nickname", "display_name"),
            )
            or fallback_nickname,
            wechat_id=_metadata_text(
                metadata,
                ("alias_name", "wechat_id", "wechatId", "alias"),
            ),
            handoff_reason=handoff_reason,
            trigger_message=trigger_message,
            source_reference=source_reference,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to enqueue handoff notification: %s", exc)


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
    should_notify_handoff = status == HANDOFF_PENDING

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
            should_notify_handoff = (
                status == HANDOFF_PENDING and conversation.status != HANDOFF_PENDING
            )
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

        preserve_ai_lock = conversation.status in AI_BLOCKED_STATUSES
        if preserve_ai_lock:
            should_notify_handoff = False
        if not preserve_ai_lock:
            conversation.status = status
            conversation.owner_id = None
        conversation.hidden_at = None
        conversation.last_message = content
        if not preserve_ai_lock:
            conversation.last_route = route
            conversation.last_intent = primary_intent
            conversation.handoff_reason = handoff_reason
            if status == HANDOFF_PENDING:
                conversation.handoff_ticket_id = (
                    conversation.handoff_ticket_id or generate_id("handoff")
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
    if should_notify_handoff:
        await _notify_handoff_safely(
            customer_wc_id=user_id,
            metadata=metadata,
            fallback_nickname=result.get("user_display_name"),
            handoff_reason=handoff_reason or "human_required",
            trigger_message=content,
            source_reference=message_id or result.get("handoff_ticket_id") or conversation_id,
        )
    return result


async def ensure_outbound_conversation_message(
    *,
    channel: str,
    user_id: str,
    session_id: str | None,
    content: str,
    message_type: str = "text",
    sender_type: str = "ai",
    sender_id: str | None = "ai",
    tenant_id: str = "tenant_default",
    provider_message_id: str | None = None,
    trace_id: str | None = None,
    delivery_status: str = "queued",
    route: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_after: datetime | None = None,
    reconcile_pending: bool = True,
) -> dict:
    """Create or reconcile one outbound message shown in the workbench."""
    conversation_id = make_conversation_id(channel, user_id, session_id)
    now = _now()
    display_content = _outbound_display_content(message_type, content)
    message_metadata = _outbound_metadata(message_type, content, metadata)

    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if conversation is None:
            conversation = ConversationModel(
                conversation_id=conversation_id,
                channel=channel,
                user_id=user_id,
                session_id=session_id,
                tenant_id=tenant_id,
                status=AI_WAITING,
                unread_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            session.flush()

        message = None
        if trace_id:
            message = session.scalar(
                select(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.trace_id == trace_id,
                )
            )
        if provider_message_id:
            message = message or session.scalar(
                select(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.message_id == provider_message_id,
                )
            )
        if message is None and reconcile_pending:
            pending_query = select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == conversation_id,
                ConversationMessageModel.content == display_content,
                ConversationMessageModel.sender_type.in_(("ai", "human")),
                ConversationMessageModel.message_id.is_(None),
                or_(
                    ConversationMessageModel.delivery_status.is_(None),
                    ConversationMessageModel.delivery_status == "queued",
                ),
            )
            if created_after is not None:
                pending_query = pending_query.where(
                    ConversationMessageModel.created_at >= created_after
                )
            message = session.scalar(
                pending_query.order_by(
                    ConversationMessageModel.created_at.asc(),
                    ConversationMessageModel.id.asc(),
                ).limit(1)
            )

        if message is None:
            message = ConversationMessageModel(
                conversation_id=conversation_id,
                trace_id=trace_id,
                message_id=provider_message_id,
                delivery_status=delivery_status,
                sender_type=sender_type,
                sender_id=sender_id,
                content=display_content,
                route=route,
                metadata_json=json.dumps(message_metadata, ensure_ascii=False),
                created_at=now,
            )
            session.add(message)
            session.flush()
        else:
            if provider_message_id:
                message.message_id = provider_message_id
            message.delivery_status = delivery_status
            existing_metadata = _load_metadata(message.metadata_json)
            existing_metadata.update(message_metadata)
            message.metadata_json = json.dumps(existing_metadata, ensure_ascii=False)

        if sender_type == "human":
            conversation.last_message = display_content
        conversation.updated_at = now
        session.commit()
        result = _message_to_dict(message)

    _publish_change(conversation_id, "message")
    return result


def update_outbound_message_delivery(
    conversation_message_id: int,
    *,
    status: str,
    provider_message_id: str | None = None,
    sent_at: datetime | None = None,
) -> None:
    with _get_session() as session:
        message = session.get(ConversationMessageModel, conversation_message_id)
        if message is None:
            return
        if provider_message_id:
            duplicate = session.scalar(
                select(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == message.conversation_id,
                    ConversationMessageModel.message_id == provider_message_id,
                    ConversationMessageModel.id != message.id,
                )
            )
            if duplicate is not None:
                session.delete(duplicate)
            message.message_id = provider_message_id
        message.delivery_status = status
        if sent_at is not None:
            message.created_at = sent_at
        session.commit()
        conversation_id = message.conversation_id
    _publish_change(conversation_id, "message")


async def update_customer_identity(
    *,
    channel: str,
    user_id: str,
    session_id: str | None,
    metadata: dict[str, Any],
) -> bool:
    conversation_id = make_conversation_id(channel, user_id, session_id)
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
        ("avatar_url", "avatar", "headimgurl", "head_img_url", "head_url"),
    )
    if not display_name and not avatar_url:
        return False

    changed = False
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if conversation is None:
            return False
        if display_name and conversation.user_display_name != display_name:
            conversation.user_display_name = display_name
            changed = True
        if avatar_url and conversation.user_avatar_url != avatar_url:
            conversation.user_avatar_url = avatar_url
            changed = True
        if changed:
            conversation.updated_at = _now()
            session.commit()

    if changed:
        _publish_change(conversation_id, "identity")
    return changed


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
    eyun_target: dict[str, str] | None = None
    conversation_message_id: int | None = None
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话未接管，不能人工回复",
                status_code=409,
            )
        eyun_target = _latest_eyun_reply_target(session, conversation)
        now = _now()
        message = ConversationMessageModel(
            conversation_id=conversation_id,
            message_id=None,
            delivery_status="queued" if eyun_target else None,
            sender_type="human",
            sender_id=operator_id,
            content=content,
            metadata_json=json.dumps(
                {
                    "provider": "eyun",
                    "direction": "outbound",
                    "message_type": "text",
                    "origin": "admin_workbench",
                }
                if eyun_target
                else {},
                ensure_ascii=False,
            ),
            created_at=now,
        )
        session.add(message)
        session.flush()
        conversation_message_id = message.id
        conversation.last_message = content
        conversation.updated_at = now
        memory_context = {
            "user_id": conversation.user_id,
            "tenant_id": conversation.tenant_id,
            "session_id": conversation.session_id,
            "channel": conversation.channel,
        }
        session.commit()
        result = _conversation_to_dict(conversation)

    if eyun_target and conversation_message_id is not None:
        try:
            from app.integrations.eyun.services.message_risk_control_service import enqueue_wechat_outbound

            await enqueue_wechat_outbound(
                w_id=eyun_target["w_id"],
                wc_id=eyun_target["wc_id"],
                content=content,
                source_batch_key=f"workbench:{conversation_message_id}",
                conversation_message_id=conversation_message_id,
            )
        except Exception as exc:  # noqa: BLE001
            update_outbound_message_delivery(conversation_message_id, status="failed")
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="Eyun 消息加入风控队列失败",
                status_code=502,
            ) from exc

    from app.domains.customers.services.user_profile_service import append_conversation_memory

    await append_conversation_memory(
        user_id=memory_context["user_id"],
        tenant_id=memory_context["tenant_id"],
        session_id=memory_context["session_id"],
        role="human",
        content=content,
        channel=memory_context["channel"],
        source_id=f"workbench:{conversation_message_id}",
    )
    _publish_change(conversation_id, "reply")
    return result


def get_human_activity_send_target(
    conversation_id: str, operator_id: str
) -> dict[str, str]:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话未由该操作员接管，不能发送活动",
                status_code=409,
            )
        target = _latest_eyun_reply_target(session, conversation)
        if target is None:
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="当前会话缺少 Eyun 发送目标",
                status_code=409,
            )
        return {
            **target,
            "user_id": conversation.user_id,
            "session_id": conversation.session_id or "default",
            "tenant_id": conversation.tenant_id,
        }


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


async def hide_conversation(conversation_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        conversation.hidden_at = _now()
        session.commit()
        result = _conversation_to_dict(conversation)
    _publish_change(conversation_id, "hidden")
    return result


async def unhide_conversation(conversation_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        conversation.hidden_at = None
        session.commit()
        result = _conversation_to_dict(conversation)
    _publish_change(conversation_id, "unhidden")
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

        media = metadata.get("media")
        if not isinstance(media, dict):
            media = {"type": "video"}
        if media.get("resolve_status") in {"succeeded", "failed"}:
            return _message_to_dict(message)
        media.update({"type": "video", "resolve_status": "processing"})
        media.pop("resolve_error", None)
        metadata["media"] = media
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        conversation_id = message.conversation_id
        session.commit()

        from app.integrations.eyun.services.eyun_callback_service import download_eyun_video

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
            media.update(
                {
                    "resolve_status": "failed",
                    "resolve_error": "provider_download_failed",
                }
            )
            message.metadata_json = json.dumps(metadata, ensure_ascii=False)
            session.commit()
            _publish_change(conversation_id, "media")
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="视频解析失败，请稍后重试或打开原链接",
                status_code=502,
            ) from exc

        media.update(
            {
                "type": "video",
                "url": url,
                "fallback": False,
                "resolve_status": "succeeded",
            }
        )
        media.pop("resolve_error", None)
        metadata["media"] = media
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        session.commit()
        result = _message_to_dict(message)
    _publish_change(conversation_id, "media")
    return result


async def force_handoff(
    conversation_id: str, operator_id: str, reason: str | None
) -> dict:
    del operator_id
    should_notify_handoff = False
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status == RESOLVED:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="已结束会话不能强制转人工",
                status_code=409,
            )
        should_notify_handoff = conversation.status != HANDOFF_PENDING
        conversation.status = HANDOFF_PENDING
        conversation.owner_id = None
        conversation.handoff_reason = reason or "manual_force_handoff"
        conversation.handoff_ticket_id = conversation.handoff_ticket_id or generate_id(
            "handoff"
        )
        conversation.updated_at = _now()
        session.commit()
        result = _conversation_to_dict(conversation)
        _publish_change(conversation_id, "handoff")
    if should_notify_handoff:
        await _notify_handoff_safely(
            customer_wc_id=result["user_id"],
            metadata={},
            fallback_nickname=result.get("user_display_name"),
            handoff_reason=reason or "manual_force_handoff",
            trigger_message=str(result.get("last_message") or "").strip(),
            source_reference=result.get("handoff_ticket_id") or conversation_id,
        )
    return result


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
        conversation.handoff_reason = None
        conversation.handoff_ticket_id = None
        conversation.updated_at = _now()
        session.commit()
        user_id = conversation.user_id
        result = _conversation_to_dict(conversation)
        _publish_change(conversation_id, "released")
    from app.domains.customers.services.user_profile_service import clear_human_handoff

    await clear_human_handoff(user_id)
    return result


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
    metadata = _load_metadata(row.metadata_json)
    if metadata.get("provider") == "eyun" and not metadata.get("media"):
        from app.integrations.eyun.services.eyun_callback_service import extract_eyun_media_metadata

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
    media_is_resolvable = (
        metadata.get("provider") == "eyun"
        and str(metadata.get("message_type") or "") == "60003"
        and isinstance(media, dict)
        and media.get("type") == "video"
    )
    if (
        media_is_resolvable
        and isinstance(media.get("url"), str)
        and not media["url"].startswith("/static/media/")
    ):
        media["original_url"] = media.pop("url")
        media["fallback"] = True
    if media_is_resolvable:
        media.setdefault(
            "resolve_status",
            "succeeded" if media.get("url") else "pending",
        )
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "trace_id": row.trace_id,
        "message_id": row.message_id,
        "delivery_status": row.delivery_status,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "content": row.content,
        "route": row.route,
        "primary_intent": row.primary_intent,
        "metadata": metadata,
        "created_at": _utc_isoformat(row.created_at),
    }


def _load_metadata(value: str | None) -> dict[str, Any]:
    try:
        metadata = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _eyun_result_message_id(result: Any) -> str | None:
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return None
    data = result["data"]
    return str(data.get("newMsgId") or data.get("msgId") or "") or None


def _eyun_result_sent_at(result: Any) -> datetime | None:
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return None
    try:
        timestamp = int(result["data"].get("createTime"))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp > 0 else None


def _outbound_display_content(message_type: str, content: str) -> str:
    if message_type == "text":
        return content
    if message_type == "mini_program":
        try:
            card = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            card = {}
        title = str(card.get("title") or "").strip() if isinstance(card, dict) else ""
        return f"[小程序] {title}".strip()
    if message_type == "link_card":
        try:
            card = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            card = {}
        title = str(card.get("title") or "").strip() if isinstance(card, dict) else ""
        return f"[链接卡片] {title}".strip()
    return {
        "image": "[图片]",
        "received_image": "[图片]",
        "received_video": "[视频]",
        "video": "[视频]",
    }.get(message_type, "[非文本消息]")


def _outbound_metadata(
    message_type: str, content: str, metadata: dict[str, Any] | None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": "eyun",
        "direction": "outbound",
        "message_type": message_type,
        "outbound_content": content,
    }
    if message_type in {"image", "received_image"}:
        result["media"] = {"type": "image", "url": content, "fallback": False}
    elif message_type in {"video", "received_video"}:
        result["media"] = {"type": "video", "url": content, "fallback": False}
    elif message_type == "mini_program":
        try:
            result["mini_program"] = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            result["mini_program"] = {"content": content}
    elif message_type == "link_card":
        try:
            result["link_card"] = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            result["link_card"] = {"content": content}
    if metadata:
        result.update(metadata)
    return result


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
        if "hidden_at" not in columns:
            session.execute(text("ALTER TABLE conversations ADD COLUMN hidden_at DATETIME"))
        message_columns = {
            column["name"]
            for column in inspect(bind).get_columns("conversation_messages")
        }
        if "delivery_status" not in message_columns:
            session.execute(
                text(
                    "ALTER TABLE conversation_messages "
                    "ADD COLUMN delivery_status VARCHAR(32)"
                )
            )
        session.commit()
