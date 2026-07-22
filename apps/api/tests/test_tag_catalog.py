from app.domains.sales.services.tag_catalog import (
    TAG_CATEGORIES,
    prompt_blocks_for_labels,
    prompt_blocks_for_tag_result,
)
from app.domains.sales.schemas.tag import TagResult


def test_current_business_tags_are_available_in_catalog():
    assert TAG_CATEGORIES["customer_level"].values[0].name == "L1 青铜期"
    assert "建兰" in [value.name for value in TAG_CATEGORIES["favorite_orchid_type"].values]
    assert [value.name for value in TAG_CATEGORIES["purchase_status"].values] == [
        "抖音已购",
        "微信已购",
    ]
    assert TAG_CATEGORIES["purchase_status"].ai_assignable is False
    assert TAG_CATEGORIES["purchase_status"].exclusive is False
    assert "product_demand" not in TAG_CATEGORIES
    assert "sop_group" not in TAG_CATEGORIES


def test_prompt_blocks_are_derived_from_tag_dimensions():
    blocks = prompt_blocks_for_labels(
        [
            "customer_tag:L3 黄金期",
            "customer_tag:100-200盆",
            "customer_tag:浙江省",
            "customer_tag:建兰",
            "customer_tag:红素（不包含其他的色花）",
        ]
    )

    assert blocks == [
        "customer_level.high_value",
        "orchid_quantity.large_collection",
        "geo.regional_care",
        "preference.orchid_variety",
    ]


def test_prompt_blocks_for_tag_result_keep_dimension_order_and_remove_duplicates():
    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        confidence=0.9,
        labels=["customer_tag:L1 青铜期", "customer_tag:建兰", "customer_tag:1-10盆"],
    )

    assert prompt_blocks_for_tag_result(tag) == [
        "customer_level.early_stage",
        "orchid_quantity.small_collection",
        "preference.orchid_variety",
    ]
