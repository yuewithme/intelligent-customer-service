from __future__ import annotations

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, PromptBlockModel, TagPromptBindingModel
from app.domains.sales.services.tag_catalog import get_tag_categories


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [PromptBlockModel.__table__, TagPromptBindingModel.__table__]
_OWNED_PREFIXES = (
    "orchid_quantity.",
    "region.",
    "orchid_preference.",
    "product_demand.",
    "price_range.",
    "growing_environment.",
)
_LEGACY_SEED_MARKER_ID = "system.seed.business_tag_prompt_policy"
_SEED_MARKER_ID = "system.seed.business_tag_prompt_policy.v2"


_PROMPT_BLOCKS = {
    "orchid_quantity.small.focus": (
        "The user keeps a small orchid collection. Focus on confidence, simple care steps, and low-risk choices."
    ),
    "orchid_quantity.medium.focus": (
        "The user keeps a medium orchid collection. Balance practical care routines with variety expansion advice."
    ),
    "orchid_quantity.large.focus": (
        "The user keeps a large orchid collection. Focus on scalable care, batch management, prevention, and efficiency."
    ),
    "region.east_china.variety": (
        "The user is in East China. Prefer classic Guolan choices such as Chunlan, Huilan, Jianlan, and stable old varieties when relevant."
    ),
    "region.north_china.variety": (
        "The user is in North China. Prioritize cold and dry-climate tolerance, spring-vernalization needs, and easy-care varieties."
    ),
    "region.south_china.variety": (
        "The user is in South China. Prioritize heat, humidity, ventilation, and disease-prevention fit; Jianlan and Molan are often safer examples."
    ),
    "region.southwest.variety": (
        "The user is in Southwest China. Consider altitude, humidity, and regional orchid resources before recommending varieties."
    ),
    "region.northwest.variety": (
        "The user is in Northwest China. Prioritize drought tolerance, indoor humidity management, and conservative variety recommendations."
    ),
    "region.overseas.context": (
        "The user is overseas. Do not infer climate or logistics from this broad tag; ask for a more specific location only when it materially changes the answer."
    ),
    "orchid_preference.chunlan": (
        "The user prefers Chunlan. Use Chunlan examples and avoid drifting to unrelated varieties unless comparison is useful."
    ),
    "orchid_preference.jianlan": (
        "The user prefers Jianlan. Use Jianlan examples; emphasize fragrance, flowering frequency, and beginner-friendly resilience when relevant."
    ),
    "orchid_preference.molan": (
        "The user prefers Molan. Use Molan examples; account for winter bloom, leaf posture, and indoor ornamental value."
    ),
    "orchid_preference.hanlan": (
        "The user prefers Hanlan. Use Hanlan examples and keep recommendations more conservative because variety fit can be specific."
    ),
    "orchid_preference.huilan": (
        "The user prefers Huilan. Use Huilan examples; mention vernalization and regional climate constraints when relevant."
    ),
    "orchid_preference.lianbanlan": (
        "The user prefers Lianbanlan. Keep recommendations aligned with Lianbanlan traits and regional adaptation."
    ),
    "orchid_preference.chunjian": (
        "The user prefers Chunjian. Use Chunjian examples and consider regional adaptation and fragrance expectations."
    ),
    "orchid_preference.cymbidium": (
        "The user prefers large colorful orchids. Distinguish these from traditional Guolan before making care or product claims."
    ),
    "orchid_preference.doubanlan": (
        "The user prefers Doubanlan. Keep examples and recommendations aligned with its regional fit and flower characteristics."
    ),
    "orchid_preference.niche": (
        "The user prefers niche orchid types such as Songchun or Qiuzhi. Avoid replacing that preference with mainstream varieties without a clear reason."
    ),
    "orchid_preference.open": (
        "The user has no fixed orchid-category preference. Do not ask for a category unless it would materially change the recommendation."
    ),
    "product_demand.preference": (
        "Treat the customer's product-demand tags as positive selection preferences, not as purchase intent. Use them when relevant and do not repeat questions already answered by those tags."
    ),
    "product_demand.open": (
        "The user has no fixed product-trait preference. Do not force another trait question unless it materially changes the recommendation."
    ),
    "price_range.constraint": (
        "Treat the latest price-range tag as the customer's budget constraint. Prefer matching products within it and do not pressure the customer above it."
    ),
    "price_range.open": (
        "The user has explicitly said price is open. Prioritize fit and value instead of asking for a budget again."
    ),
    "growing_environment.fit": (
        "Use the customer's actual growing-environment tag to judge product fit and care advice. Do not infer the environment from province alone."
    ),
}


