from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.domains.conversations.services.conversation_service import _get_session, _load_metadata
from app.domains.conversations.services.media_context_service import message_context_text
from app.infrastructure.database.models import ConversationMessageModel


CONTEXT_DELIVERY_STATUSES = {"sent", "accepted", "confirmed", "unconfirmed", "delivery_unknown"}


def message_context_turn(row) -> dict | None:
    metadata = _load_metadata(row.metadata_json)
    if row.sender_type not in {"customer", "ai", "human", "system"} or metadata.get("revoked_at") or row.delivery_status == "revoked":
        return None
    if row.sender_type != "customer" and row.delivery_status not in CONTEXT_DELIVERY_STATUSES:
        if row.delivery_status is not None or row.conversation_id.startswith("wechat:"):
            return None
    content = message_context_text({"content": row.content, "metadata": metadata})
    if not content:
        return None
    occurred_at = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
    timestamps = metadata.get("delivery_timestamps") or {}
    if row.sender_type != "customer" and isinstance(timestamps, dict):
        for key in ("accepted_at", "sent_at", "confirmed_at"):
            if not timestamps.get(key):
                continue
            try:
                occurred_at = datetime.fromisoformat(str(timestamps[key]).replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    return {"role": "customer" if row.sender_type == "customer" else "assistant",
        "sender_type": row.sender_type, "message_id": row.id, "content": content,
        "delivery_status": row.delivery_status or ("received" if row.sender_type == "customer" else "local"),
        "created_at": occurred_at.astimezone(timezone.utc).isoformat()}


def get_conversation_context(conversation_id: str, char_budget: int) -> dict | None:
    turns = []
    delivery_notes = []
    used_chars = 0
    found_records = False
    with _get_session() as session:
        rows = session.scalars(select(ConversationMessageModel).where(
            ConversationMessageModel.conversation_id == conversation_id,
        ).order_by(ConversationMessageModel.created_at.desc(), ConversationMessageModel.id.desc())).yield_per(50)
        for row in rows:
            found_records = True
            metadata = _load_metadata(row.metadata_json)
            if metadata.get("revoked_at") or row.delivery_status == "revoked":
                continue
            turn = message_context_turn(row)
            if turn is None:
                if row.sender_type != "customer" and len(delivery_notes) < 8:
                    delivery_notes.append({"message_id": row.id, "status": row.delivery_status or "unknown"})
                continue
            turns.append(turn)
            used_chars += len(turn["content"])
            # Keep the crossing message so the prompt can report its own truncation.
            if used_chars > char_budget:
                break
    if not found_records:
        return None
    turns.sort(key=lambda turn: (turn["created_at"], turn["message_id"]))
    return {"turns": turns, "source": "conversation_messages", "delivery_notes": list(reversed(delivery_notes))}


def search_conversation_history(conversation_id: str, *, query: str = "", before_message_id: int | None = None,
                                limit: int = 10) -> dict:
    limit = max(1, min(limit, 30))
    with _get_session() as session:
        statement = select(ConversationMessageModel).where(ConversationMessageModel.conversation_id == conversation_id)
        if before_message_id is not None:
            statement = statement.where(ConversationMessageModel.id < before_message_id)
        if query:
            statement = statement.where(or_(ConversationMessageModel.content.contains(query, autoescape=True),
                ConversationMessageModel.metadata_json.contains(query, autoescape=True)))
        rows = session.scalars(statement.order_by(ConversationMessageModel.id.desc())).yield_per(50)
        selected = []
        for row in rows:
            turn = message_context_turn(row)
            if turn is None or (query and query.casefold() not in turn["content"].casefold()):
                continue
            selected.append(turn)
            if len(selected) > limit:
                break
    has_more = len(selected) > limit
    selected = selected[:limit]
    return {"turns": list(reversed(selected)), "has_more": has_more,
        "next_before_message_id": selected[-1]["message_id"] if has_more else None,
        "instruction": "这是当前客户当前账号的历史原文；沿用发送状态，不把未确认当作送达。"}
