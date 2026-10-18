import json
import logging
from datetime import datetime

from sqlalchemy import or_, select

from app.domains.conversations.services.conversation_service import _get_session, _load_metadata, make_conversation_id
from app.domains.conversations.services.media_context_service import message_context_text
from app.domains.customers.services.memory_repository import get_memory_session
from app.infrastructure.database.models import ConversationMessageModel, ConversationModel, MemoryIdentityModel


logger = logging.getLogger(__name__)
DELIVERED_SOURCE = "delivered_message:"


def _confirmed(row) -> bool:
    return row.delivery_status in {"sent", "confirmed"} and not _load_metadata(row.metadata_json).get("revoked_at")


def record_delivered_message(message_id: int) -> None:
    from app.domains.customers.services.memory_dual_write_service import dual_write_conversation_event

    try:
        with _get_session() as session:
            row = session.get(ConversationMessageModel, message_id)
            if row is None or row.sender_type not in {"ai", "human", "system"} or not _confirmed(row):
                return
            conversation = session.scalar(select(ConversationModel).where(
                ConversationModel.conversation_id == row.conversation_id))
            if conversation is None or conversation.channel != "wechat":
                return
            metadata = _load_metadata(row.metadata_json)
            if metadata.get("simulated_delivery"):
                return
            timestamps = metadata.get("delivery_timestamps") or {}
            sent_at = timestamps.get("accepted_at") or timestamps.get("sent_at") or timestamps.get("confirmed_at")
            occurred_at = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00")) if sent_at else row.created_at
            payload = dict(tenant_id=conversation.tenant_id, channel=conversation.channel,
                external_user_id=conversation.user_id,
                owner_external_id=metadata.get("w_id") or metadata.get("owner_external_id") or conversation.owner_wc_id or conversation.session_id or "",
                session_id=conversation.session_id, role="human" if row.sender_type == "human" else "assistant",
                content=message_context_text({"content": row.content, "metadata": metadata}),
                source_id=f"{DELIVERED_SOURCE}{row.id}", trace_id=row.trace_id, occurred_at=occurred_at)
        dual_write_conversation_event(**payload)
    except Exception:
        # A memory-store outage must not turn a successful customer send into a retry.
        logger.exception("Could not record delivered message memory id=%s", message_id)


def invalid_delivery_event_ids(events) -> set[int]:
    """Recheck immutable historical evidence against current delivery/revocation state."""
    invalid = set()
    identities = {}
    with get_memory_session() as memory_session, _get_session() as chat_session:
        for event in events:
            if event.source_type != "conversation_message":
                continue
            key = (event.tenant_id, event.subject_id)
            if key not in identities:
                identities[key] = list(memory_session.scalars(select(MemoryIdentityModel).where(
                    MemoryIdentityModel.tenant_id == event.tenant_id,
                    MemoryIdentityModel.subject_id == event.subject_id)))
            owners = [item for item in identities[key] if item.channel == "wechat"]
            if not owners:
                continue
            cids = [make_conversation_id("wechat", item.external_user_id,
                event.session_id or item.owner_external_id) for item in owners]
            source_id = str(event.source_id or "")
            query = select(ConversationMessageModel).where(ConversationMessageModel.conversation_id.in_(cids))
            direct_id = source_id.partition(":")[2]
            if source_id.startswith((DELIVERED_SOURCE, "workbench:", "scheduled_touch:")) and direct_id.isdigit():
                query = query.where(ConversationMessageModel.id == int(direct_id))
            else:
                refs = [value for value in (event.trace_id, source_id) if value]
                query = query.where(or_(ConversationMessageModel.trace_id.in_(refs),
                    ConversationMessageModel.message_id.in_(refs)))
            outbound = event.actor_type in {"assistant", "human_agent"}
            query = query.where(ConversationMessageModel.sender_type.in_(
                ("ai", "human", "system") if outbound else ("customer",)))
            rows = list(chat_session.scalars(query.order_by(ConversationMessageModel.id)))
            if outbound:
                if not rows or not all(_confirmed(row) for row in rows):
                    invalid.add(event.id)
                    continue
                if not source_id.startswith((DELIVERED_SOURCE, "scheduled_touch:")):
                    content = event.content if hasattr(event, "content") else json.loads(event.content_json)
                    normalize = lambda value: "".join(str(value).split())
                    actual = normalize("".join(row.content for row in rows))
                    if normalize(content.get("text", "")) not in actual:
                        invalid.add(event.id)
            elif rows and all(_load_metadata(row.metadata_json).get("revoked_at") or row.delivery_status == "revoked" for row in rows):
                invalid.add(event.id)
    return invalid
