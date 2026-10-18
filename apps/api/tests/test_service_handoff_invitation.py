import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.conversations.services import conversation_service, service_handoff_service as offers
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.agent import ServiceHandoffAssessment
from app.domains.decisioning.services import agent_runtime
from app.infrastructure.database.models import ConversationMessageModel
from app.integrations.eyun.services.message_risk_control_service import _outbound_messages


@pytest.fixture(autouse=True)
def isolated_conversation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_log_db_url", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setattr(agent_runtime, "get_saved_flow", lambda scope: None)
    monkeypatch.setattr(agent_runtime, "is_sop_node_handoff_enabled", lambda scope, node: False)


def message(text, **kwargs):
    return NormalizedMessage(trace_id=uuid4().hex, channel="api", user_id="offer-customer",
        session_id="owner-1", message=text, kb_id="kb_default",
        metadata={"sop_scope": "general", "suppress_handoff_notification": True}, **kwargs)


async def reply_to(monkeypatch, text, *, assessment=None, need_human=False, tools=None, incoming=None, answer="好的，我继续帮您解答。"):
    async def generate(*args, **kwargs):
        return {"data": {
            "commercial_judgment": "处理客户当前问题", "relationship_purpose": "帮助客户解决疑问",
            "sop_node": "general.reply", "service_handoff": assessment or {},
            "tool_calls": tools or [],
            "final_response": None if tools else {
                "messages": [{"type": "text", "content": answer}],
                "need_human": need_human,
            },
        }}
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    return await agent_runtime.run_sales_agent(message=incoming or message(text),
        user_state=UserState(user_id="offer-customer"), workspace={})


@pytest.mark.parametrize("text", ["我要转人工", "请帮我找业务主管", "换真人客服", "人工", "找客服", "能联系一下负责人吗"])
def test_explicit_request(text):
    assert offers.confirmed_handoff_reason(message(text), {}) == "customer_requested_human"


@pytest.mark.parametrize("text", ["不用转人工", "不需要你帮我找主管", "先不要找客服", "如果没解决再找主管",
    "转人工是什么意思", "不需要人工", "没有必要找主管", "你刚才说可以找主管", "我家里种了人工培育的兰花", "好的", "可以", "需要"])
def test_no_request_from_negation_or_ordinary_agreement(text):
    assert offers.confirmed_handoff_reason(message(text), {}) is None


@pytest.mark.asyncio
async def test_same_issue_followups_and_photos_do_not_repeat_invitation(monkeypatch):
    first = await reply_to(monkeypatch, "明白了，谢谢", assessment={
        "issue_summary": "叶片发黄的处理", "invitation_reason": "closing", "evidence": "明白了"})
    assert first.answer.count(offers.CLOSING_INVITATION) == 1
    assert first.need_human is False
    assert {"type": "text", "content": offers.CLOSING_INVITATION} in _outbound_messages(first.model_dump())
    for text in ("我再补一张照片", "还是这片叶子的问题", "浇水这部分我明白了"):
        reply = await reply_to(monkeypatch, text, assessment={
            "issue_summary": "叶片发黄的处理", "invitation_reason": "closing", "evidence": text})
        assert offers.CLOSING_INVITATION not in reply.answer
        assert reply.need_human is False
    assert not offers.load_service_handoff(message("好的"))["awaiting"]


@pytest.mark.asyncio
async def test_model_written_invitation_is_deduplicated_without_handoff(monkeypatch):
    first = await reply_to(monkeypatch, "问题解决了", assessment={"invitation_reason": "closing"},
        answer=offers.CLOSING_INVITATION)
    assert first.answer.count(offers.CLOSING_INVITATION) == 1 and not first.need_human
    second = await reply_to(monkeypatch, "再补一点情况", assessment={"invitation_reason": "closing"},
        answer=offers.CLOSING_INVITATION)
    assert offers.CLOSING_INVITATION not in second.answer and not second.need_human
    assert second.answer


