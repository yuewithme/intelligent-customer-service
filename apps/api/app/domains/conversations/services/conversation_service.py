import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, inspect, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    ChatLogModel,
    ConversationMessageModel,
    ConversationModel,
    DemoPlatformStateModel,
)
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.services.conversation_event_service import conversation_event_broker
from app.domains.sales.services.sales_stage_catalog import normalize_sales_stage_value
from app.core.ids import generate_id


AI_ACTIVE = "ai_active"
AI_WAITING = "ai_waiting"
HANDOFF_PENDING = "handoff_pending"
HUMAN_ACTIVE = "human_active"
RESOLVED = "resolved"
AI_BLOCKED_STATUSES = frozenset({HANDOFF_PENDING, HUMAN_ACTIVE, RESOLVED})
RECOVERABLE_AUTOMATIC_HANDOFF_REASONS = frozenset(
    {
        "business_facts_unanswerable_to_handoff",
        "clarify_to_handoff",
        "invalid_route_to_handoff",
        "matched_orchid_not_found",
        "rag_no_answer_to_handoff",
        "template_not_found_to_handoff",
        "unsupported_to_handoff",
    }
)

_sessionmakers: dict[str, sessionmaker] = {}
_initialized_urls: set[str] = set()
logger = logging.getLogger("wechat_rag_bot.conversation")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_conversation_id(channel: str, user_id: str, session_id: str | None) -> str:
    return f"{channel}:{user_id}:{session_id or 'default'}"


def conversation_has_reply_route(
    *,
    channel: str,
    user_id: str,
    session_id: str | None,
    route: str,
) -> bool:
    conversation_id = make_conversation_id(channel, user_id, session_id)
    with _get_session() as session:
        return session.scalar(
            select(ConversationMessageModel.id)
            .where(
                ConversationMessageModel.conversation_id == conversation_id,
                ConversationMessageModel.sender_type.in_(("ai", "human")),
                ConversationMessageModel.route == route,
            )
            .limit(1)
        ) is not None


def latest_conversation_reply_route(
    *,
    channel: str,
    user_id: str,
    session_id: str | None,
    before: datetime | None = None,
) -> str | None:
    conversation_id = make_conversation_id(channel, user_id, session_id)
    with _get_session() as session:
        filters = [
            ConversationMessageModel.conversation_id == conversation_id,
            ConversationMessageModel.sender_type.in_(("ai", "human")),
        ]
        if before is not None:
            filters.append(ConversationMessageModel.created_at < before)
        return session.scalar(
            select(ConversationMessageModel.route)
            .where(*filters)
            .order_by(
                ConversationMessageModel.created_at.desc(),
                ConversationMessageModel.id.desc(),
            )
            .limit(1)
        )


def get_demo_platform_state(state_key: str) -> dict[str, str | None] | None:
    with _get_session() as session:
        state = session.get(DemoPlatformStateModel, state_key)
        if state is None:
            return None
        return {
            "customer_id": state.customer_id,
            "customer_name": state.customer_name,
            "session_id": state.session_id,
            "updated_at": state.updated_at.isoformat(),
        }


def set_demo_platform_state(
    state_key: str,
    *,
    customer_id: str,
    customer_name: str | None,
    session_id: str,
) -> dict[str, str | None]:
    now = _now()
    with _get_session() as session:
        state = session.get(DemoPlatformStateModel, state_key)
        if state is None:
            state = DemoPlatformStateModel(
                state_key=state_key,
                customer_id=customer_id,
                customer_name=customer_name,
                session_id=session_id,
                updated_at=now,
            )
            session.add(state)
        else:
            state.customer_id = customer_id
            state.customer_name = customer_name
            state.session_id = session_id
            state.updated_at = now
        session.commit()
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "session_id": session_id,
        "updated_at": now.isoformat(),
    }


