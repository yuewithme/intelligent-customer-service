from dataclasses import dataclass

from app.schemas.tag import TagResult


@dataclass(frozen=True)
class TagValue:
    name: str
    prompt_block_id: str | None = None


@dataclass(frozen=True)
class TagCategory:
    id: str
    name: str
    prompt_rule: str
    values: tuple[TagValue, ...]
    ai_assignable: bool = True
    exclusive: bool = True


TAG_CATEGORIES: dict[str, TagCategory] = {
    "customer_level": TagCategory(
        id="customer_level",
        name="客户等级",
        prompt_rule="Use value tier to decide how much trust-building, exclusivity, and follow-up detail to include.",
        values=(
            TagValue("L1 青铜期", "customer_level.early_stage"),
            TagValue("L2 白银期", "customer_level.early_stage"),
            TagValue("L3 黄金期", "customer_level.high_value"),
            TagValue("L4 铂金期", "customer_level.high_value"),
            TagValue("L5 宗师期", "customer_level.high_value"),
            TagValue("L6 王者期", "customer_level.high_value"),
        ),
    ),
    "orchid_quantity": TagCategory(
        id="orchid_quantity",
        name="养兰数量",
        prompt_rule="Use collection size to decide whether to explain basics or optimize care/selection efficiency.",
        values=(
            TagValue("1-10盆", "orchid_quantity.small_collection"),
            TagValue("10-30盆", "orchid_quantity.small_collection"),
            TagValue("30-50盆", "orchid_quantity.medium_collection"),
            TagValue("50-100盆", "orchid_quantity.medium_collection"),
            TagValue("100-200盆", "orchid_quantity.large_collection"),
            TagValue("200+盆", "orchid_quantity.large_collection"),
            TagValue("1000+盆", "orchid_quantity.large_collection"),
        ),
    ),
    "province": TagCategory(
        id="province",
        name="所在省份",
        prompt_rule="Use region only as climate/logistics context when it helps care advice or delivery expectations.",
        values=tuple(
            TagValue(name, "geo.regional_care")
            for name in [
                "浙江省",
                "北京市",
                "天津市",
                "上海市",
                "重庆市",
                "河北省",
                "山西省",
                "辽宁省",
                "吉林省",
                "黑龙江省",
                "江苏省",
                "安徽省",
                "福建省",
                "江西省",
                "山东省",
                "河南省",
                "湖北省",
                "湖南省",
                "广东省",
                "四川省",
                "贵州省",
                "云南省",
                "陕西省",
                "甘肃省",
                "青海省",
                "内蒙古",
                "宁夏",
                "新疆",
                "西藏自治区",
                "广西省",
            ]
        ),
    ),
    "favorite_orchid_type": TagCategory(
        id="favorite_orchid_type",
        name="用户喜欢的兰花品类",
        prompt_rule="Use preferred orchid type to keep recommendations and examples aligned with the user's taste.",
        values=tuple(
            TagValue(name, "preference.orchid_variety")
            for name in ["春兰", "建兰", "墨兰", "寒兰", "蕙兰", "莲瓣兰", "春剑", "大花蕙兰等花大色漂亮的"]
        ),
    ),
    "purchase_status": TagCategory(
        id="purchase_status",
        name="购买状态",
        prompt_rule="Purchase status is assigned only from verified commerce data, never inferred from chat content.",
        values=(
            TagValue("抖音已购"),
            TagValue("微信已购"),
        ),
        ai_assignable=False,
        exclusive=False,
    ),
}


def prompt_blocks_for_tag_result(tag: TagResult) -> list[str]:
    return prompt_blocks_for_labels(tag.labels)


def prompt_blocks_for_labels(labels: list[str]) -> list[str]:
    blocks: list[str] = []
    for category in TAG_CATEGORIES.values():
        for value in category.values:
            if value.prompt_block_id and any(_label_value(label) == value.name for label in labels):
                if value.prompt_block_id not in blocks:
                    blocks.append(value.prompt_block_id)
                break
    return blocks


def _label_value(label: str) -> str:
    return label.split(":", 1)[1] if ":" in label else label
