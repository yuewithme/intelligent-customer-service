import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine, delete, func, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    CustomerLevelPromptBindingModel,
    MemoryFactModel,
    PromptBlockModel,
    TagCatalogMetaModel,
    TagCategoryModel,
    TagDefinitionModel,
    TagPromptBindingModel,
    UserProfileModel,
)


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
            TagValue("准备养兰", "orchid_quantity.small_collection"),
            TagValue("1-9盆", "orchid_quantity.small_collection"),
            TagValue("10-29盆", "orchid_quantity.small_collection"),
            TagValue("30-49盆", "orchid_quantity.medium_collection"),
            TagValue("50-99盆", "orchid_quantity.medium_collection"),
            TagValue("100-199盆", "orchid_quantity.large_collection"),
            TagValue("200-499盆", "orchid_quantity.large_collection"),
            TagValue("500-999盆", "orchid_quantity.large_collection"),
            TagValue("1000盆以上", "orchid_quantity.large_collection"),
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
                "广西壮族自治区",
                "香港",
                "澳门",
                "台湾",
                "海外",
            ]
        ),
    ),
    "favorite_orchid_type": TagCategory(
        id="favorite_orchid_type",
        name="用户喜欢的兰花品类",
        prompt_rule="Use preferred orchid type to keep recommendations and examples aligned with the user's taste.",
        values=tuple(
            TagValue(name, "preference.orchid_variety")
            for name in [
                "春兰",
                "建兰",
                "墨兰",
                "寒兰",
                "蕙兰",
                "莲瓣兰",
                "春剑",
                "豆瓣兰",
                "大花蕙兰等花大色漂亮的",
                "小众品类（送春、秋芝等）",
                "品类不限",
            ]
        ),
        exclusive=False,
    ),
    "product_demand": TagCategory(
        id="product_demand",
        name="产品需求分类",
        prompt_rule="Use explicit product preferences to rank matching products without treating them as purchase intent.",
        values=tuple(
            TagValue(name)
            for name in [
                "色花（红、黄、复色等，不含红素）",
                "素花（绿白黄素心，不含红素）",
                "红素",
                "奇花（多瓣、蝶瓣、三星蝶）",
                "艺草（虎斑、蛇斑、线艺、缟艺）",
                "梅瓣",
                "荷瓣",
                "水仙瓣及其他瓣型",
                "浓香",
                "清香",
                "矮种",
                "半垂叶",
                "直立叶",
                "花大色艳",
                "好养易活",
                "勤花易开",
                "需求不限",
            ]
        ),
        exclusive=False,
    ),
    "price_range": TagCategory(
        id="price_range",
        name="价格接受范围",
        prompt_rule="Use the latest explicit budget as a recommendation constraint; do not pressure the customer above it.",
        values=tuple(
            TagValue(name)
            for name in [
                "50元以内",
                "51-100元",
                "101-200元",
                "201-500元",
                "501-999元",
                "1000元以上",
                "价格不限",
            ]
        ),
    ),
    "growing_environment": TagCategory(
        id="growing_environment",
        name="养兰环境",
        prompt_rule="Use the customer's actual growing environment for product fit and care advice; never infer it from province alone.",
        values=tuple(
            TagValue(name)
            for name in ["阳台", "室内", "庭院/露台", "室外露养", "有兰棚"]
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
_CATALOG_VERSION = "7"
_DELETION_TRACKING_MARKER = "tag.deletion_tracking.v1"
_MANAGED_MEMORY_FACT_CATEGORIES = {
    "purchase.product_interest": "product_interest",
    "service.pain_point": "pain_point",
}

_V7_TAG_RENAMES = {
    ("orchid_quantity", "1-10盆"): "1-9盆",
    ("orchid_quantity", "10-30盆"): "10-29盆",
    ("orchid_quantity", "30-50盆"): "30-49盆",
    ("orchid_quantity", "50-100盆"): "50-99盆",
    ("orchid_quantity", "100-200盆"): "100-199盆",
    ("orchid_quantity", "200+盆"): "200-499盆",
    ("orchid_quantity", "500+盆"): "500-999盆",
    ("orchid_quantity", "800+盆"): "500-999盆",
    ("orchid_quantity", "1000+盆"): "1000盆以上",
    ("orchid_quantity", "2000+盆"): "1000盆以上",
    ("province", "广西省"): "广西壮族自治区",
    ("favorite_orchid_type", "小众品类（送春秋芝等）"): "小众品类（送春、秋芝等）",
    ("product_demand", "色花（红素、红、黄、复色花等）"): "色花（红、黄、复色等，不含红素）",
    ("product_demand", "素花（绿白黄素心、素雅绿色）"): "素花（绿白黄素心，不含红素）",
    ("product_demand", "红素（不包含其他的色花）"): "红素",
    ("product_demand", "水仙瓣及其他"): "水仙瓣及其他瓣型",
    ("growing_environment", "阳台党"): "阳台",
    ("growing_environment", "室外"): "室外露养",
}

_V7_TAG_MOVES = {
    ("product_demand", "接受50元以内"): ("price_range", "50元以内"),
    ("product_demand", "50元以内"): ("price_range", "50元以内"),
    ("product_demand", "接受50-100以内"): ("price_range", "51-100元"),
    ("product_demand", "50-100以内"): ("price_range", "51-100元"),
    ("product_demand", "100-200以内"): ("price_range", "101-200元"),
    ("product_demand", "200-500以内"): ("price_range", "201-500元"),
    ("product_demand", "500以上"): ("price_range", "501-999元"),
    ("product_demand", "1000+"): ("price_range", "1000元以上"),
}


_sessionmakers: dict[str, sessionmaker] = {}
_category_cache: dict[str, dict[str, TagCategory]] = {}
_tables = [
    TagCategoryModel.__table__,
    TagDefinitionModel.__table__,
    TagCatalogMetaModel.__table__,
    PromptBlockModel.__table__,
    CustomerLevelPromptBindingModel.__table__,
    TagPromptBindingModel.__table__,
    UserProfileModel.__table__,
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


def is_tag_category_enabled(category_id: str) -> bool:
    return category_id in get_tag_categories()


def is_memory_fact_enabled(fact_key: str) -> bool:
    category_id = _MANAGED_MEMORY_FACT_CATEGORIES.get(fact_key)
    return category_id is None or is_tag_category_enabled(category_id)


def mark_category_deleted(
    session: Session,
    category_id: str,
    values: list[str] | tuple[str, ...] = (),
) -> None:
    _set_meta_marker(session, _category_deletion_key(category_id))
    default_category = TAG_CATEGORIES.get(category_id)
    known_values = set(values)
    if default_category is not None:
        known_values.update(value.name for value in default_category.values)
    for value in known_values:
        mark_tag_deleted(session, category_id, value)


def clear_category_deletion(session: Session, category_id: str) -> None:
    _remove_meta_marker(session, _category_deletion_key(category_id))


def mark_tag_deleted(session: Session, category_id: str, value: str) -> None:
    _set_meta_marker(session, _tag_deletion_key(category_id, value))


def clear_tag_deletion(session: Session, category_id: str, value: str) -> None:
    _remove_meta_marker(session, _tag_deletion_key(category_id, value))


def retire_deleted_category_data(
    session: Session, category_ids: set[str]
) -> None:
    if not category_ids:
        return
    now = datetime.now(timezone.utc)
    removed_values = {
        value.name
        for category_id in category_ids
        for value in TAG_CATEGORIES.get(
            category_id,
            TagCategory(category_id, category_id, "", ()),
        ).values
    }
    for profile in session.scalars(select(UserProfileModel)).all():
        changed = False
        if removed_values:
            try:
                tags = json.loads(profile.customer_tags_json or "[]")
            except (TypeError, ValueError):
                tags = []
            if isinstance(tags, list):
                updated_tags = [value for value in tags if value not in removed_values]
                if updated_tags != tags:
                    profile.customer_tags_json = json.dumps(
                        updated_tags, ensure_ascii=False
                    )
                    changed = True
        if "risk_level" in category_ids and profile.risk_level != "normal":
            profile.risk_level = "normal"
            changed = True
        if (
            "product_interest" in category_ids
            and profile.product_interests_json != "[]"
        ):
            profile.product_interests_json = "[]"
            changed = True
        if "pain_point" in category_ids and profile.pain_points_json != "[]":
            profile.pain_points_json = "[]"
            changed = True
        if changed:
            profile.updated_at = now

    fact_keys = {
        fact_key
        for fact_key, category_id in _MANAGED_MEMORY_FACT_CATEGORIES.items()
        if category_id in category_ids
    }
    if fact_keys and inspect(session.get_bind()).has_table(
        MemoryFactModel.__tablename__
    ):
        session.execute(
            update(MemoryFactModel)
            .where(
                MemoryFactModel.fact_key.in_(fact_keys),
                MemoryFactModel.status.in_(("active", "disputed")),
            )
            .values(status="superseded", valid_to=now, updated_at=now)
        )


def _ensure_seeded() -> None:
    with _get_session() as session:
        count = session.scalar(select(func.count()).select_from(TagCategoryModel)) or 0
        marker = session.get(TagCatalogMetaModel, "seed_version")
        if marker and marker.value == _CATALOG_VERSION:
            _ensure_deletion_tracking(session)
            session.commit()
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
            if _is_category_deleted(session, category.id):
                continue
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
                if _is_tag_deleted(session, category.id, value.name):
                    continue
                session.add(
                    TagDefinitionModel(
                        category_id=category.id,
                        value=value.name,
                        position=value_position,
                    )
                )
        if count and (marker is None or marker.value != _CATALOG_VERSION):
            _upgrade_profile_catalog_v7(session)
            _remove_retired_categories(session, {"intent", "sales_stage"})
        if marker is None:
            session.add(TagCatalogMetaModel(key="seed_version", value=_CATALOG_VERSION))
        else:
            marker.value = _CATALOG_VERSION
        session.flush()
        _ensure_deletion_tracking(session)
        session.commit()


def _upgrade_profile_catalog_v7(session: Session) -> None:
    for (category_id, old_value), new_value in _V7_TAG_RENAMES.items():
        _rename_catalog_value(session, category_id, old_value, new_value)
        session.flush()

    max_position = session.scalar(select(func.max(TagCategoryModel.position))) or 0
    for category in (
        TAG_CATEGORIES["customer_level"],
        TAG_CATEGORIES["orchid_quantity"],
        TAG_CATEGORIES["province"],
        TAG_CATEGORIES["favorite_orchid_type"],
        TAG_CATEGORIES["product_demand"],
        TAG_CATEGORIES["price_range"],
        TAG_CATEGORIES["growing_environment"],
    ):
        row = session.get(TagCategoryModel, category.id)
        if row is None:
            if _is_category_deleted(session, category.id):
                continue
            max_position += 1
            row = TagCategoryModel(
                id=category.id,
                name=category.name,
                prompt_rule=category.prompt_rule,
                ai_assignable=category.ai_assignable,
                exclusive=category.exclusive,
                position=max_position,
            )
            session.add(row)
            session.flush()
        else:
            row.name = category.name
            row.prompt_rule = category.prompt_rule
            row.ai_assignable = category.ai_assignable
            row.exclusive = category.exclusive
        for value in category.values:
            _seed_missing_value(session, category.id, value.name)

    for (old_category_id, old_value), (
        new_category_id,
        new_value,
    ) in _V7_TAG_MOVES.items():
        _move_catalog_value(
            session,
            old_category_id,
            old_value,
            new_category_id,
            new_value,
        )
        session.flush()


def _rename_catalog_value(
    session: Session,
    category_id: str,
    old_value: str,
    new_value: str,
) -> None:
    old_row = session.scalar(
        select(TagDefinitionModel).where(
            TagDefinitionModel.category_id == category_id,
            TagDefinitionModel.value == old_value,
        )
    )
    if old_row is None:
        return
    new_row = session.scalar(
        select(TagDefinitionModel).where(TagDefinitionModel.value == new_value)
    )
    _replace_profile_tag_value(session, old_value, new_value)
    bindings = session.scalars(
        select(TagPromptBindingModel).where(
            TagPromptBindingModel.category_id == category_id,
            TagPromptBindingModel.tag_value == old_value,
        )
    ).all()
    for binding in bindings:
        duplicate = session.scalar(
            select(TagPromptBindingModel.id).where(
                TagPromptBindingModel.category_id == category_id,
                TagPromptBindingModel.tag_value == new_value,
                TagPromptBindingModel.prompt_block_id == binding.prompt_block_id,
            )
        )
        if duplicate is None:
            binding.tag_value = new_value
        else:
            session.delete(binding)
    if new_row is None:
        old_row.value = new_value
    else:
        session.delete(old_row)


def _replace_profile_tag_value(
    session: Session,
    old_value: str,
    new_value: str,
) -> None:
    for profile in session.scalars(select(UserProfileModel)).all():
        try:
            tags = json.loads(profile.customer_tags_json or "[]")
        except (TypeError, ValueError):
            tags = []
        if not isinstance(tags, list) or old_value not in tags:
            continue
        updated: list[str] = []
        for tag in tags:
            value = new_value if tag == old_value else tag
            if value and value not in updated:
                updated.append(value)
        profile.customer_tags_json = json.dumps(updated, ensure_ascii=False)
        profile.updated_at = datetime.now(timezone.utc)


def _move_catalog_value(
    session: Session,
    old_category_id: str,
    old_value: str,
    new_category_id: str,
    new_value: str,
) -> None:
    old_row = session.scalar(
        select(TagDefinitionModel).where(
            TagDefinitionModel.category_id == old_category_id,
            TagDefinitionModel.value == old_value,
        )
    )
    if old_row is None:
        return
    new_row = session.scalar(
        select(TagDefinitionModel).where(TagDefinitionModel.value == new_value)
    )
    _replace_profile_tag_value(session, old_value, new_value)
    if new_row is old_row:
        _retarget_tag_bindings(
            session,
            old_category_id,
            old_value,
            new_category_id,
            new_value,
        )
        old_row.category_id = new_category_id
        return
    if new_row is not None and new_row.category_id != new_category_id:
        _retarget_tag_bindings(
            session,
            new_row.category_id,
            new_value,
            new_category_id,
            new_value,
        )
        new_row.category_id = new_category_id
        session.flush()
    _retarget_tag_bindings(
        session,
        old_category_id,
        old_value,
        new_category_id,
        new_value,
    )
    if new_row is None:
        old_row.category_id = new_category_id
        old_row.value = new_value
    else:
        session.delete(old_row)


def _retarget_tag_bindings(
    session: Session,
    old_category_id: str,
    old_value: str,
    new_category_id: str,
    new_value: str,
) -> None:
    bindings = session.scalars(
        select(TagPromptBindingModel).where(
            TagPromptBindingModel.category_id == old_category_id,
            TagPromptBindingModel.tag_value == old_value,
        )
    ).all()
    for binding in bindings:
        duplicate = session.scalar(
            select(TagPromptBindingModel.id).where(
                TagPromptBindingModel.category_id == new_category_id,
                TagPromptBindingModel.tag_value == new_value,
                TagPromptBindingModel.prompt_block_id == binding.prompt_block_id,
            )
        )
        if duplicate is None:
            binding.category_id = new_category_id
            binding.tag_value = new_value
        else:
            session.delete(binding)


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
    if _is_tag_deleted(session, category_id, value):
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
    session.flush()


def _ensure_deletion_tracking(session: Session) -> None:
    if session.get(TagCatalogMetaModel, _DELETION_TRACKING_MARKER) is not None:
        return
    category_ids = set(session.scalars(select(TagCategoryModel.id)).all())
    tag_values = set(
        session.execute(
            select(TagDefinitionModel.category_id, TagDefinitionModel.value)
        ).all()
    )
    missing_categories: set[str] = set()
    for category in TAG_CATEGORIES.values():
        if category.id not in category_ids:
            missing_categories.add(category.id)
            mark_category_deleted(
                session,
                category.id,
                tuple(value.name for value in category.values),
            )
            continue
        for value in category.values:
            if (category.id, value.name) not in tag_values:
                mark_tag_deleted(session, category.id, value.name)
    retire_deleted_category_data(session, missing_categories)
    session.add(
        TagCatalogMetaModel(key=_DELETION_TRACKING_MARKER, value="enabled")
    )


def _category_deletion_key(category_id: str) -> str:
    return "tag.deleted.category." + _marker_digest(category_id)


def _tag_deletion_key(category_id: str, value: str) -> str:
    return "tag.deleted.value." + _marker_digest(f"{category_id}\0{value}")


def _marker_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _is_category_deleted(session: Session, category_id: str) -> bool:
    return session.get(TagCatalogMetaModel, _category_deletion_key(category_id)) is not None


def _is_tag_deleted(session: Session, category_id: str, value: str) -> bool:
    return session.get(TagCatalogMetaModel, _tag_deletion_key(category_id, value)) is not None


def _set_meta_marker(session: Session, key: str) -> None:
    if session.get(TagCatalogMetaModel, key) is None:
        session.add(TagCatalogMetaModel(key=key, value="deleted"))


def _remove_meta_marker(session: Session, key: str) -> None:
    marker = session.get(TagCatalogMetaModel, key)
    if marker is not None:
        session.delete(marker)


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


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def prompt_blocks_for_labels(labels: list[str]) -> list[str]:
    blocks: list[str] = []
    live_values = {
        value.name
        for category in get_tag_categories().values()
        for value in category.values
    }
    for category in TAG_CATEGORIES.values():
        for value in category.values:
            if (
                value.name in live_values
                and value.prompt_block_id
                and any(_label_value(label) == value.name for label in labels)
            ):
                if value.prompt_block_id not in blocks:
                    blocks.append(value.prompt_block_id)
                break
    return blocks


def _label_value(label: str) -> str:
    return label.split(":", 1)[1] if ":" in label else label
