import pytest

from app.schemas.context import ContextSelectionInput
from app.services.context_selector import select_context


@pytest.mark.asyncio
async def test_select_context_keeps_recent_turns_and_profile_summary():
    request = ContextSelectionInput(
        profile={
            "ai_summary": "The user is a beginner.",
            "pain_points": ["worried about keeping orchids alive"],
        },
        state={"sales_stage": "care_support", "risk_level": "normal"},
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

    assert result.profile_summary["ai_summary"] == "The user is a beginner."
    assert result.session_state["sales_stage"] == "care_support"
    assert [turn["content"] for turn in result.recent_turns] == [
        "second turn",
        "second reply",
    ]
    assert "worried about keeping orchids alive" in result.long_memory_summary


@pytest.mark.asyncio
async def test_select_context_carries_persisted_basic_info_and_sales_profile():
    request = ContextSelectionInput(
        profile={
            "basic_info": {
                "nickname": "张" * 200,
                "remark_name": "广西张姐",
                "owner_wc_id": "wxid_bot",
            },
            "customer_tags": ["100-200盆", "建兰"],
            "product_interests": ["建兰"],
            "pain_points": ["夏季容易烂根"],
            "active_opportunity": {"stage": "solution_recommended"},
        },
        state={"sales_stage": "after_sale"},
        memories=[],
    )

    result = await select_context(request)

    assert result.profile_summary == {
        "basic_info": {"nickname": "张" * 120, "remark_name": "广西张姐"},
        "customer_tags": ["100-200盆", "建兰"],
        "product_interests": ["建兰"],
        "pain_points": ["夏季容易烂根"],
        "active_opportunity": {"stage": "solution_recommended"},
    }
