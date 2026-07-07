from __future__ import annotations

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, PromptBlockModel, TagPromptBindingModel


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [PromptBlockModel.__table__, TagPromptBindingModel.__table__]
_OWNED_PREFIXES = ("orchid_quantity.", "region.", "orchid_preference.")


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
}


_BINDINGS = {
    "orchid_quantity": {
        "1-10盆": "orchid_quantity.small.focus",
        "10-30盆": "orchid_quantity.small.focus",
        "30-50盆": "orchid_quantity.medium.focus",
        "50-100盆": "orchid_quantity.medium.focus",
        "100-200盆": "orchid_quantity.large.focus",
        "200+盆": "orchid_quantity.large.focus",
        "1000+盆": "orchid_quantity.large.focus",
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
        "广西省": "region.south_china.variety",
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
    },
    "favorite_orchid_type": {
        "春兰": "orchid_preference.chunlan",
        "建兰": "orchid_preference.jianlan",
        "墨兰": "orchid_preference.molan",
        "寒兰": "orchid_preference.hanlan",
        "蕙兰": "orchid_preference.huilan",
        "莲瓣兰": "orchid_preference.lianbanlan",
        "春剑": "orchid_preference.chunjian",
        "大花蕙兰等花大色漂亮的": "orchid_preference.cymbidium",
    },
}


def seed_business_tag_prompt_policy() -> None:
    with _get_session() as session:
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
            session.add(
                PromptBlockModel(
                    block_id=block_id,
                    title=block_id,
                    content=content,
                    enabled=True,
                )
            )
        for category_id, bindings in _BINDINGS.items():
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
    values = [_label_value(label) for label in labels]
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


def _ensure_seeded() -> None:
    with _get_session() as session:
        has_binding = session.scalar(
            select(TagPromptBindingModel.id)
            .where(TagPromptBindingModel.category_id.in_(list(_BINDINGS.keys())))
            .limit(1)
        )
    if has_binding is None:
        seed_business_tag_prompt_policy()


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