async def list_conversations(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
    channels: tuple[str, ...] | None = None,
    user_id_prefix: str | None = None,
    excluded_user_id_prefix: str | None = None,
    wechat_group_allowlist: tuple[str, ...] | None = None,
    test_data: bool | None = None,
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
        if user_id_prefix:
            filters.append(ConversationModel.user_id.like(f"{user_id_prefix}%"))
        if excluded_user_id_prefix:
            filters.append(
                ConversationModel.user_id.not_like(f"{excluded_user_id_prefix}%")
            )
        if test_data is not None:
            test_message_exists = (
                select(ConversationMessageModel.id)
                .where(
                    ConversationMessageModel.conversation_id
                    == ConversationModel.conversation_id,
                    or_(
                        ConversationMessageModel.metadata_json.like(
                            '%"evaluation_id"%'
                        ),
                        ConversationMessageModel.metadata_json.like('%"test_entry"%'),
                        ConversationMessageModel.metadata_json.like('%"is_evaluation"%'),
                    ),
                )
                .exists()
            )
            is_test_conversation = or_(
                ConversationModel.user_id.like("demo:%"),
                ConversationModel.channel.in_(("web_demo", "mcp_demo")),
                test_message_exists,
            )
            filters.append(
                is_test_conversation if test_data else ~is_test_conversation
            )
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


async def recover_automatic_handoff(
    *, channel: str, user_id: str, session_id: str | None
) -> bool:
    """Resume AI for obsolete, unclaimed system fallbacks from older releases."""
    conversation_id = make_conversation_id(channel, user_id, session_id)
    now = _now()
    with _get_session() as session:
        result = session.execute(
            update(ConversationModel)
            .where(
                ConversationModel.conversation_id == conversation_id,
                ConversationModel.status == HANDOFF_PENDING,
                ConversationModel.owner_id.is_(None),
                ConversationModel.handoff_reason.in_(
                    RECOVERABLE_AUTOMATIC_HANDOFF_REASONS
                ),
            )
            .values(
                status=AI_ACTIVE,
                handoff_reason=None,
                handoff_ticket_id=None,
                updated_at=now,
            )
        )
        session.commit()
        recovered = bool(result.rowcount)
    if not recovered:
        return False

    from app.domains.customers.services.user_profile_service import clear_human_handoff

    await clear_human_handoff(user_id)
    _publish_change(conversation_id, "automatic_handoff_recovered")
    return True


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
        trace_ids = {row.trace_id for row in messages if row.trace_id}
        sales_stage_by_trace = {}
        if trace_ids:
            sales_stage_by_trace = {
                trace_id: sales_stage
                for trace_id, sales_stage in session.execute(
                    select(ChatLogModel.trace_id, ChatLogModel.sales_stage).where(
                        ChatLogModel.trace_id.in_(trace_ids)
                    )
                ).all()
                if sales_stage
            }
    from app.domains.customers.services.user_profile_service import get_sales_opportunity

    sales_opportunity = await get_sales_opportunity(conversation.user_id)
    return {
        "conversation": _conversation_to_dict(conversation),
        "messages": [
            _message_to_dict(
                row,
                sales_stage=sales_stage_by_trace.get(row.trace_id or ""),
            )
            for row in messages
        ],
        "sales_opportunity": sales_opportunity,
    }


async def record_ai_turn(*, message, result: dict) -> None:
    conversation_id = make_conversation_id(
        message.channel, message.user_id, message.session_id
    )
    status = HANDOFF_PENDING if result.get("need_human") else AI_WAITING
    handoff = result.get("handoff") or {}
    intent = result.get("intent") or {}
    sales_stage = normalize_sales_stage_value(
        intent.get("sales_stage"),
        fallback="",
    )
    skip_customer_record = bool(message.metadata.get("skip_customer_record"))
    evaluation_metadata = _evaluation_metadata(message.metadata)
    turn_metadata = {
        **evaluation_metadata,
        **({"sales_stage": sales_stage} if sales_stage else {}),
    }
    now = _now()
    suppress_handoff_notification = bool(
        message.metadata.get("suppress_handoff_notification")
    )
    should_notify_handoff = (
        status == HANDOFF_PENDING and not suppress_handoff_notification
    )
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if (
            not skip_customer_record
            and message.metadata.get("provider") == "eyun"
            and message.metadata.get("provider_delivery_mode") != "simulated"
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
                and not suppress_handoff_notification
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
                        {
                            "channel": message.channel,
                            "tenant_id": message.tenant_id,
                            **turn_metadata,
                        },
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
            )
        outbound_messages = _result_outbound_messages(result)
        for outbound in outbound_messages:
            message_type = outbound["type"]
            outbound_content = outbound["content"]
            outbound_metadata = _outbound_metadata(
                message_type,
                outbound_content,
                {
                    "sources": result.get("sources", []),
                    "template": result.get("template", {}),
                    **turn_metadata,
                },
            )
            if evaluation_metadata:
                outbound_metadata["provider"] = "evaluation"
                outbound_metadata["simulated_delivery"] = True
            session.add(
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    trace_id=message.trace_id,
                    sender_type="ai",
                    sender_id="ai",
                    content=_outbound_display_content(
                        message_type,
                        outbound_content,
                    ),
                    route=result.get("route"),
                    primary_intent=conversation.last_intent,
                    metadata_json=json.dumps(outbound_metadata, ensure_ascii=False),
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


def _result_outbound_messages(result: dict) -> list[dict[str, str]]:
    messages = result.get("outbound_messages")
    if isinstance(messages, list):
        normalized = [
            {
                "type": str(message.get("type") or "").strip(),
                "content": str(message.get("content") or "").strip(),
            }
            for message in messages
            if isinstance(message, dict)
            and str(message.get("type") or "").strip()
            and str(message.get("content") or "").strip()
        ]
        if normalized:
            return normalized
    return [
        {"type": "text", "content": answer}
        for answer in _answer_segments(result)
    ]


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


def list_conversation_emojis(conversation_id: str, limit: int = 40) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with _get_session() as session:
        _get_conversation_or_error(session, conversation_id)
        rows = list(
            session.scalars(
                select(ConversationMessageModel)
                .where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.sender_type == "customer",
                )
                .order_by(
                    ConversationMessageModel.created_at.desc(),
                    ConversationMessageModel.id.desc(),
                )
                .limit(500)
            )
        )
    items = []
    seen = set()
    for row in rows:
        emoji = _emoji_from_message(row)
        if not emoji or emoji["md5"] in seen:
            continue
        seen.add(emoji["md5"])
        items.append({"message_id": row.id, **emoji})
        if len(items) >= limit:
            break
    return {"items": items}


async def reply_conversation_image(
    conversation_id: str,
    operator_id: str,
    image_url: str,
) -> dict:
    image_url = image_url.strip()
    if not image_url.lower().startswith(("http://", "https://")):
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="图片地址必须是可访问的 HTTP(S) 地址",
            status_code=422,
        )
    return await _reply_conversation_media(
        conversation_id,
        operator_id,
        message_type="image",
        content=image_url,
        display_content="[图片]",
        media={"type": "image", "url": image_url, "fallback": False},
    )


