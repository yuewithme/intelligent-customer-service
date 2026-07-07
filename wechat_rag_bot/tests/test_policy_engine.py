import pytest

from app.schemas.tag import TagResult
from app.services.policy_engine import decide_policy


@pytest.mark.asyncio
async def test_beginner_orchid_care_uses_basic_kb_and_beginner_prompt_blocks():
    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        emotion="anxious",
        stage="care_support",
        confidence=0.9,
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.action == "rag_answer"
    assert decision.knowledge_base_ids == ["kb_orchid_basic", "kb_care_faq"]
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
