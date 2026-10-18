import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.agent import ServiceHandoffAssessment
from app.infrastructure.database.models import Base, ConversationMessageModel, ConversationServiceHandoffModel


CLOSING_INVITATION = "如果还有没解释清楚或没解决的地方，我可以请业务主管进一步为您解答。您需要我帮您转接吗？"
DISSATISFIED_INVITATION = "抱歉，刚才的解答没能帮您解决好问题。我可以请业务主管接着帮您处理，您看需要转接吗？"
HANDOFF_CONFIRMATION = "好的，我这边为您转接人工，接着帮您处理。"
_factories: dict[str, sessionmaker] = {}
_REQUEST = re.compile(r"(?:转接?|找|需要|要|叫|联系|换|请)(?:一下|一个|一位|个|你们的?|我们|位)?(?:人工(?:客服)?|真人(?:客服)?|客服|(?:业务)?主管|负责人)")
_NEGATIVE = re.compile(r"(?:不(?:需要|用|要|想|必|是)?|别|无需|先不|暂不|没(?:有)?必要|并非)(?:再|麻烦|帮我|给我|你们?|您)*$")


def directly_requests_human(text: str) -> bool:
    if re.fullmatch(r"\s*(?:人工|人工客服|真人客服|转人工|转接人工)[。！!\s]*", text):
        return True
    for clause in re.split(r"[，。！？；,!?;\n]", re.sub(r"\s+", "", text)):
        for match in _REQUEST.finditer(clause):
            prefix = clause[:match.start()]
            if _NEGATIVE.search(prefix) or any(word in prefix for word in ("如果", "假如", "要是", "等会", "到时候")):
                continue
            if re.search(r"(?:你(?:刚才)?说|提到|写着)(?:可以|能|要)?$", prefix):
                continue
            if re.search(r"(?:不用|不要|不需要|算了|先不|是什么意思|是什么|怎么理解|这个词)", clause[match.end():]):
                continue
            return True
    return False


def invitation_reply(text: str) -> str:
    normalized = re.sub(r"[\s，。！？、,.!？~～]", "", text)
    if re.fullmatch(r"(?:先|暂时|现在)?(?:不用|不需要|不要|不必|先不|暂不|算了)(?:了|转|转接|转人工|找主管|麻烦了)?(?:谢谢)?", normalized):
        return "decline"
    if (_REQUEST.search(normalized) and not directly_requests_human(normalized)
            and re.search(r"不用|不需要|不要|不必|别|无需|先不|暂不|没(?:有)?必要", normalized)):
        return "decline"
    if re.fullmatch(r"(?:好的?|可以|行|需要|要|同意|嗯|是的|帮我转(?:一下)?|麻烦(?:您|你)转(?:一下)?)(?:的|吧|呀|啊)?(?:谢谢|麻烦您了|麻烦你了)?", normalized):
        return "accept"
    return "none"


def _key(message: NormalizedMessage) -> str:
    identity = [message.channel, message.tenant_id, message.user_id, message.session_id, message.metadata.get("evaluation_id")]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _session():
    url = get_settings().chat_log_db_url
    if url not in _factories:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[ConversationServiceHandoffModel.__table__])
        _factories[url] = sessionmaker(bind=engine, expire_on_commit=False)
    return _factories[url]()


def load_service_handoff(message: NormalizedMessage) -> dict:
    with _session() as session:
        row = session.get(ConversationServiceHandoffModel, _key(message))
        state = json.loads(row.state_json) if row else {}
    # A queued or failed invitation is not a question the customer has received.
    if state.get("awaiting") and message.metadata.get("provider") == "eyun" and not message.metadata.get("evaluation_id"):
        from app.domains.conversations.services.conversation_service import _get_session, make_conversation_id
        with _get_session() as session:
            rows = session.scalars(select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == make_conversation_id(message.channel, message.user_id, message.session_id),
                ConversationMessageModel.sender_type == "ai",
                or_(ConversationMessageModel.trace_id == state.get("invitation_trace_id"),
                    ConversationMessageModel.metadata_json.contains(str(state.get("invitation_trace_id") or "missing"))),
                ConversationMessageModel.content.contains(state.get("invitation_text") or "missing"),
            )).all()
            delivered = any(row.delivery_status in {"sent", "accepted", "confirmed", "unconfirmed", "delivery_unknown"} for row in rows)
            if not delivered:
                state["awaiting"] = False
                if not rows or all(row.delivery_status in {"failed", "cancelled", "revoked"} for row in rows):
                    state["invited"] = False
            else:
                latest_reply = session.scalars(select(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == make_conversation_id(message.channel, message.user_id, message.session_id),
                    ConversationMessageModel.sender_type.in_(["ai", "human"]),
                    or_(ConversationMessageModel.delivery_status.is_(None),
                        ConversationMessageModel.delivery_status.not_in(["failed", "cancelled", "revoked"])),
                ).order_by(ConversationMessageModel.id.desc()).limit(1)).first()
                if latest_reply and latest_reply.id not in {row.id for row in rows}:
                    state["awaiting"] = False
    return state


def save_service_handoff(message: NormalizedMessage, state: dict) -> None:
    with _session() as session:
        session.merge(ConversationServiceHandoffModel(state_key=_key(message),
            state_json=json.dumps(state, ensure_ascii=False), updated_at=datetime.now(timezone.utc)))
        session.commit()


def confirmed_handoff_reason(message: NormalizedMessage, state: dict, assessment: ServiceHandoffAssessment | None = None) -> str | None:
    if message.metadata.get("system_event"):
        return None
    text = str(message.message or "")
    if directly_requests_human(text):
        return "customer_requested_human"
    response = invitation_reply(text)
    if state.get("awaiting") and response != "decline":
        if response == "accept" or (assessment and assessment.issue_relation == "same"
                and assessment.invitation_response == "accept" and assessment.evidence.strip()
                and assessment.evidence in text):
            return "service_handoff_invitation_accepted"
    return None


def advance_service_handoff(message: NormalizedMessage, state: dict, assessment: ServiceHandoffAssessment) -> tuple[dict, str]:
    state = dict(state)
    if message.metadata.get("system_event"):
        return state, ""
    text = str(message.message or "")
    evidence = assessment.evidence.strip()
    grounded = bool(evidence and evidence in text)
    was_awaiting = bool(state.get("awaiting"))
    if assessment.issue_relation == "new" and assessment.issue_summary.strip() and grounded:
        state = {"issue_summary": assessment.issue_summary.strip()}
    elif not state.get("issue_summary") and assessment.issue_summary.strip():
        state["issue_summary"] = assessment.issue_summary.strip()
    state["awaiting"] = False
    response = invitation_reply(text)
    if was_awaiting and (response == "decline" or (grounded and assessment.invitation_response == "decline")):
        state["declined"] = True
        return state, ""
    level = {"closing": 0, "dissatisfied": 1, "escalated": 2, "still_unresolved": 2}.get(assessment.invitation_reason)
    if level is None or (level > 0 and not grounded):
        return state, ""
    renewed = (state.get("declined") and assessment.invitation_reason in {"escalated", "still_unresolved"}
               and evidence != state.get("invitation_evidence"))
    if state.get("invited") and not renewed:
        return state, ""
    invitation = CLOSING_INVITATION if level == 0 else DISSATISFIED_INVITATION
    state.update(invited=True, awaiting=True, declined=False, invitation_reason=assessment.invitation_reason,
                 invitation_evidence=evidence,
                 invitation_text=invitation, invitation_trace_id=message.trace_id)
    return state, invitation
