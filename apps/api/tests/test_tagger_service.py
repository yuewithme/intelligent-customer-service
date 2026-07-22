import pytest

from app.domains.sales.schemas.tag import TagResult
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.sales.services.tagger_service import build_tag_result


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

    assert result.intent == "unknown"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "anxious"
    assert result.stage == "unknown"
    assert result.risk_level == "normal"
    assert result.secondary_intents == []
    assert result.entities == {"plant": "orchid"}
    assert "customer_tag:newbie" not in result.labels


@pytest.mark.asyncio
async def test_build_tag_result_rejects_free_form_memory_tags_but_keeps_configured_strategy_tags():
    message = NormalizedMessage(
        trace_id="trace_2",
        channel="wechat",
        user_id="user_2",
        session_id="default",
        message="我的预算在200这样，在杭州这边，兰花烂根了咋办",
        kb_id="kb_default",
        metadata={},
    )
    state = UserState(user_id="user_2", session_id="default", customer_tags=[])
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        secondary_intents=["root_rot"],
        sales_stage="pain_confirmed",
        confidence=0.9,
        need_rag=True,
        slots={"city": "杭州", "budget": "200"},
        reason="care question",
    )

    result = await build_tag_result(message=message, user_state=state, intent=intent)

    assert "region:杭州" not in result.labels
    assert "budget:200" not in result.labels
    assert "pain_point:兰花烂根" in result.labels
    assert "product_interest:兰花养护" in result.labels


@pytest.mark.asyncio
async def test_build_tag_result_does_not_carry_high_risk_into_normal_care_question():
    message = NormalizedMessage(
        trace_id="trace_risk",
        channel="api",
        user_id="user_risk",
        session_id="sess_risk",
        message="root rot and yellow leaves, what should I do?",
        kb_id="kb_default",
        metadata={},
    )
    state = UserState(
        user_id="user_risk",
        session_id="sess_risk",
        risk_level="high",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="orchid_care",
        confidence=0.9,
        need_rag=True,
        reason="normal care question",
    )

    result = await build_tag_result(message=message, user_state=state, intent=intent)

    assert result.risk_level == "normal"


@pytest.mark.asyncio
async def test_build_tag_result_does_not_generate_region_or_plant_count_tags():
    message = NormalizedMessage(
        trace_id="trace_3",
        channel="wechat",
        user_id="user_3",
        session_id="default",
        message="我在广西，养了100盆花，你有什么推荐的花吗",
        kb_id="kb_default",
        metadata={},
    )
    state = UserState(user_id="user_3", session_id="default", customer_tags=[])
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        sales_stage="pain_confirmed",
        confidence=0.9,
        need_rag=True,
        slots={},
        reason="care question",
    )

    result = await build_tag_result(message=message, user_state=state, intent=intent)

    assert "region:广西" not in result.labels
    assert "plant_count:100盆" not in result.labels
