import pytest

from app.domains.conversations.schemas.context import ContextSelectionInput
from app.domains.knowledge.services.context_selector import select_context


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


@pytest.mark.asyncio
async def test_care_context_can_exclude_all_sales_and_product_state():
    request = ContextSelectionInput(
        profile={
            "product_interests": ["建兰忆香荷"],
            "active_opportunity": {"selected_product_id": "orchid-1"},
        },
        state={
            "sales_stage": "closing",
            "metadata": {"sales_action": {"sales_action": "close_order"}},
        },
        memories=[{"role": "assistant", "content": "推荐建兰忆香荷"}],
        memory_context={
            "current_facts": [{"subject": "建兰忆香荷"}],
            "verified_business_facts": [{"price": 68}],
            "relevant_episodes": [{"summary": "刚推荐过商品"}],
        },
        context_policy={
            "recent_turns": 0,
            "include_profile_summary": False,
            "include_long_memory_summary": False,
            "include_session_state": False,
            "include_memory_context": False,
        },
    )

    result = await select_context(request)

    assert result.profile_summary == {}
    assert result.session_state == {}
    assert result.recent_turns == []
    assert result.memory_facts == []
    assert result.verified_business_facts == []
    assert result.relevant_episodes == []
