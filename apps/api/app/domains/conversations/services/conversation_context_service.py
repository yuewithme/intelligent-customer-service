from datetime import datetime, timezone

from sqlalchemy import select

from app.domains.conversations.services.conversation_service import _get_session, _load_metadata
from app.domains.conversations.services.media_context_service import message_context_text
from app.infrastructure.database.models import ConversationMessageModel


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
            if row.sender_type not in {"customer", "ai", "human", "system"}:
                continue
            metadata = _load_metadata(row.metadata_json)
            if metadata.get("revoked_at") or row.delivery_status == "revoked":
                continue
            if row.sender_type != "customer":
                delivered = row.delivery_status in {"sent", "accepted", "confirmed"}
                # Local API replies have no provider receipt; real WeChat sends do.
                local_reply = row.delivery_status is None and not conversation_id.startswith("wechat:")
                if not delivered and not local_reply:
                    if len(delivery_notes) < 8:
                        delivery_notes.append({"message_id": row.id, "status": row.delivery_status or "unknown",
                            "content": str(row.content or "")[:200]})
                    continue
            content = message_context_text({"content": row.content, "metadata": metadata})
            if not content:
                continue
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
            turns.append({"role": "customer" if row.sender_type == "customer" else "assistant",
                "sender_type": row.sender_type, "message_id": row.id, "content": content,
                "created_at": occurred_at.astimezone(timezone.utc).isoformat()})
            used_chars += len(content)
            # Keep the crossing message so the prompt can report its own truncation.
            if used_chars > char_budget:
                break
    if not found_records:
        return None
    turns.sort(key=lambda turn: (turn["created_at"], turn["message_id"]))
    return {"turns": turns, "source": "conversation_messages", "delivery_notes": list(reversed(delivery_notes))}
