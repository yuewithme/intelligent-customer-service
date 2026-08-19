from dataclasses import dataclass

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    CustomerLevelPromptBindingModel,
    PromptBlockModel,
    TagCatalogMetaModel,
    TagCategoryModel,
    TagDefinitionModel,
    TagPromptBindingModel,
)
from app.domains.sales.schemas.tag import TagResult


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
                "海南省",
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
    "service_status": TagCategory(
        id="service_status",
        name="服务标签",
        prompt_rule="由运营人工标记正在接受固定内容服务的客户，AI 不得自行分配。",
        values=(TagValue("服务中"),),
        ai_assignable=False,
        exclusive=True,
    ),
}


SYSTEM_TAG_CATEGORIES: dict[str, TagCategory] = {
    "customer_segment": TagCategory(
        id="customer_segment",
        name="客户分群",
        prompt_rule="用于区分新手、进阶客户及尚未识别的客户，只允许目录内分群。",
        values=tuple(
            TagValue(f"segment:{value}")
            for value in ("unknown", "beginner", "advanced")
        ),
    ),
    "customer_sentiment": TagCategory(
        id="customer_sentiment",
        name="客户情绪",
        prompt_rule="AI 只能选择目录内情绪；目录外输出按 neutral 处理。",
        values=tuple(
            TagValue(f"emotion:{value}")
            for value in ("neutral", "anxious", "angry")
        ),
    ),
    "risk_level": TagCategory(
        id="risk_level",
        name="风险等级",
        prompt_rule="用于升级人工和风险控制，仅允许目录内等级。",
        values=tuple(
            TagValue(f"risk:{value}")
            for value in ("normal", "medium", "high", "elevated")
        ),
    ),
    "pain_point": TagCategory(
        id="pain_point",
        name="客户痛点",
        prompt_rule="仅记录已配置、可被销售策略使用的固定痛点标签。",
        values=(TagValue("pain_point:兰花烂根"),),
        exclusive=False,
    ),
    "product_interest": TagCategory(
        id="product_interest",
        name="产品兴趣",
        prompt_rule="仅记录已配置、可被销售策略使用的固定兴趣标签。",
        values=(TagValue("product_interest:兰花养护"),),
        exclusive=False,
    ),
}

TAG_CATEGORIES.update(SYSTEM_TAG_CATEGORIES)

SYSTEM_CATEGORY_IDS = frozenset(SYSTEM_TAG_CATEGORIES)
FROZEN_CATEGORY_IDS = frozenset()
SYSTEM_TAG_PREFIXES = {
    "customer_segment": "segment:",
    "customer_sentiment": "emotion:",
    "risk_level": "risk:",
    "pain_point": "pain_point:",
    "product_interest": "product_interest:",
}
PURCHASE_TAG_VALUES = frozenset({"抖音已购", "微信已购"})
_CATALOG_VERSION = "6"


_sessionmakers: dict[str, sessionmaker] = {}
_category_cache: dict[str, dict[str, TagCategory]] = {}
_tables = [
    TagCategoryModel.__table__,
    TagDefinitionModel.__table__,
    TagCatalogMetaModel.__table__,
    PromptBlockModel.__table__,
    CustomerLevelPromptBindingModel.__table__,
    TagPromptBindingModel.__table__,
]


def get_tag_categories() -> dict[str, TagCategory]:
    """Return the live catalog used by profile validation and the admin page."""
    url = get_settings().database_url
    cached = _category_cache.get(url)
    if cached is not None:
        return cached
    _ensure_seeded()
    with _get_session() as session:
        category_rows = session.scalars(
            select(TagCategoryModel).order_by(
                TagCategoryModel.position.asc(), TagCategoryModel.id.asc()
            )
        ).all()
        value_rows = session.scalars(
            select(TagDefinitionModel).order_by(
                TagDefinitionModel.position.asc(), TagDefinitionModel.id.asc()
            )
        ).all()

    values_by_category: dict[str, list[TagValue]] = {}
    for row in value_rows:
        values_by_category.setdefault(row.category_id, []).append(TagValue(row.value))
    categories = {
        row.id: TagCategory(
            id=row.id,
            name=row.name,
            prompt_rule=row.prompt_rule,
            values=tuple(values_by_category.get(row.id, [])),
            ai_assignable=row.ai_assignable,
            exclusive=row.exclusive,
        )
        for row in category_rows
    }
    _category_cache[url] = categories
    return categories