_BINDINGS = {
    "orchid_quantity": {
        "准备养兰": "orchid_quantity.small.focus",
        "1-9盆": "orchid_quantity.small.focus",
        "10-29盆": "orchid_quantity.small.focus",
        "30-49盆": "orchid_quantity.medium.focus",
        "50-99盆": "orchid_quantity.medium.focus",
        "100-199盆": "orchid_quantity.large.focus",
        "200-499盆": "orchid_quantity.large.focus",
        "500-999盆": "orchid_quantity.large.focus",
        "1000盆以上": "orchid_quantity.large.focus",
    },
    "province": {
        "浙江省": "region.east_china.variety",
        "上海市": "region.east_china.variety",
        "江苏省": "region.east_china.variety",
        "安徽省": "region.east_china.variety",
        "福建省": "region.east_china.variety",
        "江西省": "region.east_china.variety",
        "山东省": "region.east_china.variety",
        "北京市": "region.north_china.variety",
        "天津市": "region.north_china.variety",
        "河北省": "region.north_china.variety",
        "山西省": "region.north_china.variety",
        "辽宁省": "region.north_china.variety",
        "吉林省": "region.north_china.variety",
        "黑龙江省": "region.north_china.variety",
        "河南省": "region.north_china.variety",
        "广东省": "region.south_china.variety",
        "广西壮族自治区": "region.south_china.variety",
        "海南省": "region.south_china.variety",
        "湖北省": "region.southwest.variety",
        "湖南省": "region.southwest.variety",
        "重庆市": "region.southwest.variety",
        "四川省": "region.southwest.variety",
        "贵州省": "region.southwest.variety",
        "云南省": "region.southwest.variety",
        "陕西省": "region.northwest.variety",
        "甘肃省": "region.northwest.variety",
        "青海省": "region.northwest.variety",
        "内蒙古": "region.northwest.variety",
        "宁夏": "region.northwest.variety",
        "新疆": "region.northwest.variety",
        "西藏自治区": "region.northwest.variety",
        "香港": "region.south_china.variety",
        "澳门": "region.south_china.variety",
        "台湾": "region.south_china.variety",
        "海外": "region.overseas.context",
    },
    "favorite_orchid_type": {
        "春兰": "orchid_preference.chunlan",
        "建兰": "orchid_preference.jianlan",
        "墨兰": "orchid_preference.molan",
        "寒兰": "orchid_preference.hanlan",
        "蕙兰": "orchid_preference.huilan",
        "莲瓣兰": "orchid_preference.lianbanlan",
        "春剑": "orchid_preference.chunjian",
        "豆瓣兰": "orchid_preference.doubanlan",
        "大花蕙兰等花大色漂亮的": "orchid_preference.cymbidium",
        "小众品类（送春、秋芝等）": "orchid_preference.niche",
        "品类不限": "orchid_preference.open",
    },
    "product_demand": {
        **{
            value: "product_demand.preference"
            for value in (
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
            )
        },
        "需求不限": "product_demand.open",
    },
    "price_range": {
        **{
            value: "price_range.constraint"
            for value in (
                "50元以内",
                "51-100元",
                "101-200元",
                "201-500元",
                "501-999元",
                "1000元以上",
            )
        },
        "价格不限": "price_range.open",
    },
    "growing_environment": {
        value: "growing_environment.fit"
        for value in ("阳台", "室内", "庭院/露台", "室外露养", "有兰棚")
    },
}

_V2_NEW_BINDING_VALUES = {
    "准备养兰",
    "500-999盆",
    "香港",
    "澳门",
    "台湾",
    "海外",
    "豆瓣兰",
    "小众品类（送春、秋芝等）",
    "品类不限",
    *tuple(_BINDINGS["product_demand"]),
    *tuple(_BINDINGS["price_range"]),
    *tuple(_BINDINGS["growing_environment"]),
}


