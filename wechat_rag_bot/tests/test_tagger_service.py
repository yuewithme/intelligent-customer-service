import pytest

from app.schemas.tag import TagResult
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.services.tagger_service import build_tag_result


def test_tag_result_defaults_are_stable():
    result = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        confidence=0.88,
    )

    assert result.intent == "orchid_care"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "neutral"
    assert result.stage == "unknown"
    assert result.risk_level == "normal"
    assert result.entities == {}
    assert result.tags == ["intent:orchid_care", "segment:beginner"]


@pytest.mark.asyncio
async def test_build_tag_result_uses_intent_state_and_message_metadata():
    message = NormalizedMessage(
        trace_id="trace_1",
        channel="api",
        user_id="user_1",
        session_id="sess_1",
        message="first time growing orchids, root rot, what should I do?",
        kb_id="kb_default",
        metadata={"segment": "beginner"},
    )
    state = UserState(
        user_id="user_1",
        session_id="sess_1",
        sales_stage="first_order_nurture",
        risk_level="normal",
        customer_tags=["newbie"],
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="orchid_care",
        secondary_intents=["root_rot"],
        sales_stage="care_support",
        customer_sentiment="anxious",
        confidence=0.91,
        need_rag=True,
        slots={"plant": "orchid"},
        reason="care question",
    )

    result = await build_tag_result(message=message, user_state=state, intent=intent)

    assert result.intent == "orchid_care"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "anxious"
    assert result.stage == "care_support"
    assert result.risk_level == "normal"
    assert result.secondary_intents == ["root_rot"]
    assert result.entities == {"plant": "orchid"}
    assert "customer_tag:newbie" in result.labels
