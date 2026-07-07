import pytest

from app.schemas.context import ContextSelectionInput
from app.services.context_selector import select_context


@pytest.mark.asyncio
async def test_select_context_keeps_recent_turns_and_profile_summary():
    request = ContextSelectionInput(
        profile={
            "user_id": "user_1",
            "ai_summary": "The user is a beginner and asks about root rot often.",
            "preference_summary": "Prefers step-by-step explanations.",
            "pain_points": ["worried about failing to keep orchids alive"],
        },
        state={
            "sales_stage": "care_support",
            "risk_level": "normal",
        },
        memories=[
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second turn"},
            {"role": "assistant", "content": "second reply"},
        ],
        context_policy={
            "recent_turns": 2,
            "include_profile_summary": True,
            "include_long_memory_summary": True,
        },
    )

    result = await select_context(request)

    assert result.profile_summary["ai_summary"] == "The user is a beginner and asks about root rot often."
    assert result.session_state["sales_stage"] == "care_support"
    assert [turn["content"] for turn in result.recent_turns] == ["second turn", "second reply"]
    assert "worried about failing to keep orchids alive" in result.long_memory_summary
