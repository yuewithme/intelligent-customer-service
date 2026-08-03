import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.policy_service import decide_route


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
async def test_clarify_intent_continues_automatically():
    intent = IntentResult(
        route="clarify",
        primary_intent="unknown",
        confidence=0.45,
        reason="rule_no_match",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "chitchat"
    assert decision.reason == "ambiguous_continue_automatically"
    assert decision.original_route == "clarify"
    assert decision.next_action is None


@pytest.mark.asyncio
async def test_unsupported_intent_continues_automatically():
    intent = IntentResult(
        route="unsupported",
        primary_intent="unsupported",
        confidence=0.85,
        reason="unsupported_word",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "chitchat"
    assert decision.reason == "unsupported_continue_automatically"
    assert decision.original_route == "unsupported"
    assert decision.next_action is None


@pytest.mark.asyncio
async def test_unverified_llm_handoff_is_blocked():
    intent = IntentResult(
        route="human",
        primary_intent="unknown",
        confidence=0.31,
        need_human=True,
        reason="llm_uncertain",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "template_reply"
    assert decision.reason == "unverified_handoff_blocked"
    assert decision.next_action is None


@pytest.mark.asyncio
async def test_low_confidence_knowledge_intent_uses_rag_fallback():
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        confidence=0.3,
        reason="low_confidence",
        need_rag=True,
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "rag_answer"
    assert decision.reason == "low_confidence_knowledge_lookup"
    assert decision.original_route == "rag_answer"


@pytest.mark.asyncio
async def test_explicit_human_intent_still_routes_to_handoff():
    intent = IntentResult(
        route="human",
        primary_intent="refund_request",
        confidence=0.98,
        need_human=True,
        reason="rule_refund",
    )

    decision = await decide_route(intent, UserState(user_id="user_policy"), _message())

    assert decision.route == "human"
    assert decision.reason == "human_required"
    assert decision.next_action == "human_handoff"
