import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.talk_script.models import TalkScriptMatchResult


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_eval",
        channel="api",
        user_id="eval_user",
        session_id="eval_session",
        message=text,
        kb_id="kb_default",
    )


def _state() -> UserState:
    return UserState(user_id="eval_user", session_id="eval_session")


def _intent(route: str, primary_intent: str) -> IntentResult:
    return IntentResult(
        route=route,
        primary_intent=primary_intent,
        confidence=0.9,
        reason="eval_case",
    )


@pytest.mark.asyncio
async def test_disease_case_generates_followup_not_handoff(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "Black spots and root issues need checking watering, substrate, and ventilation first. Can you send a leaf and pot-surface photo, and tell me how often you water?",
            "sources": [],
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
        intent=_intent("rag_answer", "care_question"),
        message=_message("black spots yellow leaves root rot"),
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "rag_answer"
    assert reply.need_human is False
    assert reply.answer
    assert "photo" in reply.answer


@pytest.mark.asyncio
async def test_recommend_short_sentence_asks_sales_qualifying_question(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def fail_rag(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        raise AssertionError("transaction fallback must not enter general RAG")

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", fail_rag)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
        intent=_intent("template_reply", "order_intent"),
        message=_message("recommend one"),
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "template_reply"
    assert reply.need_human is False
    assert "更看重" in reply.answer