@pytest.mark.asyncio
@pytest.mark.parametrize("acceptance", ["好的", "可以", "需要"])
async def test_confirmation_enters_existing_handoff_state(monkeypatch, acceptance):
    await reply_to(monkeypatch, "已经解决了", assessment={"invitation_reason": "closing"})
    incoming = message(acceptance)
    async def unexpected_model_call(*args, **kwargs):
        pytest.fail("明确确认无需再次调用模型")
    monkeypatch.setattr(agent_runtime, "generate_messages_json", unexpected_model_call)
    reply = await agent_runtime.run_sales_agent(message=incoming,
        user_state=UserState(user_id=incoming.user_id), workspace={})
    assert reply.need_human and reply.answer == offers.HANDOFF_CONFIRMATION
    assert reply.metadata["handoff"]["reason"] == "service_handoff_invitation_accepted"
    await conversation_service.record_ai_turn(message=incoming,
        result={**reply.model_dump(), "handoff": reply.metadata["handoff"]})
    conversation = await conversation_service.get_conversation_detail(
        conversation_service.make_conversation_id(incoming.channel, incoming.user_id, incoming.session_id))
    assert conversation["conversation"]["status"] == conversation_service.HANDOFF_PENDING
    assert not offers.load_service_handoff(incoming)["awaiting"]


@pytest.mark.asyncio
async def test_decline_continues_ai_and_only_new_escalation_renews_offer(monkeypatch):
    await reply_to(monkeypatch, "你说的没用", assessment={
        "issue_summary": "养护处理", "invitation_reason": "dissatisfied", "evidence": "你说的没用"})
    decline = await reply_to(monkeypatch, "先不用", assessment={"invitation_response": "decline", "evidence": "先不用"})
    assert not decline.need_human and offers.DISSATISFIED_INVITATION not in decline.answer
    repeat = await reply_to(monkeypatch, "你说的没用", assessment={
        "invitation_reason": "escalated", "evidence": "你说的没用"})
    assert offers.DISSATISFIED_INVITATION not in repeat.answer
    escalation = await reply_to(monkeypatch, "照做了两次还是不行，越来越糟了", assessment={
        "invitation_reason": "still_unresolved", "evidence": "照做了两次还是不行，越来越糟了"})
    assert not escalation.need_human and escalation.answer.count(offers.DISSATISFIED_INVITATION) == 1
    ignored = await reply_to(monkeypatch, "很不满意", assessment={"invitation_reason": "escalated", "evidence": "很不满意"})
    assert offers.DISSATISFIED_INVITATION not in ignored.answer


@pytest.mark.asyncio
async def test_new_issue_can_invite_again_but_needs_current_evidence(monkeypatch):
    await reply_to(monkeypatch, "发黄明白了", assessment={"issue_summary": "叶子发黄", "invitation_reason": "closing"})
    invalid = await reply_to(monkeypatch, "补张图片", assessment={
        "issue_relation": "new", "issue_summary": "订单问题", "invitation_reason": "closing", "evidence": "订单未收到"})
    assert offers.CLOSING_INVITATION not in invalid.answer
    new_issue = await reply_to(monkeypatch, "订单一直没收到，你们怎么回事", assessment={
        "issue_relation": "new", "issue_summary": "订单未收到", "invitation_reason": "dissatisfied", "evidence": "订单一直没收到"})
    assert offers.DISSATISFIED_INVITATION in new_issue.answer
    assert offers.load_service_handoff(message(""))["issue_summary"] == "订单未收到"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_call", [False, True])
async def test_invitation_does_not_execute_premature_handoff(monkeypatch, tool_call):
    tools = [{"call_id": "handoff", "name": "human.handoff", "arguments": {"reason": "不满", "summary": "客户不满"}}] if tool_call else None
    reply = await reply_to(monkeypatch, "你没解答清楚", assessment={
        "invitation_reason": "dissatisfied", "evidence": "你没解答清楚"}, need_human=True, tools=tools)
    assert not reply.need_human
    assert offers.DISSATISFIED_INVITATION in reply.answer
    assert "handoff" not in reply.metadata


@pytest.mark.asyncio
async def test_no_pending_invitation_cannot_be_accepted(monkeypatch):
    reply = await reply_to(monkeypatch, "好的", assessment={"invitation_response": "accept", "evidence": "好的"}, need_human=True)
    assert not reply.need_human


