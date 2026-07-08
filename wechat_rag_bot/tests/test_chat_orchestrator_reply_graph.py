import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.schemas.chat import ChatRequest
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.policy import PolicyDecision
from app.schemas.reply import FinalReply
from app.schemas.state import UserState


def _intent() -> IntentResult:
    return IntentResult(
        route="rag_answer",
        primary_intent="care_question",
        confidence=0.9,
        need_rag=True,
    )


def _message() -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_001",
        channel="api",
        user_id="user_001",
        session_id="session_001",
        message="hello",
        kb_id="kb_default",
    )


def _reply(answer: str) -> FinalReply:
    return FinalReply(
        answer=answer,
        reply_type="rag",
        route="rag_answer",
        sources=[{"doc_id": "doc_1", "file_name": "doc.md"}],
        usage={"tokens": 1},
        metadata={"path": answer},
    )


def _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator):
    async def normalize_chat_request(request):
        del request
        return _message()

    async def get_user_state(user_id, session_id):
        return UserState(user_id=user_id, session_id=session_id)

    async def check_rules(message, user_state):
        del message, user_state
        return None

    async def retrieve_intent_examples(message, top_k):
        del message, top_k
        return []

    async def classify_intent(message, user_state, candidates):
        del message, user_state, candidates
        return _intent()

    async def decide_route(intent, user_state, message):
        del intent, user_state, message
        return PolicyDecision(route="rag_answer", reason="test_policy")

    async def noop(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(chat_orchestrator, "normalize_chat_request", normalize_chat_request)
    monkeypatch.setattr(chat_orchestrator, "get_user_state", get_user_state)
    monkeypatch.setattr(chat_orchestrator, "check_rules", check_rules)
    monkeypatch.setattr(chat_orchestrator, "retrieve_intent_examples", retrieve_intent_examples)
    monkeypatch.setattr(chat_orchestrator, "classify_intent", classify_intent)
    monkeypatch.setattr(chat_orchestrator, "decide_route", decide_route)
    monkeypatch.setattr(chat_orchestrator, "update_user_state", noop)
    monkeypatch.setattr(chat_orchestrator, "append_conversation_memory", noop)
    monkeypatch.setattr(chat_orchestrator, "update_profile_after_chat", noop)
    monkeypatch.setattr(chat_orchestrator, "record_chat_log", noop)
    monkeypatch.setattr(chat_orchestrator, "record_ai_turn", noop)


@pytest.mark.asyncio
async def test_reply_graph_disabled_uses_legacy_build_reply(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    calls = {"legacy": 0, "graph": 0}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(reply_graph_enabled=False, intent_example_top_k=5),
    )

    async def legacy_build_reply(
        route,
        intent,
        message,
        user_state,
        stage_latencies,
        policy_decision=None,
    ):
        del route, intent, message, user_state, policy_decision
        calls["legacy"] += 1
        stage_latencies["talk_script_ms"] = 0
        stage_latencies["template_ms"] = 0
        stage_latencies["rag_ms"] = 0
        return _reply("legacy")

    async def graph_build_reply(**kwargs):
        del kwargs
        calls["graph"] += 1
        return _reply("graph")

    fake_module = ModuleType("app.services.reply_workflow_graph")
    fake_module.build_reply_with_graph = graph_build_reply
    monkeypatch.setitem(sys.modules, "app.services.reply_workflow_graph", fake_module)
    monkeypatch.setattr(chat_orchestrator, "_build_reply", legacy_build_reply)

    result = await chat_orchestrator.handle_chat(
        ChatRequest(channel="api", user_id="user_001", message="hello", kb_id="kb_default")
    )

    assert calls == {"legacy": 1, "graph": 0}
    assert result["answer"] == "legacy"
    assert result["route"] == "rag_answer"
    assert result["reply_type"] == "rag"
    assert result["sources"] == [{"doc_id": "doc_1", "file_name": "doc.md"}]
    assert result["usage"] == {"tokens": 1}
    assert result["need_human"] is False
    assert result["next_action"] is None
    assert result["trace_id"] == "trace_001"
    assert result["metadata"]["path"] == "legacy"
    assert "tag_result" in result["metadata"]
    assert "policy_decision" in result["metadata"]
    assert result["handoff"] is None


@pytest.mark.asyncio
async def test_reply_graph_enabled_calls_graph_builder(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    calls = {"legacy": 0, "graph": 0}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(reply_graph_enabled=True, intent_example_top_k=5),
    )

    async def legacy_build_reply(*args, **kwargs):
        del args, kwargs
        calls["legacy"] += 1
        return _reply("legacy")

    async def graph_build_reply(**kwargs):
        calls["graph"] += 1
        assert kwargs["route"] == "rag_answer"
        assert kwargs["intent"].primary_intent == "care_question"
        assert kwargs["message"].trace_id == "trace_001"
        assert kwargs["user_state"].user_id == "user_001"
        kwargs["stage_latencies"]["talk_script_ms"] = 0
        kwargs["stage_latencies"]["template_ms"] = 0
        kwargs["stage_latencies"]["rag_ms"] = 0
        return _reply("graph")

    fake_module = ModuleType("app.services.reply_workflow_graph")
    fake_module.build_reply_with_graph = graph_build_reply
    monkeypatch.setitem(sys.modules, "app.services.reply_workflow_graph", fake_module)
    monkeypatch.setattr(chat_orchestrator, "_build_reply", legacy_build_reply)

    result = await chat_orchestrator.handle_chat(
        ChatRequest(channel="api", user_id="user_001", message="hello", kb_id="kb_default")
    )

    assert calls == {"legacy": 0, "graph": 1}
    assert result["answer"] == "graph"
    assert set(result) == {
        "answer",
        "answer_segments",
        "session_id",
        "sources",
        "usage",
        "reply_type",
        "route",
        "intent",
        "template",
        "need_human",
        "next_action",
        "trace_id",
        "metadata",
        "handoff",
    }


@pytest.mark.asyncio
async def test_orchestrator_uses_sales_stage_decision_for_state_updates(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    captured = {}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(reply_graph_enabled=False, intent_example_top_k=5),
    )

    async def classify_intent(message, user_state, candidates):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="ask_price",
            sales_stage="price_discussed",
            confidence=0.9,
            need_template=True,
        )

    async def decide_route(intent, user_state, message):
        del intent, user_state, message
        return PolicyDecision(route="template_reply", reason="test_policy")

    async def legacy_build_reply(
        route,
        intent,
        message,
        user_state,
        stage_latencies,
        policy_decision=None,
    ):
        del route, intent, message, user_state, policy_decision
        stage_latencies["talk_script_ms"] = 0
        stage_latencies["template_ms"] = 0
        stage_latencies["rag_ms"] = 0
        return FinalReply(
            answer="price reply",
            reply_type="template",
            route="template_reply",
            metadata={},
        )

    async def update_user_state(user_id, session_id, intent, reply):
        del reply
        captured["state_update"] = {
            "user_id": user_id,
            "session_id": session_id,
            "sales_stage": intent.sales_stage,
        }

    async def update_profile_after_chat(message, intent, reply):
        del message, reply
        captured["profile_stage"] = intent.sales_stage

    monkeypatch.setattr(chat_orchestrator, "classify_intent", classify_intent)
    monkeypatch.setattr(chat_orchestrator, "decide_route", decide_route)
    monkeypatch.setattr(chat_orchestrator, "_build_reply", legacy_build_reply)
    monkeypatch.setattr(chat_orchestrator, "update_user_state", update_user_state)
    monkeypatch.setattr(chat_orchestrator, "update_profile_after_chat", update_profile_after_chat)

    result = await chat_orchestrator.handle_chat(
        ChatRequest(channel="api", user_id="user_001", message="多少钱", kb_id="kb_default")
    )

    assert result["intent"]["sales_stage"] == "need_discovery"
    assert captured["state_update"]["sales_stage"] == "need_discovery"
    assert captured["profile_stage"] == "need_discovery"