def seed_business_tag_prompt_policy() -> None:
    live_bindings = _live_bindings()
    live_block_ids = {
        block_id for bindings in live_bindings.values() for block_id in bindings.values()
    }
    with _get_session() as session:
        session.execute(
            delete(PromptBlockModel).where(PromptBlockModel.block_id == _SEED_MARKER_ID)
        )
        for prefix in _OWNED_PREFIXES:
            session.execute(
                delete(PromptBlockModel).where(PromptBlockModel.block_id.startswith(prefix))
            )
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id.in_(list(_BINDINGS.keys()))
            )
        )
        for block_id, content in _PROMPT_BLOCKS.items():
            if block_id not in live_block_ids:
                continue
            session.add(
                PromptBlockModel(
                    block_id=block_id,
                    title=block_id,
                    content=content,
                    enabled=True,
                )
            )
        session.add(
            PromptBlockModel(
                block_id=_SEED_MARKER_ID,
                title="Business tag prompt policy seeded",
                content="",
                enabled=False,
            )
        )
        for category_id, bindings in live_bindings.items():
            for tag_value, block_id in bindings.items():
                session.add(
                    TagPromptBindingModel(
                        category_id=category_id,
                        tag_value=tag_value,
                        prompt_block_id=block_id,
                        priority=1,
                        enabled=True,
                    )
                )
        session.commit()


def get_business_tag_prompt_block_ids(labels: list[str]) -> list[str]:
    _ensure_seeded()
    live_values = {
        value.name
        for category in get_tag_categories().values()
        for value in category.values
    }
    values = [
        value for label in labels if (value := _label_value(label)) in live_values
    ]
    if not values:
        return []
    with _get_session() as session:
        rows = session.scalars(
            select(TagPromptBindingModel)
            .where(
                TagPromptBindingModel.tag_value.in_(values),
                TagPromptBindingModel.enabled.is_(True),
            )
            .order_by(
                TagPromptBindingModel.category_id.asc(),
                TagPromptBindingModel.priority.asc(),
                TagPromptBindingModel.id.asc(),
            )
        ).all()
    ordered = []
    category_order = {"orchid_quantity": 0, "province": 1, "favorite_orchid_type": 2}
    rows = sorted(rows, key=lambda row: (category_order.get(row.category_id, 99), row.priority, row.id))
    for row in rows:
        if row.prompt_block_id not in ordered:
            ordered.append(row.prompt_block_id)
    return ordered


def get_prompt_blocks(block_ids: list[str]) -> dict[str, str]:
    if not block_ids:
        return {}
    _ensure_seeded()
    with _get_session() as session:
        rows = session.scalars(
            select(PromptBlockModel).where(
                PromptBlockModel.block_id.in_(block_ids),
                PromptBlockModel.enabled.is_(True),
            )
        ).all()
    return {row.block_id: row.content for row in rows}


def clear_cache() -> None:
    _sessionmakers.clear()


def ensure_business_tag_prompt_policy() -> None:
    _ensure_seeded()


def _ensure_seeded() -> None:
    live_bindings = _live_bindings()
    live_block_ids = {
        block_id for bindings in live_bindings.values() for block_id in bindings.values()
    }
    with _get_session() as session:
        marker = session.get(PromptBlockModel, _SEED_MARKER_ID)
        if marker is not None:
            return
        legacy_seeded = session.get(PromptBlockModel, _LEGACY_SEED_MARKER_ID) is not None
        for block_id, content in _PROMPT_BLOCKS.items():
            if block_id not in live_block_ids:
                continue
            if session.get(PromptBlockModel, block_id) is None:
                session.add(
                    PromptBlockModel(
                        block_id=block_id,
                        title=block_id,
                        content=content,
                        enabled=True,
                    )
                )
        for category_id, bindings in live_bindings.items():
            for tag_value, block_id in bindings.items():
                if legacy_seeded and tag_value not in _V2_NEW_BINDING_VALUES:
                    continue
                existing = session.scalar(
                    select(TagPromptBindingModel.id).where(
                        TagPromptBindingModel.category_id == category_id,
                        TagPromptBindingModel.tag_value == tag_value,
                        TagPromptBindingModel.prompt_block_id == block_id,
                    )
                )
                if existing is None:
                    session.add(
                        TagPromptBindingModel(
                            category_id=category_id,
                            tag_value=tag_value,
                            prompt_block_id=block_id,
                            priority=1,
                            enabled=True,
                        )
                    )
        session.add(
            PromptBlockModel(
                block_id=_SEED_MARKER_ID,
                title="Business tag prompt policy v2 seeded",
                content="",
                enabled=False,
            )
        )
        session.commit()


def _live_bindings() -> dict[str, dict[str, str]]:
    categories = get_tag_categories()
    return {
        category_id: {
            tag_value: block_id
            for tag_value, block_id in bindings.items()
            if any(value.name == tag_value for value in categories[category_id].values)
        }
        for category_id, bindings in _BINDINGS.items()
        if category_id in categories
    }


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _label_value(label: str) -> str:
    return label.split(":", 1)[1] if ":" in label else label