async def reply_conversation_emoji(
    conversation_id: str,
    operator_id: str,
    source_message_id: int,
) -> dict:
    with _get_session() as session:
        _get_conversation_or_error(session, conversation_id)
        source = session.get(ConversationMessageModel, source_message_id)
        if (
            source is None
            or source.conversation_id != conversation_id
            or source.sender_type != "customer"
        ):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="表情来源消息不存在",
                status_code=404,
            )
        emoji = _emoji_from_message(source)
    if not emoji:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="该消息不是可复用的微信表情",
            status_code=422,
        )
    payload = json.dumps(
        {
            "md5": emoji["md5"],
            "size": emoji["size"],
            "preview_url": emoji["url"],
        },
        ensure_ascii=False,
    )
    return await _reply_conversation_media(
        conversation_id,
        operator_id,
        message_type="emoji",
        content=payload,
        display_content="[表情]",
        media={
            "type": "emoji",
            "url": emoji["url"],
            "md5": emoji["md5"],
            "size": emoji["size"],
            "fallback": not bool(emoji["url"]),
        },
    )


async def _reply_conversation_media(
    conversation_id: str,
    operator_id: str,
    *,
    message_type: str,
    content: str,
    display_content: str,
    media: dict[str, Any],
) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话未接管，不能人工回复",
                status_code=409,
            )
        eyun_target = _latest_eyun_reply_target(session, conversation)
        if eyun_target is None:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话渠道不支持发送该媒体消息",
                status_code=409,
            )
        now = _now()
        message = ConversationMessageModel(
            conversation_id=conversation_id,
            delivery_status="queued",
            sender_type="human",
            sender_id=operator_id,
            content=display_content,
            metadata_json=json.dumps(
                {
                    "provider": "eyun",
                    "direction": "outbound",
                    "message_type": message_type,
                    "outbound_content": content,
                    "origin": "admin_workbench",
                    "media": media,
                },
                ensure_ascii=False,
            ),
            created_at=now,
        )
        session.add(message)
        session.flush()
        message_id = message.id
        conversation.last_message = display_content
        conversation.updated_at = now
        memory_context = {
            "user_id": conversation.user_id,
            "tenant_id": conversation.tenant_id,
            "session_id": conversation.session_id,
            "channel": conversation.channel,
        }
        session.commit()
        result = _conversation_to_dict(conversation)
    try:
        from app.integrations.eyun.services.message_risk_control_service import (
            enqueue_wechat_outbound,
        )

        await enqueue_wechat_outbound(
            w_id=eyun_target["w_id"],
            wc_id=eyun_target["wc_id"],
            content=content,
            message_type=message_type,
            source_batch_key=f"workbench:{message_id}",
            conversation_message_id=message_id,
        )
    except Exception as exc:  # noqa: BLE001
        update_outbound_message_delivery(message_id, status="failed")
        raise AppError(
            ErrorCode.WECHAT_REPLY_FAILED,
            message="Eyun 媒体消息加入风控队列失败",
            status_code=502,
        ) from exc

    from app.domains.customers.services.user_profile_service import (
        append_conversation_memory,
    )

    await append_conversation_memory(
        user_id=memory_context["user_id"],
        tenant_id=memory_context["tenant_id"],
        session_id=memory_context["session_id"],
        role="human",
        content=display_content,
        channel=memory_context["channel"],
        source_id=f"workbench:{message_id}",
    )
    _publish_change(conversation_id, "reply")
    return result


