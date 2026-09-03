import pytest

from app.core.config import get_settings
from app.domains.sales.schemas.tag import TagResult
from app.services import business_tag_prompt_service, customer_level_service
from app.domains.sales.services.business_tag_prompt_service import (
    get_business_tag_prompt_block_ids,
    seed_business_tag_prompt_policy,
)
from app.domains.decisioning.services.policy_engine import decide_policy
from app.domains.decisioning.services.prompt_builder import build_prompt
from app.domains.conversations.schemas.context import ContextPackage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.prompt import PromptBuildInput


@pytest.fixture(autouse=True)
def isolated_tag_prompt_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'tag_prompt.db').as_posix()}")
    get_settings.cache_clear()
    customer_level_service.clear_cache()
    business_tag_prompt_service.clear_cache()
    yield
    business_tag_prompt_service.clear_cache()
    customer_level_service.clear_cache()
    get_settings.cache_clear()


def test_seed_business_tag_policy_splits_quantity_region_and_orchid_preference():
    seed_business_tag_prompt_policy()

    assert get_business_tag_prompt_block_ids(["customer_tag:1-9盆"]) == [
        "orchid_quantity.small.focus"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:100-199盆"]) == [
        "orchid_quantity.large.focus"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:浙江省"]) == [
        "region.east_china.variety"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:广东省"]) == [
        "region.south_china.variety"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:建兰"]) == [
        "orchid_preference.jianlan"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:浓香"]) == [
        "product_demand.preference"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:201-500元"]) == [
        "price_range.constraint"
    ]
    assert get_business_tag_prompt_block_ids(["customer_tag:阳台"]) == [
        "growing_environment.fit"
    ]


def test_advanced_customer_level_changes_guidance_without_forcing_handoff():
    result = customer_level_service.classify_customer_level(
        message="我主要研究艺草和叶艺",
        user_state=UserState(user_id="advanced-customer"),
    )

    assert result.level == "L5"
    assert result.route == "rag_answer"
    assert result.handoff_reason is None
    assert customer_level_service.get_customer_level_prompt_block_ids("L5") == [
        "customer_level.l5.identity",
        "customer_level.l5.communication",
        "customer_level.l5.recommendation",
    ]


@pytest.mark.asyncio
async def test_policy_adds_business_tag_prompt_blocks_after_customer_level_blocks():
    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        confidence=0.9,
        labels=[
            "customer_tag:L2 白银期",
            "customer_tag:100-199盆",
            "customer_tag:浙江省",
            "customer_tag:春兰",
        ],
    )

    decision = await decide_policy(tag)

    expected_order = [
        "customer_level.l2.identity",
        "customer_level.l2.communication",
        "customer_level.l2.recommendation",
        "orchid_quantity.large.focus",
        "region.east_china.variety",
        "orchid_preference.chunlan",
        "output.customer_reply",
    ]
    positions = [decision.prompt_block_ids.index(block_id) for block_id in expected_order]

    assert positions == sorted(positions)
    assert "orchid_preference.chunlan" in decision.prompt_block_ids


@pytest.mark.asyncio
async def test_prompt_builder_renders_database_business_tag_blocks():
    prompt = await build_prompt(
        PromptBuildInput(
            prompt_block_ids=[
                "base.customer_service",
                "orchid_quantity.large.focus",
                "region.south_china.variety",
                "orchid_preference.molan",
            ],
            context=ContextPackage(),
            user_message="广东养了很多盆墨兰，推荐一下。",
        )
    )

    assert "large orchid collection" in prompt
    assert "South China" in prompt
    assert "Molan" in prompt