def clear_cache() -> None:
    _sessionmakers.clear()
    _category_cache.clear()


def invalidate_cache() -> None:
    _category_cache.pop(get_settings().database_url, None)


def _ensure_seeded() -> None:
    with _get_session() as session:
        count = session.scalar(select(func.count()).select_from(TagCategoryModel)) or 0
        marker = session.get(TagCatalogMetaModel, "seed_version")
        if count and marker and marker.value == _CATALOG_VERSION:
            return
        categories = (
            TAG_CATEGORIES
            if not count
            else {
                **SYSTEM_TAG_CATEGORIES,
                "service_status": TAG_CATEGORIES["service_status"],
            }
        )
        max_position = session.scalar(select(func.max(TagCategoryModel.position))) or 0
        for category_position, category in enumerate(categories.values(), start=1):
            if session.get(TagCategoryModel, category.id):
                continue
            session.add(
                TagCategoryModel(
                    id=category.id,
                    name=category.name,
                    prompt_rule=category.prompt_rule,
                    ai_assignable=category.ai_assignable,
                    exclusive=category.exclusive,
                    position=max_position + category_position if count else category_position,
                )
            )
            for value_position, value in enumerate(category.values, start=1):
                session.add(
                    TagDefinitionModel(
                        category_id=category.id,
                        value=value.name,
                        position=value_position,
                    )
                )
        if count and (marker is None or marker.value != _CATALOG_VERSION):
            _seed_missing_value(session, "province", "海南省")
            _remove_retired_categories(session, {"intent", "sales_stage"})
        if marker is None:
            session.add(TagCatalogMetaModel(key="seed_version", value=_CATALOG_VERSION))
        else:
            marker.value = _CATALOG_VERSION
        session.commit()


def _remove_retired_categories(session: Session, category_ids: set[str]) -> None:
    block_ids = set(
        session.scalars(
            select(TagPromptBindingModel.prompt_block_id).where(
                TagPromptBindingModel.category_id.in_(category_ids)
            )
        ).all()
    )
    session.execute(
        delete(TagPromptBindingModel).where(
            TagPromptBindingModel.category_id.in_(category_ids)
        )
    )
    session.execute(
        delete(TagDefinitionModel).where(
            TagDefinitionModel.category_id.in_(category_ids)
        )
    )
    session.execute(
        delete(TagCategoryModel).where(TagCategoryModel.id.in_(category_ids))
    )
    _delete_orphan_prompt_blocks(session, block_ids)


def _sync_frozen_category(session: Session, category: TagCategory) -> None:
    category_row = session.get(TagCategoryModel, category.id)
    if category_row is None:
        return
    category_row.name = category.name
    category_row.prompt_rule = category.prompt_rule
    category_row.ai_assignable = category.ai_assignable
    category_row.exclusive = category.exclusive

    expected_values = [value.name for value in category.values]
    existing_rows = session.scalars(
        select(TagDefinitionModel).where(TagDefinitionModel.category_id == category.id)
    ).all()
    existing_by_value = {row.value: row for row in existing_rows}
    obsolete_values = set(existing_by_value) - set(expected_values)
    if obsolete_values:
        obsolete_block_ids = set(
            session.scalars(
                select(TagPromptBindingModel.prompt_block_id).where(
                    TagPromptBindingModel.category_id == category.id,
                    TagPromptBindingModel.tag_value.in_(obsolete_values),
                )
            ).all()
        )
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == category.id,
                TagPromptBindingModel.tag_value.in_(obsolete_values),
            )
        )
        session.execute(
            delete(TagDefinitionModel).where(
                TagDefinitionModel.category_id == category.id,
                TagDefinitionModel.value.in_(obsolete_values),
            )
        )
        _delete_orphan_prompt_blocks(session, obsolete_block_ids)

    for position, value in enumerate(expected_values, start=1):
        row = existing_by_value.get(value)
        if row is None:
            session.add(
                TagDefinitionModel(
                    category_id=category.id,
                    value=value,
                    position=position,
                )
            )
        else:
            row.position = position


