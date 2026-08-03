from types import SimpleNamespace

import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.decisioning.schemas.reply_plan import BusinessFacts, ReplyPlan
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.template import TemplateItem


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

    monkeypatch.setattr("app.domains.knowledge.services.rag_service.answer_knowledge", answer_knowledge)

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
    assert reply.metadata["persona"]["render_mode"] == "persona"
    assert reply.metadata["persona"]["version"] == "v1.1"


@pytest.mark.asyncio
async def test_rag_reply_is_persona_rendered_before_reply_guard(monkeypatch):
    from app.services import reply_workflow_graph

    order = []
    grounded_answer = "浇水不要固定看天数，要根据植料干湿程度判断。"
    original_guard = reply_workflow_graph.guard_reply_spec

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": grounded_answer, "sources": [{"doc_id": "care_1"}]}

    async def render_persona_reply(*, spec, context, current_message):
        del context, current_message
        order.append("persona")
        assert spec.render_mode == "persona"
        assert spec.verified_facts["grounded_knowledge_answer"] == grounded_answer
        return spec.model_copy(
            update={
                "suggested_copy": "浇水别固定看几天，主要看植料干湿。",
                "metadata": {
                    **spec.metadata,
                    "persona": {"render_mode": "persona", "rendered": True},
                },
            }
        )

    def guard_reply_spec(*, spec, context):
        order.append("guard")
        assert spec.suggested_copy == "浇水别固定看几天，主要看植料干湿。"
        return original_guard(spec=spec, context=context)

    monkeypatch.setattr(
        "app.domains.knowledge.services.rag_service.answer_knowledge",
        answer_knowledge,
    )
    monkeypatch.setattr(reply_workflow_graph, "render_persona_reply", render_persona_reply)
    monkeypatch.setattr(reply_workflow_graph, "guard_reply_spec", guard_reply_spec)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer"),
        intent=_intent("rag_answer"),
        message=_message("下次几天浇水？"),
        user_state=_state(),
        stage_latencies={},
    )

    assert order == ["persona", "guard"]
    assert reply.answer == "浇水别固定看几天，主要看植料干湿。"


@pytest.mark.asyncio
async def test_rag_answer_without_answer_continues_with_llm(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "", "sources": []}

    async def generate_messages(messages, **kwargs):
        del messages, kwargs
        return {"answer": "我先按您说的情况继续帮您看。", "usage": {}}

    async def fail_handoff(**kwargs):
        del kwargs
        raise AssertionError("ordinary knowledge miss must not create a handoff")

    monkeypatch.setattr("app.domains.knowledge.services.rag_service.answer_knowledge", answer_knowledge)
    monkeypatch.setattr(reply_workflow_graph, "generate_messages", generate_messages)
    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", fail_handoff)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer"),
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "rag_answer"
    assert reply.reply_type == "llm_fallback"
    assert reply.need_human is False
    assert reply.answer == "我先按您说的情况继续帮您看。"
    assert reply.metadata["llm_fallback"]["reason"] == "rag_no_answer_to_handoff"


@pytest.mark.asyncio
async def test_demo_rag_no_answer_uses_llm_without_creating_handoff(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "", "sources": []}

    async def generate_messages(messages, **kwargs):
        assert messages[0]["role"] == "system"
        assert kwargs["purpose"] == "business"
        return {"answer": "可以的，您更看重好养还是花香？", "usage": {"tokens": 8}}

    async def fail_handoff(**kwargs):
        del kwargs
        raise AssertionError("demo fallback must not create a handoff")

    monkeypatch.setattr(
        "app.domains.knowledge.services.rag_service.answer_knowledge",
        answer_knowledge,
    )
    monkeypatch.setattr(reply_workflow_graph, "generate_messages", generate_messages)
    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", fail_handoff)
    message = _message("有没有适合新手的？").model_copy(
        update={"channel": "web_demo", "metadata": {"demo": True}}
    )

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("rag_answer"),
        intent=_intent("rag_answer"),
        message=message,
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.answer == "可以的，您更看重好养还是花香？"
    assert reply.route == "rag_answer"
    assert reply.reply_type == "llm_fallback"
    assert reply.need_human is False
    assert reply.metadata["demo_llm_fallback"]["reason"] == "rag_no_answer_to_handoff"


