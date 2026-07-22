from types import SimpleNamespace

import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.reply_plan import BusinessFacts, ReplyPlan
from app.schemas.state import UserState
from app.schemas.template import TemplateItem


def _message(text: str = "hello") -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_001",
        channel="api",
        user_id="user_001",
        session_id="session_001",
        message=text,
        kb_id="kb_default",
    )


def _state() -> UserState:
    return UserState(user_id="user_001", session_id="session_001")


def _intent(route: str, primary_intent: str = "care_question") -> IntentResult:
    return IntentResult(
        route=route,
        primary_intent=primary_intent,
        confidence=0.9,
        reason="test_reason",
        slots={"original_route": route},
    )


def _plan(action: str, **updates) -> ReplyPlan:
    return ReplyPlan(
        action=action,
        reason="test_plan",
        original_route=updates.pop("original_route", action),
        **updates,
    )


def _assert_reply(reply: FinalReply) -> None:
    assert isinstance(reply, FinalReply)
    assert isinstance(reply.metadata, dict)


async def _handoff_reply(**kwargs) -> FinalReply:
    return FinalReply(
        answer="",
        reply_type="human",
        route="human",
        need_human=True,
        next_action="human_handoff",
        metadata={
            "handoff": {"status": "pending", "reason": kwargs["reason"]},
            "original_route": kwargs.get("original_route"),
            **(kwargs.get("context") or {}),
        },
    )


@pytest.mark.asyncio
async def test_rag_answer_with_answer_returns_rag_reply(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "rag answer",
            "sources": [{"doc_id": "doc_1"}],
            "usage": {"tokens": 2},
        }

    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer"),
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "rag_answer"
    assert reply.reply_type == "rag"
    assert reply.need_human is False
    assert reply.sources == [{"doc_id": "doc_1"}]
    assert reply.metadata["persona"]["render_mode"] == "locked"
    assert reply.metadata["persona"]["version"] == "v1.1"


@pytest.mark.asyncio
async def test_rag_answer_without_answer_silently_hands_off(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "", "sources": []}

    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer"),
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.answer == ""
    assert reply.metadata["handoff"]["reason"] == "rag_no_answer_to_handoff"


@pytest.mark.asyncio
async def test_template_reply_falls_back_to_default_template(monkeypatch):
    from app.services import reply_workflow_graph

    async def select_default_template(message, intent, user_state):
        del message, intent, user_state
        return TemplateItem(
            template_id="tpl_price_objection_default",
            intent="price_objection",
            content="默认价格异议回复",
        )

    monkeypatch.setattr(
        "app.services.template_reply_service.select_template",
        select_default_template,
    )

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("template_reply"),
        intent=_intent("template_reply", primary_intent="price_objection"),
        message=_message("这个有点贵"),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "template_reply"
    assert reply.reply_type == "template"
    assert reply.need_human is False
    assert reply.template_id == "tpl_price_objection_default"


@pytest.mark.asyncio
async def test_plan_with_business_facts_uses_grounded_renderer(monkeypatch):
    from app.services import reply_workflow_graph

    async def render(message, facts):
        del message
        assert facts.tool_state == {"payment_status": "failed"}
        return FinalReply(
            answer="系统当前显示支付失败，请先核对扣款状态。",
            reply_type="template",
            route="template_reply",
        )

    monkeypatch.setattr(reply_workflow_graph, "render_business_reply", render)
    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan(
            "template_reply",
            business_facts=BusinessFacts(tool_state={"payment_status": "failed"}),
        ),
        intent=_intent("template_reply", primary_intent="payment_intent"),
        message=_message("我刚付过了，怎么还显示失败？"),
        user_state=_state(),
        stage_latencies={},
    )

    assert "支付失败" in reply.answer


@pytest.mark.asyncio
async def test_template_reply_missing_default_template_silently_hands_off(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(*args, **kwargs):
        del args, kwargs
        raise AssertionError("transaction template miss must not call generic RAG")

    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("template_reply"),
        intent=_intent("template_reply", primary_intent="unknown_intent"),
        message=_message("没有对应模板"),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.answer == ""
    assert reply.metadata["handoff"]["reason"] == "template_not_found_to_handoff"


@pytest.mark.asyncio
async def test_rag_plan_uses_rag_without_legacy_template(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "rag answer", "sources": [{"doc_id": "doc_1"}]}

    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer", original_route="template_then_rag"),
        intent=_intent("template_then_rag"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "rag_answer"
    assert reply.reply_type == "rag"
    assert reply.need_human is False
    assert reply.sources == [{"doc_id": "doc_1"}]
    assert reply.metadata["persona"]["render_mode"] == "locked"
    assert reply.metadata["persona"]["version"] == "v1.1"


@pytest.mark.asyncio
async def test_rag_node_passes_policy_decision_to_rag(monkeypatch):
    from app.services import reply_workflow_graph

    plan = ReplyPlan(
        action="rag_answer",
        original_route="rag_answer",
        reason="beginner_orchid_care_policy",
        knowledge_base_ids=["kb_orchid_basic"],
        prompt_block_ids=["base.customer_service", "segment.beginner"],
    )

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state
        assert policy_decision is not None
        assert policy_decision.reason == plan.reason
        assert policy_decision.knowledge_base_ids == plan.knowledge_base_ids
        assert policy_decision.prompt_block_ids == plan.prompt_block_ids
        return {"answer": "rag answer", "sources": []}

    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=plan,
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "rag_answer"
    assert reply.answer == "rag answer"


@pytest.mark.asyncio
async def test_human_route_handoffs(monkeypatch):
    from app.services import reply_workflow_graph

    monkeypatch.setattr(reply_workflow_graph, "build_handoff_reply", _handoff_reply)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("human", need_human=True, next_action="human_handoff"),
        intent=_intent("human"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.metadata["handoff"]["reason"] == "test_plan"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["unsupported", "clarify"])
async def test_unanswerable_routes_silently_hand_off(route):
    from app.services import reply_workflow_graph

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan(route),
        intent=_intent(route),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.answer == ""


@pytest.mark.asyncio
async def test_chitchat_route_keeps_normal_reply():
    from app.services import reply_workflow_graph

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("chitchat"),
        intent=_intent("chitchat"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "chitchat"
    assert reply.reply_type == "chitchat"
    assert reply.need_human is False
    assert reply.answer
