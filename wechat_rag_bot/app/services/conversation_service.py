import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ConversationMessageModel, ConversationModel
from app.schemas.common import AppError, ErrorCode
from app.utils.ids import generate_id


AI_ACTIVE = "ai_active"
AI_WAITING = "ai_waiting"
HANDOFF_PENDING = "handoff_pending"
HUMAN_ACTIVE = "human_active"
RESOLVED = "resolved"

_sessionmakers: dict[str, sessionmaker] = {}


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
    now = _now()
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if conversation is None:
            conversation = ConversationModel(
                conversation_id=conversation_id,
                channel=message.channel,
                user_id=message.user_id,
                session_id=message.session_id,
                tenant_id=message.tenant_id,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            session.flush()

        conversation.status = status
        conversation.owner_id = None
        conversation.last_message = message.message
        conversation.last_route = result.get("route")
        conversation.last_intent = intent.get("primary_intent")
        conversation.handoff_reason = handoff.get("reason")
        conversation.handoff_ticket_id = handoff.get("ticket_id")
        conversation.unread_count = (conversation.unread_count or 0) + 1
        conversation.updated_at = now

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
        answer = result.get("answer")
        if answer:
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
        session.commit()
        return _conversation_to_dict(conversation)


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
        return _conversation_to_dict(conversation)


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
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _message_to_dict(row: ConversationMessageModel) -> dict:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
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
        "created_at": row.created_at.isoformat(),
    }
