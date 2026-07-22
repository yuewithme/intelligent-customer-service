import pytest

from app.domains.conversations.schemas.context import ContextPackage
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.knowledge.services.rag_service import build_rag_prompt


@pytest.mark.asyncio
async def test_build_rag_prompt_uses_policy_prompt_blocks_and_context():
    policy = PolicyDecision(
        route="rag_answer",
        action="rag_answer",
        prompt_block_ids=["base.customer_service", "segment.beginner"],
        template_ids=["opening_beginner_care"],
    )
    context = ContextPackage(
        profile_summary={"ai_summary": "The user is a beginner."},
        recent_turns=[{"role": "user", "content": "My orchid has root rot."}],
    )
    docs = [
        {
            "file_name": "care.md",
            "text": "Root rot treatment starts with water control and better ventilation.",
        }
    ]

    prompt = await build_rag_prompt(
        question="What should I do?",
        docs=docs,
        policy=policy,
        context=context,
        templates=["I will help you check this step by step."],
    )

    assert "The user is a beginner." in prompt
    assert "I will help you check this step by step." in prompt
    assert "Root rot treatment starts with water control" in prompt
    assert prompt.strip().endswith("What should I do?")
