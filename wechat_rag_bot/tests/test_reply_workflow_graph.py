from types import SimpleNamespace

import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.policy import PolicyDecision
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.schemas.template import TemplateItem
from app.talk_script.models import TalkScriptMatchResult


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
async def test_talk_script_matched_returns_template_reply(monkeypatch):
    from app.services import reply_workflow_graph

    async def match_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(
            status="matched",
            template_id="tpl_script",
            answer="script answer",
            confidence=0.88,
            reason="matched",
        )

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", match_talk_script)

    latencies = {}
    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies=latencies,
    )

    _assert_reply(reply)
    assert reply.route == "template_reply"
    assert reply.reply_type == "template"
    assert reply.need_human is False
    assert reply.metadata["talk_script"]["status"] == "matched"
    assert latencies["talk_script_ms"] >= 0
    assert latencies["template_ms"] == 0
    assert latencies["rag_ms"] == 0


@pytest.mark.asyncio
async def test_talk_script_handoff_returns_human(monkeypatch):
    from app.services import reply_workflow_graph

    async def match_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="handoff", reason="need_human")

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", match_talk_script)
    monkeypatch.setattr(reply_workflow_graph, "build_handoff_reply", _handoff_reply)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
        intent=_intent("template_reply"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.metadata["handoff"]["reason"] == "need_human"
    assert reply.metadata["talk_script"]["status"] == "handoff"


@pytest.mark.asyncio
async def test_rag_answer_with_answer_returns_rag_reply(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "rag answer",
            "sources": [{"doc_id": "doc_1"}],
            "usage": {"tokens": 2},
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
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
    assert reply.metadata == {}


@pytest.mark.asyncio
async def test_rag_answer_without_answer_handoffs(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "", "sources": []}

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)
    monkeypatch.setattr(reply_workflow_graph, "build_handoff_reply", _handoff_reply)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.metadata["handoff"]["reason"] == "rag_no_answer_to_handoff"
    assert reply.metadata["original_route"] == "rag_answer"


@pytest.mark.asyncio
async def test_template_reply_falls_back_to_default_template(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def select_default_template(message, intent, user_state):
        del message, intent, user_state
        return TemplateItem(
            template_id="tpl_price_objection_default",
            intent="price_objection",
            content="默认价格异议回复",
        )

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr(
        "app.services.template_reply_service.select_template",
        select_default_template,
    )

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
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
async def test_template_reply_missing_default_template_falls_back_to_rag(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "I can recommend one first. Are you looking for an easy beginner plant or a stronger fragrance?",
            "sources": [],
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
        intent=_intent("template_reply", primary_intent="unknown_intent"),
        message=_message("没有对应模板"),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "rag_answer"
    assert reply.reply_type == "rag"
    assert reply.need_human is False
    assert "recommend" in reply.answer


@pytest.mark.asyncio
async def test_template_then_rag_uses_rag_without_legacy_template(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {"answer": "rag answer", "sources": [{"doc_id": "doc_1"}]}

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_then_rag",
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
    assert reply.metadata == {}


@pytest.mark.asyncio
async def test_rag_node_passes_policy_decision_to_rag(monkeypatch):
    from app.services import reply_workflow_graph

    policy = PolicyDecision(
        route="rag_answer",
        reason="beginner_orchid_care_policy",
        knowledge_base_ids=["kb_orchid_basic"],
        prompt_block_ids=["base.customer_service", "segment.beginner"],
    )

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state
        assert policy_decision == policy
        return {"answer": "rag answer", "sources": []}

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
        intent=_intent("rag_answer"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
        policy_decision=policy,
    )

    assert reply.route == "rag_answer"
    assert reply.answer == "rag answer"


@pytest.mark.asyncio
async def test_human_route_handoffs(monkeypatch):
    from app.services import reply_workflow_graph

    monkeypatch.setattr(reply_workflow_graph, "build_handoff_reply", _handoff_reply)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="human",
        intent=_intent("human"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "human"
    assert reply.reply_type == "human"
    assert reply.need_human is True
    assert reply.metadata["handoff"]["reason"] == "test_reason"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "reply_type"),
    [
        ("chitchat", "chitchat"),
        ("unsupported", "unsupported"),
        ("clarify", "clarify"),
    ],
)
async def test_simple_routes_return_existing_builder_replies(route, reply_type):
    from app.services import reply_workflow_graph

    reply = await reply_workflow_graph.build_reply_with_graph(
        route=route,
        intent=_intent(route),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == route
    assert reply.reply_type == reply_type
    assert reply.need_human is False
    assert reply.metadata == {}