def _delete_orphan_prompt_blocks(session: Session, block_ids: set[str]) -> None:
    for block_id in block_ids:
        tag_binding = session.scalar(
            select(TagPromptBindingModel.id)
            .where(TagPromptBindingModel.prompt_block_id == block_id)
            .limit(1)
        )
        level_binding = session.scalar(
            select(CustomerLevelPromptBindingModel.id)
            .where(CustomerLevelPromptBindingModel.prompt_block_id == block_id)
            .limit(1)
        )
        if tag_binding is None and level_binding is None:
            session.execute(
                delete(PromptBlockModel).where(PromptBlockModel.block_id == block_id)
            )


def _seed_missing_value(session: Session, category_id: str, value: str) -> None:
    if session.get(TagCategoryModel, category_id) is None:
        return
    existing = session.scalar(
        select(TagDefinitionModel.id).where(TagDefinitionModel.value == value).limit(1)
    )
    if existing is not None:
        return
    max_position = session.scalar(
        select(func.max(TagDefinitionModel.position)).where(
            TagDefinitionModel.category_id == category_id
        )
    ) or 0
    session.add(
        TagDefinitionModel(
            category_id=category_id,
            value=value,
            position=max_position + 1,
        )
    )


def get_profile_tag_categories() -> dict[str, TagCategory]:
    """Return customer-profile dimensions, excluding system strategy tags."""
    return {
        category_id: category
        for category_id, category in get_tag_categories().items()
        if category_id not in SYSTEM_CATEGORY_IDS
    }


def is_profile_tag_category_enabled(category_id: str) -> bool:
    return category_id != "purchase_status" or get_settings().purchase_tags_enabled


def is_profile_tag_enabled(value: str) -> bool:
    return value not in PURCHASE_TAG_VALUES or get_settings().purchase_tags_enabled


def system_tag_token(category_id: str, value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    prefix = SYSTEM_TAG_PREFIXES.get(category_id, "")
    if not value or not prefix:
        return ""
    return value if value.startswith(prefix) else f"{prefix}{value}"


def system_tag_values(category_id: str) -> list[str]:
    """Return raw values for one system dimension in live catalog order."""
    category = get_tag_categories().get(category_id)
    prefix = SYSTEM_TAG_PREFIXES.get(category_id, "")
    if category is None or not prefix:
        return []
    return [
        value.name[len(prefix):]
        for value in category.values
        if value.name.startswith(prefix) and value.name[len(prefix):]
    ]


def normalize_system_value(
    category_id: str,
    value: str | None,
    *,
    fallback: str,
) -> str:
    allowed = set(system_tag_values(category_id))
    return value.strip() if isinstance(value, str) and value.strip() in allowed else fallback


def is_allowed_system_tag(label: str, category_id: str | None = None) -> bool:
    if not isinstance(label, str) or not label.strip():
        return False
    label = label.strip()
    category_ids = (category_id,) if category_id else tuple(SYSTEM_CATEGORY_IDS)
    for current_id in category_ids:
        category = get_tag_categories().get(current_id)
        if category and any(value.name == label for value in category.values):
            return True
    return False


def is_allowed_profile_tag(value: str) -> bool:
    return any(
        tag.name == value
        for category in get_profile_tag_categories().values()
        for tag in category.values
    )


def filter_profile_tags(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.split(":", 1)[1] if value.startswith("customer_tag:") else value
        normalized = normalized.strip()
        if (
            is_allowed_profile_tag(normalized)
            and is_profile_tag_enabled(normalized)
            and normalized not in result
        ):
            result.append(normalized)
    return result


def filter_runtime_labels(labels: list[str]) -> list[str]:
    """Keep only configured customer or system labels; never preserve free-form tags."""
    result: list[str] = []
    for label in labels:
        if not isinstance(label, str):
            continue
        label = label.strip()
        if label.startswith("customer_tag:"):
            value = label.split(":", 1)[1].strip()
            normalized = (
                f"customer_tag:{value}"
                if is_allowed_profile_tag(value) and is_profile_tag_enabled(value)
                else ""
            )
        else:
            normalized = label if is_allowed_system_tag(label) else ""
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


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