def _emoji_from_message(row: ConversationMessageModel) -> dict[str, str] | None:
    metadata = _load_metadata(row.metadata_json)
    message_type = str(metadata.get("message_type") or "")
    if not message_type.endswith("006"):
        return None
    media = metadata.get("media")
    if not isinstance(media, dict):
        from app.integrations.eyun.services.eyun_callback_service import (
            extract_eyun_media_metadata,
        )

        media = extract_eyun_media_metadata(
            message_type,
            {
                "content": metadata.get("raw_content"),
                "url": metadata.get("url"),
                "md5": metadata.get("md5"),
                "length": metadata.get("length"),
            },
        )
    if not isinstance(media, dict):
        return None
    md5 = str(media.get("md5") or "").strip()
    size = str(media.get("size") or "").strip()
    if not md5 or not size:
        return None
    return {
        "md5": md5,
        "size": size,
        "url": str(media.get("url") or ""),
    }


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
                DemoPlatformStateModel.__table__,
                ChatLogModel.__table__,
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


def _message_to_dict(
    row: ConversationMessageModel,
    *,
    sales_stage: str | None = None,
) -> dict:
    metadata = _load_metadata(row.metadata_json)
    stored_sales_stage = normalize_sales_stage_value(
        metadata.get("sales_stage"),
        fallback="",
    )
    if sales_stage:
        stored_sales_stage = normalize_sales_stage_value(sales_stage, fallback="")
    if stored_sales_stage:
        metadata["sales_stage"] = stored_sales_stage
    else:
        metadata.pop("sales_stage", None)
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


def _evaluation_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    evaluation_id = str(metadata.get("evaluation_id") or "").strip()
    if not evaluation_id:
        return {}
    return {
        "evaluation_id": evaluation_id,
        "is_evaluation": True,
    }


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
        "emoji": "[表情]",
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
    elif message_type == "emoji":
        try:
            emoji = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            emoji = {}
        result["media"] = {
            "type": "emoji",
            "url": str(emoji.get("preview_url") or ""),
            "md5": str(emoji.get("md5") or ""),
            "size": str(emoji.get("size") or ""),
            "fallback": not bool(emoji.get("preview_url")),
        }
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