@pytest.mark.asyncio
async def test_demo_explicit_human_request_still_hands_off(monkeypatch):
    from app.services import reply_workflow_graph

    async def request_handoff(**kwargs):
        del kwargs
        return SimpleNamespace(status="pending")

    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", request_handoff)
    message = _message("转人工").model_copy(
        update={"channel": "web_demo", "metadata": {"demo": True}}
    )

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("human", need_human=True),
        intent=_intent("human", primary_intent="human_request"),
        message=message,
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "human"
    assert reply.need_human is True


@pytest.mark.asyncio
async def test_demo_policy_handoff_uses_llm_when_intent_did_not_request_human(
    monkeypatch,
):
    from app.services import reply_workflow_graph

    async def generate_messages(messages, **kwargs):
        del messages, kwargs
        return {"answer": "我先确认一下，您具体想了解哪一方面？", "usage": {}}

    async def fail_handoff(**kwargs):
        del kwargs
        raise AssertionError("demo policy fallback must not create a handoff")

    monkeypatch.setattr(reply_workflow_graph, "generate_messages", generate_messages)
    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", fail_handoff)
    message = _message("不明确输入").model_copy(
        update={"channel": "web_demo", "metadata": {"demo": True}}
    )

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("human", original_route="clarify"),
        intent=_intent("human", primary_intent="unknown"),
        message=message,
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.answer == "我先确认一下，您具体想了解哪一方面？"
    assert reply.route == "clarify"
    assert reply.need_human is False


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
        "app.domains.decisioning.services.template_reply_service.select_template",
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
async def test_template_reply_missing_default_template_continues_with_llm(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(*args, **kwargs):
        del args, kwargs
        raise AssertionError("transaction template miss must not call generic RAG")

    async def generate_messages(messages, **kwargs):
        del messages, kwargs
        return {"answer": "好的，我接着帮您处理。", "usage": {}}

    async def fail_handoff(**kwargs):
        del kwargs
        raise AssertionError("ordinary template miss must not create a handoff")

    monkeypatch.setattr("app.domains.knowledge.services.rag_service.answer_knowledge", answer_knowledge)
    monkeypatch.setattr(reply_workflow_graph, "generate_messages", generate_messages)
    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", fail_handoff)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan("template_reply"),
        intent=_intent("template_reply", primary_intent="unknown_intent"),
        message=_message("没有对应模板"),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "template_reply"
    assert reply.reply_type == "llm_fallback"
    assert reply.need_human is False
    assert reply.answer == "好的，我接着帮您处理。"
    assert reply.metadata["llm_fallback"]["reason"] == "template_not_found_to_handoff"


@pytest.mark.asyncio
async def test_rag_plan_uses_rag_without_legacy_template(monkeypatch):
    from app.services import reply_workflow_graph

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "rag answer", "sources": [{"doc_id": "doc_1"}]}

    monkeypatch.setattr("app.domains.knowledge.services.rag_service.answer_knowledge", answer_knowledge)

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
    assert reply.metadata["persona"]["render_mode"] == "persona"
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

    monkeypatch.setattr("app.domains.knowledge.services.rag_service.answer_knowledge", answer_knowledge)

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
async def test_unanswerable_routes_continue_without_handoff(monkeypatch, route):
    from app.services import reply_workflow_graph

    async def generate_messages(messages, **kwargs):
        del messages, kwargs
        return {"answer": "您接着说，我继续帮您。", "usage": {}}

    async def fail_handoff(**kwargs):
        del kwargs
        raise AssertionError("ordinary unanswerable route must not create a handoff")

    monkeypatch.setattr(reply_workflow_graph, "generate_messages", generate_messages)
    monkeypatch.setattr(reply_workflow_graph, "request_human_handoff", fail_handoff)

    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan(route),
        intent=_intent(route),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == route
    assert reply.reply_type == "llm_fallback"
    assert reply.need_human is False
    assert reply.answer == "您接着说，我继续帮您。"


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
