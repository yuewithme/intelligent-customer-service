import pytest

from app.schemas.context import ContextPackage
from app.schemas.prompt import PromptBuildInput
from app.services.prompt_builder import build_prompt


@pytest.mark.asyncio
async def test_build_prompt_orders_blocks_context_knowledge_and_question():
    request = PromptBuildInput(
        prompt_block_ids=["base.customer_service", "segment.beginner", "tone.patient_step_by_step"],
        templates=["I will help you check this step by step."],
        context=ContextPackage(
            profile_summary={"ai_summary": "The user is a beginner."},
            session_state={"sales_stage": "care_support"},
            recent_turns=[{"role": "user", "content": "My orchid has root rot."}],
            long_memory_summary="The user worries about keeping orchids alive.",
        ),
        knowledge_snippets=[
            {
                "source": "kb_basic",
                "text": "Common root rot causes include overwatering and poor ventilation.",
            }
        ],
        user_message="What should I do now?",
    )

    prompt = await build_prompt(request)

    assert prompt.index("You are an intelligent customer service assistant.") < prompt.index("The user is a beginner.")
    assert "I will help you check this step by step." in prompt
    assert "The user worries about keeping orchids alive." in prompt
    assert "Common root rot causes include overwatering and poor ventilation." in prompt
    assert prompt.strip().endswith("What should I do now?")


@pytest.mark.asyncio
async def test_base_prompt_tells_model_not_to_reask_known_profile_facts():
    prompt = await build_prompt(
        PromptBuildInput(
            prompt_block_ids=["base.customer_service"],
            context={
                "profile_summary": {
                    "customer_tags": ["浙江省", "100-200盆", "建兰"],
                    "ai_summary": "客户在浙江，养了100盆花。",
                }
            },
            user_message="你们家建兰有什么推荐",
        )
    )

    assert "Do not ask again for profile facts already provided" in prompt
