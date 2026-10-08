import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.domains.conversations.services.conversation_service import (
    AI_ACTIVE, HANDOFF_PENDING, HUMAN_ACTIVE, RESOLVED,
    _conversation_to_dict, _get_conversation_or_error, _get_session, _publish_change,
)
from app.domains.customers.services.user_profile_service import clear_human_handoff
from app.infrastructure.database.models import ConversationModel, ConversationReplyDraftModel
from app.shared.schemas.common import AppError, ErrorCode


DRAFT_REASON = "workbench_typing"
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _restore_ai(conversation: ConversationModel) -> None:
    conversation.status = AI_ACTIVE
    conversation.owner_id = None
    conversation.handoff_reason = None
    conversation.handoff_ticket_id = None
    conversation.updated_at = _now()


async def update_reply_draft(
    conversation_id: str, operator_id: str, editor_id: str,
    *, active: bool, remaining_seconds: float = 60,
) -> dict:
    await expire_reply_drafts()
    restore_user = None
    changed = False
    with _get_session() as session:
        # Serialize the editor ownership check with lease updates and expiry.
        session.execute(text("BEGIN IMMEDIATE"))
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status == RESOLVED:
            raise AppError(ErrorCode.REQUEST_INVALID, "会话已结束，仅可查看", status_code=409)
        draft = session.get(ConversationReplyDraftModel, conversation_id)
        if active:
            if (draft and (draft.editor_id != editor_id or draft.operator_id != operator_id)) or (
                conversation.status == HUMAN_ACTIVE and conversation.owner_id != operator_id
            ):
                raise AppError(ErrorCode.REQUEST_INVALID, "其他客服正在回复，请稍后再试", status_code=409)
            if draft is None:
                draft = ConversationReplyDraftModel(
                    conversation_id=conversation_id, editor_id=editor_id, operator_id=operator_id,
                )
                session.add(draft)
            draft.expires_at = _now() + timedelta(seconds=min(60, max(0, remaining_seconds)))
            changed = conversation.status != HUMAN_ACTIVE or conversation.handoff_reason != DRAFT_REASON
            conversation.status = HUMAN_ACTIVE
            conversation.owner_id = operator_id
            conversation.handoff_reason = DRAFT_REASON
            conversation.handoff_ticket_id = None
            if changed:
                conversation.updated_at = _now()
        elif draft:
            if draft.editor_id == editor_id and draft.operator_id == operator_id:
                session.delete(draft)
                if conversation.handoff_reason == DRAFT_REASON and conversation.owner_id == operator_id:
                    _restore_ai(conversation)
                    restore_user = conversation.user_id
                    changed = True
        elif conversation.status == HANDOFF_PENDING or (
            conversation.status == HUMAN_ACTIVE and conversation.owner_id == operator_id
        ):
            _restore_ai(conversation)
            restore_user = conversation.user_id
            changed = True
        session.commit()
        result = _conversation_to_dict(conversation)
    if restore_user:
        await clear_human_handoff(restore_user)
    if changed:
        _publish_change(conversation_id, "reply_mode")
    return result


def reply_draft_deadline(conversation_id: str) -> datetime | None:
    with _get_session() as session:
        draft = session.get(ConversationReplyDraftModel, conversation_id)
        # Keep queued work pending until the expiry worker restores the status.
        return max(_aware(draft.expires_at), _now() + timedelta(seconds=1)) if draft else None


async def expire_reply_drafts() -> int:
    restored = []
    with _get_session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        drafts = session.scalars(select(ConversationReplyDraftModel).where(
            ConversationReplyDraftModel.expires_at <= _now(),
        )).all()
        for draft in drafts:
            conversation = session.scalar(select(ConversationModel).where(
                ConversationModel.conversation_id == draft.conversation_id,
            ))
            if conversation and conversation.status == HUMAN_ACTIVE and conversation.handoff_reason == DRAFT_REASON and conversation.owner_id == draft.operator_id:
                _restore_ai(conversation)
                restored.append((conversation.conversation_id, conversation.user_id))
            session.delete(draft)
        session.commit()
    for conversation_id, user_id in restored:
        await clear_human_handoff(user_id)
        _publish_change(conversation_id, "reply_draft_expired")
    return len(restored)


async def reply_draft_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await expire_reply_drafts()
        except Exception:
            logger.exception("Failed to expire reply drafts")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)
        except TimeoutError:
            pass
