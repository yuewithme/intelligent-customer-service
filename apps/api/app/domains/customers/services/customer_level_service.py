from __future__ import annotations

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    CustomerLevelProfileModel,
    CustomerLevelPromptBindingModel,
    PromptBlockModel,
    TagPromptBindingModel,
)
from app.domains.sales.services.tag_catalog import get_tag_categories


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [
    CustomerLevelProfileModel.__table__,
    PromptBlockModel.__table__,
    CustomerLevelPromptBindingModel.__table__,
    TagPromptBindingModel.__table__,
]
_PROMPT_PREFIX = "customer_level."
_SEED_MARKER_ID = "system.seed.customer_level_policy.v2"


_LEVEL_PROFILES = [
    {
        "level": "L1",
        "name": "L1 青铜期",
        "description": "潜在用户或试错期新人，养兰经验少，预算低，主要需要建立信任和基础认知。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
    {
        "level": "L2",
        "name": "L2 白银期",
        "description": "种植小白，有少量养兰经验，常见诉求是烂根、黄叶、不开花和基础纠错。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
    {
        "level": "L3",
        "name": "L3 黄金期",
        "description": "入行基础型用户，已有较多品种和养护经验，关注病虫害、环境和经典品种。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
    {
        "level": "L4",
        "name": "L4 铂金期",
        "description": "品种收藏型用户，关注瓣型、老种鉴定、品种档案和价格行情。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
    {
        "level": "L5",
        "name": "L5 宗师期",
        "description": "艺向研究型用户，关注艺草、叶艺进化、虎斑、蛇斑、中透艺等高专业问题。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
    {
        "level": "L6",
        "name": "L6 王者期",
        "description": "品种缔造或高价值交易用户，关注稀有品种、投资、命名权和种源交易。",
        "min_score": 1.0,
        "default_route": "rag_answer",
        "handoff_reason": None,
    },
]


_PROMPT_BLOCKS = {
    "customer_level.l1.identity": (
        "L1 customer: beginner or trial-stage orchid user. Reduce anxiety and explain basics before selling."
    ),
    "customer_level.l1.communication": (
        "Use plain beginner-friendly language. First build trust, then ask one simple clarifying question if needed."
    ),
    "customer_level.l1.recommendation": (
        "Recommend low-risk, easy-care, fragrant, affordable orchids. Do not recommend high-price or complex varieties."
    ),
    "customer_level.l2.identity": (
        "L2 customer: has some orchid experience but still needs practical correction for common care mistakes."
    ),
    "customer_level.l2.communication": (
        "Acknowledge their experience, ask region and current care method, then correct watering, substrate, and disease basics."
    ),
    "customer_level.l2.recommendation": (
        "Recommend reliable regional varieties and care-safe products. Avoid expensive art orchids and hard-to-grow premium varieties."
    ),
    "customer_level.l3.identity": (
        "L3 customer: experienced orchid user with larger collection and stronger interest in varieties, disease control, and environment."
    ),
    "customer_level.l3.communication": (
        "Respect their experience. Avoid over-explaining beginner concepts; focus on source, environment, disease prevention, and tradeoffs."
    ),
    "customer_level.l3.recommendation": (
        "Recommend classic varieties, color flowers, and proven old varieties when relevant. Avoid too-basic commodity suggestions."
    ),
    "customer_level.l4.identity": (
        "L4 customer: an experienced collector who values petal form, classic cultivars, provenance, and market context."
    ),
    "customer_level.l4.communication": (
        "Use precise collector language, respect existing knowledge, and focus on evidence, distinctions, and tradeoffs."
    ),
    "customer_level.l4.recommendation": (
        "Prefer credible classic or collectible varieties when relevant; never infer authenticity, scarcity, or appreciation potential without verified facts."
    ),
    "customer_level.l5.identity": (
        "L5 customer: an advanced enthusiast with strong interest in leaf art and specialist orchid traits."
    ),
    "customer_level.l5.communication": (
        "Be concise and technically precise. Do not repeat beginner basics unless the current question requires them."
    ),
    "customer_level.l5.recommendation": (
        "Align recommendations with verified art-orchid traits and provenance; do not make unsupported stability or evolution claims."
    ),
    "customer_level.l6.identity": (
        "L6 customer: a highly experienced or high-value orchid customer. Treat expertise and transaction risk with care."
    ),
    "customer_level.l6.communication": (
        "Communicate directly and professionally, verify high-value facts, and avoid sales pressure or basic explanations."
    ),
    "customer_level.l6.recommendation": (
        "Only present premium or rare options when relevant and verified; customer level alone never triggers handoff or a purchase action."
    ),
}


_PROMPT_BINDINGS = {
    "L1": [
        "customer_level.l1.identity",
        "customer_level.l1.communication",
        "customer_level.l1.recommendation",
    ],
    "L2": [
        "customer_level.l2.identity",
        "customer_level.l2.communication",
        "customer_level.l2.recommendation",
    ],
    "L3": [
        "customer_level.l3.identity",
        "customer_level.l3.communication",
        "customer_level.l3.recommendation",
    ],
    "L4": [
        "customer_level.l4.identity",
        "customer_level.l4.communication",
        "customer_level.l4.recommendation",
    ],
    "L5": [
        "customer_level.l5.identity",
        "customer_level.l5.communication",
        "customer_level.l5.recommendation",
    ],
    "L6": [
        "customer_level.l6.identity",
        "customer_level.l6.communication",
        "customer_level.l6.recommendation",
    ],
}


def seed_customer_level_policy() -> None:
    live_labels = _live_customer_level_labels()
    with _get_session() as session:
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == "customer_level"
            )
        )
        session.execute(delete(CustomerLevelPromptBindingModel))
        session.execute(delete(CustomerLevelProfileModel))
        session.execute(
            delete(PromptBlockModel).where(PromptBlockModel.block_id.startswith(_PROMPT_PREFIX))
        )
        for item in _LEVEL_PROFILES:
            session.add(CustomerLevelProfileModel(**item, enabled=True))
        for block_id, content in _PROMPT_BLOCKS.items():
            session.add(
                PromptBlockModel(
                    block_id=block_id,
                    title=block_id,
                    content=content,
                    enabled=True,
                )
            )
        for level, block_ids in _PROMPT_BINDINGS.items():
            for priority, block_id in enumerate(block_ids, start=1):
                session.add(
                    CustomerLevelPromptBindingModel(
                        level=level,
                        prompt_block_id=block_id,
                        priority=priority,
                        enabled=True,
                    )
                )
                label = next(
                    item["name"] for item in _LEVEL_PROFILES if item["level"] == level
                )
                if label in live_labels:
                    session.add(
                        TagPromptBindingModel(
                            category_id="customer_level",
                            tag_value=label,
                            prompt_block_id=block_id,
                            priority=priority,
                            enabled=True,
                        )
                    )
        marker = session.get(PromptBlockModel, _SEED_MARKER_ID)
        if marker is None:
            session.add(
                PromptBlockModel(
                    block_id=_SEED_MARKER_ID,
                    title="Customer level policy v2 seeded",
                    content="",
                    enabled=False,
                )
            )
        session.commit()


def get_customer_level_prompt_block_ids(level: str) -> list[str]:
    _ensure_seeded()
    with _get_session() as session:
        label = session.scalar(
            select(CustomerLevelProfileModel.name).where(
                CustomerLevelProfileModel.level == level
            )
        )
        if not label:
            return []
        if label not in _live_customer_level_labels():
            return []
        rows = session.scalars(
            select(TagPromptBindingModel)
            .where(
                TagPromptBindingModel.category_id == "customer_level",
                TagPromptBindingModel.tag_value == label,
                TagPromptBindingModel.enabled.is_(True),
            )
            .order_by(TagPromptBindingModel.priority.asc(), TagPromptBindingModel.id.asc())
        ).all()
        return [row.prompt_block_id for row in rows]


def prompt_blocks_for_customer_level_labels(labels: list[str]) -> list[str]:
    for label in labels:
        value = label.split(":", 1)[1] if ":" in label else label
        level = _level_from_label(value)
        if level in {"L1", "L2", "L3", "L4", "L5", "L6"}:
            return get_customer_level_prompt_block_ids(level)
    return []


def clear_cache() -> None:
    _sessionmakers.clear()


def _ensure_seeded() -> None:
    with _get_session() as session:
        has_profile = session.scalar(select(CustomerLevelProfileModel.level).limit(1))
        marker = session.get(PromptBlockModel, _SEED_MARKER_ID)
    if has_profile is None:
        seed_customer_level_policy()
        return
    if marker is not None:
        return
    with _get_session() as session:
        profiles = {
            row.level: row
            for row in session.scalars(select(CustomerLevelProfileModel)).all()
        }
        for item in _LEVEL_PROFILES:
            profile = profiles.get(item["level"])
            if profile is None:
                profile = CustomerLevelProfileModel(**item, enabled=True)
                session.add(profile)
                profiles[item["level"]] = profile
            elif item["level"] in {"L4", "L5", "L6"}:
                profile.default_route = "rag_answer"
                profile.handoff_reason = None
        for block_id, content in _PROMPT_BLOCKS.items():
            if session.get(PromptBlockModel, block_id) is None:
                session.add(
                    PromptBlockModel(
                        block_id=block_id,
                        title=block_id,
                        content=content,
                        enabled=True,
                    )
                )
        live_labels = _live_customer_level_labels()
        for level, block_ids in _PROMPT_BINDINGS.items():
            label = profiles[level].name
            for priority, block_id in enumerate(block_ids, start=1):
                level_binding = session.scalar(
                    select(CustomerLevelPromptBindingModel.id).where(
                        CustomerLevelPromptBindingModel.level == level,
                        CustomerLevelPromptBindingModel.prompt_block_id == block_id,
                    )
                )
                if level_binding is None:
                    session.add(
                        CustomerLevelPromptBindingModel(
                            level=level,
                            prompt_block_id=block_id,
                            priority=priority,
                            enabled=True,
                        )
                    )
                if label in live_labels:
                    tag_binding = session.scalar(
                        select(TagPromptBindingModel.id).where(
                            TagPromptBindingModel.category_id == "customer_level",
                            TagPromptBindingModel.tag_value == label,
                            TagPromptBindingModel.prompt_block_id == block_id,
                        )
                    )
                    if tag_binding is None:
                        session.add(
                            TagPromptBindingModel(
                                category_id="customer_level",
                                tag_value=label,
                                prompt_block_id=block_id,
                                priority=priority,
                                enabled=True,
                            )
                        )
        session.add(
            PromptBlockModel(
                block_id=_SEED_MARKER_ID,
                title="Customer level policy v2 seeded",
                content="",
                enabled=False,
            )
        )
        session.commit()


def _live_customer_level_labels() -> set[str]:
    category = get_tag_categories().get("customer_level")
    return {value.name for value in category.values} if category else set()


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _level_from_label(value: str) -> str | None:
    if value.startswith("L1"):
        return "L1"
    if value.startswith("L2"):
        return "L2"
    if value.startswith("L3"):
        return "L3"
    if value.startswith("L4"):
        return "L4"
    if value.startswith("L5"):
        return "L5"
    if value.startswith("L6"):
        return "L6"
    return None
