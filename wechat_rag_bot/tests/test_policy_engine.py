import pytest

from app.schemas.tag import TagResult
from app.services.policy_engine import decide_policy


@pytest.mark.asyncio
async def test_beginner_orchid_care_uses_basic_kb_and_beginner_prompt_blocks():
    tag = TagResult(
        intent="care_question",
        route="rag_answer",
        segment="beginner",
        emotion="anxious",
        stage="care_support",
        confidence=0.9,
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.action == "rag_answer"
    assert decision.knowledge_base_ids == ["kb_orchid_basic"]
    assert "segment.beginner" in decision.prompt_block_ids
    assert "tone.patient_step_by_step" in decision.prompt_block_ids
    assert decision.context_policy["recent_turns"] == 6
    assert decision.retrieval_policy["focus"] == ["basic_causes", "treatment_steps", "common_mistakes", "after_sales"]


@pytest.mark.asyncio
async def test_high_risk_policy_goes_to_human():
    tag = TagResult(
        intent="complaint",
        route="human",
        segment="advanced",
        risk_level="high",
        confidence=0.95,
    )

    decision = await decide_policy(tag)

    assert decision.route == "human"
    assert decision.action == "human"
    assert decision.template_ids == ["handoff_risk_high"]
    assert decision.next_action == "human_handoff"


@pytest.mark.asyncio
async def test_advanced_customer_level_uses_rag_not_default_handoff():
    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="advanced",
        emotion="neutral",
        stage="pain_confirmed",
        risk_level="normal",
        confidence=0.9,
        labels=["customer_tag:L5 master"],
        reason="advanced customer asks care question",
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.action == "rag_answer"
    assert decision.knowledge_base_ids == [
        "kb_orchid_advanced",
        "kb_best_practices",
    ]
    assert decision.next_action is None
    assert decision.reason != "advanced_customer_level_to_human"


@pytest.mark.asyncio
async def test_care_pain_words_do_not_handoff_without_human_route_or_high_risk():
    tag = TagResult(
        intent="care_question",
        route="rag_answer",
        segment="unknown",
        emotion="anxious",
        stage="after_sale",
        risk_level="normal",
        confidence=0.85,
        labels=["pain_point:root_rot", "pain_point:yellow_leaf"],
        reason="serious care issue without refund or complaint",
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.knowledge_base_ids == [
        "kb_orchid_basic",
        "kb_best_practices",
    ]
    assert decision.next_action is None
