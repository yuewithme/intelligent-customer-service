from app.domains.sales.services.tag_catalog import (
    TAG_CATEGORIES,
    prompt_blocks_for_labels,
)


def test_current_business_tags_are_available_in_catalog():
    assert TAG_CATEGORIES["customer_level"].values[0].name == "L1 青铜期"
    assert "建兰" in [value.name for value in TAG_CATEGORIES["favorite_orchid_type"].values]
    assert [value.name for value in TAG_CATEGORIES["purchase_status"].values] == [
        "抖音已购",
        "微信已购",
    ]
    assert TAG_CATEGORIES["purchase_status"].ai_assignable is False
    assert TAG_CATEGORIES["purchase_status"].exclusive is False
    assert TAG_CATEGORIES["favorite_orchid_type"].exclusive is False
    assert TAG_CATEGORIES["product_demand"].exclusive is False
    assert TAG_CATEGORIES["price_range"].exclusive is True
    assert TAG_CATEGORIES["growing_environment"].exclusive is True
    assert "sop_group" not in TAG_CATEGORIES


def test_prompt_blocks_are_derived_from_tag_dimensions():
    blocks = prompt_blocks_for_labels(
        [
            "customer_tag:L3 黄金期",
            "customer_tag:100-199盆",
            "customer_tag:浙江省",
            "customer_tag:建兰",
            "customer_tag:红素",
        ]
    )

    assert blocks == [
        "customer_level.high_value",
        "orchid_quantity.large_collection",
        "geo.regional_care",
        "preference.orchid_variety",
    ]
