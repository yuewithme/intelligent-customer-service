import pytest

from app.core.config import get_settings
from app.domains.sales.services import business_tag_prompt_service
from app.domains.customers.services import customer_level_service
from app.domains.sales.services.business_tag_prompt_service import (
    get_business_tag_prompt_block_ids,
    seed_business_tag_prompt_policy,
)
from app.domains.decisioning.services.prompt_builder import build_prompt
from app.domains.conversations.schemas.context import ContextPackage
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
