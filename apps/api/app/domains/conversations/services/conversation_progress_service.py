import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.domains.conversations.services.conversation_context_service import message_context_turn
from app.domains.conversations.services.conversation_service import _get_session, make_conversation_id
from app.infrastructure.database.models import ConversationIssueProgressModel, ConversationMessageModel


def _identity(message):
    cid = make_conversation_id(message.channel, message.user_id, message.session_id)
    key = hashlib.sha256(f"{message.tenant_id}\0{cid}".encode()).hexdigest()
    return cid, key


def _source_turn(session, cid, source):
    query = select(ConversationMessageModel).where(ConversationMessageModel.conversation_id == cid)
    if isinstance(source.get("message_id"), int):
        query = query.where(ConversationMessageModel.id == source["message_id"])
    else:
        identifiers = []
        if source.get("trace_id"):
            identifiers.append(ConversationMessageModel.trace_id == source["trace_id"])
        if source.get("provider_message_id"):
            identifiers.append(ConversationMessageModel.message_id == source["provider_message_id"])
        if not identifiers:
            return None
        query = query.where(ConversationMessageModel.sender_type == "customer", or_(*identifiers))
    row = session.scalar(query.order_by(ConversationMessageModel.id.desc()))
    turn = message_context_turn(row) if row is not None else None
    if turn and source["quote"] in turn["content"] and (
        turn["role"] == "customer" or turn["delivery_status"] in {"sent", "confirmed", "local"}
    ):
        return {"message_id": turn["message_id"], "sender_type": turn["sender_type"],
            "created_at": turn["created_at"], "delivery_status": turn["delivery_status"], "quote": source["quote"]}
    return None


def load_issue_progress(message) -> dict:
    if message.metadata.get("evaluation_id"):
        return {}
    cid, key = _identity(message)
    with _get_session() as session:
        row = session.get(ConversationIssueProgressModel, key)
        if row is None:
            return {}
        state = json.loads(row.state_json)
        sources = [_source_turn(session, cid, source) for source in state["evidence"]]
        if not all(sources):
            return {}
        return {"summary": state["summary"], "evidence": sources, "updated_at": row.updated_at.isoformat(),
            "instruction": "这是带来源的阶段性摘要，不是新的客户原话；近期更新优先。有疑问查原文，不能把建议当成客户已完成。"}


def save_issue_progress(message, progress) -> bool:
    if progress is None or message.metadata.get("evaluation_id") or message.metadata.get("system_event"):
        return False
    cid, key = _identity(message)
    with _get_session() as session:
        evidence = []
        for item in progress.evidence:
            source = item.model_dump()
            if item.message_id == "current":
                if item.quote not in message.message:
                    return False
                batch_ids = message.metadata.get("conversation_message_ids") or []
                if batch_ids:
                    sources = [_source_turn(session, cid, {"message_id": message_id, "quote": item.quote})
                        for message_id in batch_ids if isinstance(message_id, int)]
                    source = next((value for value in sources if value and value["sender_type"] == "customer"), None)
                    if source is None:
                        return False
                else:
                    source = {"trace_id": message.trace_id, "provider_message_id": message.message_id, "quote": item.quote}
                    source = _source_turn(session, cid, source) or source
            else:
                source = _source_turn(session, cid, source)
                if source is None:
                    return False
            evidence.append(source)
        state = {"summary": progress.summary, "evidence": evidence}
        session.merge(ConversationIssueProgressModel(state_key=key, state_json=json.dumps(state, ensure_ascii=False),
            updated_at=datetime.now(timezone.utc)))
        session.commit()
    return True
