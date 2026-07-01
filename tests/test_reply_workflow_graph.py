from types import SimpleNamespace

import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.schemas.template import TemplateItem, TemplateReply
from app.talk_script.models import TalkScriptMatchResult


def _message() -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_001",
        channel="api",
        user_id="user_001",
        session_id="session_001",
        message="hello",
        kb_id="kb_default",
    )


def _state() -> UserState:
    return UserState(user_id="user_001", session_id="session_001")


def _intent(route: str) -> IntentResult:
    return IntentResult(
        route=route,
        primary_intent="care_question",
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

    async def answer_knowledge(message, user_state):
        del message, user_state
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

    async def answer_knowledge(message, user_state):
        del message, user_state
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
async def test_template_reply_missing_template_handoffs(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def select_template(message, intent, user_state):
        del message, intent, user_state
        return None

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr(reply_workflow_graph, "select_template", select_template)
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
    assert reply.metadata["handoff"]["reason"] == "template_not_found_to_handoff"


@pytest.mark.asyncio
async def test_template_then_rag_returns_combined_reply(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def select_template(message, intent, user_state):
        del message, intent, user_state
        return TemplateItem(
            template_id="tpl_001",
            intent="care_question",
            content="template answer",
        )

    async def render_template(template, message, user_state):
        del message, user_state
        return TemplateReply(answer=template.content, template_id=template.template_id)

    async def answer_knowledge(message, user_state):
        del message, user_state
        return {"answer": "rag answer", "sources": [{"doc_id": "doc_1"}]}

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr(reply_workflow_graph, "select_template", select_template)
    monkeypatch.setattr(reply_workflow_graph, "render_template", render_template)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_then_rag",
        intent=_intent("template_then_rag"),
        message=_message(),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "template_then_rag"
    assert reply.reply_type == "template_then_rag"
    assert reply.need_human is False
    assert reply.template_id == "tpl_001"
    assert reply.sources == [{"doc_id": "doc_1"}]
    assert reply.metadata == {}


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
