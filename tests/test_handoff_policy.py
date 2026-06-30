import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.services.policy_service import decide_route


def _message() -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="req_policy",
        channel="api",
        user_id="user_policy",
        session_id="sess_policy",
        message="乱七八糟不明确输入",
        kb_id="kb_default",
    )


@pytest.mark.asyncio
async def test_clarify_intent_is_routed_to_human_for_mvp():
    intent = IntentResult(
        route="clarify",
        primary_intent="unknown",
        confidence=0.45,
        reason="rule_no_match",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "human"
    assert decision.reason == "clarify_to_handoff"
    assert decision.fallback_route == "clarify"
    assert decision.original_route == "clarify"
    assert decision.next_action == "human_handoff"


@pytest.mark.asyncio
async def test_unsupported_intent_is_routed_to_human_for_mvp():
    intent = IntentResult(
        route="unsupported",
        primary_intent="unsupported",
        confidence=0.85,
        reason="unsupported_word",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "human"
    assert decision.reason == "unsupported_to_handoff"
    assert decision.fallback_route == "unsupported"


@pytest.mark.asyncio
async def test_low_confidence_intent_is_routed_to_human_for_mvp():
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        confidence=0.3,
        reason="low_confidence",
        need_rag=True,
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "human"
    assert decision.reason == "low_confidence_to_handoff"
    assert decision.fallback_route == "rag_answer"