@pytest.mark.asyncio
async def test_fallback_consumes_previous_invitation(monkeypatch):
    await reply_to(monkeypatch, "问题解决了", assessment={"invitation_reason": "closing"})
    async def unavailable(*args, **kwargs):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(agent_runtime, "generate_messages_json", unavailable)
    await agent_runtime.run_sales_agent(message=message("我再补一张图"),
        user_state=UserState(user_id="offer-customer"), workspace={})
    assert not offers.load_service_handoff(message("好的"))["awaiting"]


def test_semantic_confirmation_must_refer_to_current_invitation():
    state = {"awaiting": True}
    acceptance = ServiceHandoffAssessment(invitation_response="accept", evidence="让经验丰富的同事接着帮我看看")
    assert offers.confirmed_handoff_reason(message(acceptance.evidence), state, acceptance)
    assert offers.confirmed_handoff_reason(message("先不用"), state, acceptance) is None
    assert offers.confirmed_handoff_reason(message("这个建议可以"), state) is None
    assert offers.invitation_reply("转人工是什么意思") == "none"
    assert offers.confirmed_handoff_reason(message("好的，我还有订单问题"), state,
        acceptance.model_copy(update={"issue_relation": "new"})) is None


def test_invitation_is_durable_and_isolated_by_conversation():
    incoming = message("问题解决了")
    offers.save_service_handoff(incoming, {"invited": True, "awaiting": True})
    offers._factories.clear()
    assert offers.load_service_handoff(incoming)["invited"]
    for changes in ({"session_id": "owner-2"}, {"user_id": "other"}, {"tenant_id": "other"},
                    {"channel": "wechat"}, {"metadata": {"evaluation_id": "test"}}):
        assert not offers.load_service_handoff(incoming.model_copy(update=changes))


def add_delivery(incoming, status, *, content=offers.CLOSING_INVITATION, sender="ai"):
    with conversation_service._get_session() as session:
        session.add(ConversationMessageModel(
            conversation_id=conversation_service.make_conversation_id(incoming.channel, incoming.user_id, incoming.session_id),
            trace_id="queue-trace", sender_type=sender, content=content, delivery_status=status,
            metadata_json=json.dumps({"sales_turn_id": incoming.trace_id}), created_at=datetime.now(timezone.utc)))
        session.commit()


@pytest.mark.parametrize("status,awaiting,invited", [(None, False, False), ("queued", False, True),
    ("failed", False, False), ("cancelled", False, False), ("accepted", True, True), ("confirmed", True, True)])
def test_eyun_confirmation_requires_invitation_delivery(status, awaiting, invited):
    incoming = message("问题解决了").model_copy(update={"channel": "wechat", "metadata": {"provider": "eyun"}})
    offers.save_service_handoff(incoming, {"invited": True, "awaiting": True,
        "invitation_trace_id": incoming.trace_id, "invitation_text": offers.CLOSING_INVITATION})
    if status:
        add_delivery(incoming, status)
    state = offers.load_service_handoff(incoming)
    assert state["awaiting"] is awaiting and state["invited"] is invited


def test_intervening_human_reply_invalidates_old_confirmation():
    incoming = message("问题解决了").model_copy(update={"channel": "wechat", "metadata": {"provider": "eyun"}})
    offers.save_service_handoff(incoming, {"invited": True, "awaiting": True,
        "invitation_trace_id": incoming.trace_id, "invitation_text": offers.CLOSING_INVITATION})
    add_delivery(incoming, "accepted")
    add_delivery(incoming, "sent", content="请您再拍一张照片可以吗？", sender="human")
    assert not offers.load_service_handoff(incoming)["awaiting"]


@pytest.mark.asyncio
async def test_configured_mandatory_handoff_still_applies(monkeypatch):
    monkeypatch.setattr(agent_runtime, "is_sop_node_handoff_enabled", lambda scope, node: True)
    reply = await reply_to(monkeypatch, "问题已解决", assessment={"invitation_reason": "closing"})
    assert reply.need_human
    assert reply.metadata["agent_runtime"]["result"] == "sop_node_handoff"
