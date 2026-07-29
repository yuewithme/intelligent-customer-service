from __future__ import annotations

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    CustomerLevelProfileModel,
    CustomerLevelPromptBindingModel,
    CustomerLevelRuleModel,
    PromptBlockModel,
    TagPromptBindingModel,
)
from app.domains.customers.schemas.customer_level import CustomerLevelResult
from app.domains.customers.schemas.state import UserState
from app.domains.sales.services.tag_catalog import get_tag_categories


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [
    CustomerLevelProfileModel.__table__,
    CustomerLevelRuleModel.__table__,
    PromptBlockModel.__table__,
    CustomerLevelPromptBindingModel.__table__,
    TagPromptBindingModel.__table__,
]
_PROMPT_PREFIX = "customer_level."


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
        "default_route": "human",
        "handoff_reason": "advanced_customer_level",
    },
    {
        "level": "L5",
        "name": "L5 宗师期",
        "description": "艺向研究型用户，关注艺草、叶艺进化、虎斑、蛇斑、中透艺等高专业问题。",
        "min_score": 1.0,
        "default_route": "human",
        "handoff_reason": "advanced_customer_level",
    },
    {
        "level": "L6",
        "name": "L6 王者期",
        "description": "品种缔造或高价值交易用户，关注稀有品种、投资、命名权和种源交易。",
        "min_score": 1.0,
        "default_route": "human",
        "handoff_reason": "advanced_customer_level",
    },
]


_RULES = {
    "L1": [
        ("keyword_any", ["新手", "刚开始", "没养过", "泥土", "水培", "预算30", "预算50", "10盆以内"], 1.0),
    ],
    "L2": [
        ("keyword_any", ["烂根", "黄叶", "焦尖", "不开花", "芦头", "植料", "叶芽", "花芽"], 1.0),
    ],
    "L3": [
        ("keyword_any", ["100盆", "一百多盆", "病虫害", "经典品种", "跨区引种", "春兰", "蕙兰"], 1.0),
    ],
    "L4": [
        ("keyword_any", ["瓣型", "老八种", "品种档案", "老种鉴定", "价格行情", "荷瓣"], 1.0),
    ],
    "L5": [
        ("keyword_any", ["艺草", "虎斑", "蛇斑", "中透艺", "叶艺", "返青"], 1.0),
    ],
    "L6": [
        ("keyword_any", ["命名权", "转卖", "种源交易", "投资", "几万", "稀有品种"], 1.0),
    ],
}


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
}


def seed_customer_level_policy() -> None:
    with _get_session() as session:
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == "customer_level"
            )
        )
        session.execute(delete(CustomerLevelPromptBindingModel))
        session.execute(delete(CustomerLevelRuleModel))
        session.execute(delete(CustomerLevelProfileModel))
        session.execute(
            delete(PromptBlockModel).where(PromptBlockModel.block_id.startswith(_PROMPT_PREFIX))
        )
        for item in _LEVEL_PROFILES:
            session.add(CustomerLevelProfileModel(**item, enabled=True))
        for level, rules in _RULES.items():
            for rule_type, patterns, weight in rules:
                for pattern in patterns:
                    session.add(
                        CustomerLevelRuleModel(
                            level=level,
                            rule_type=rule_type,
                            pattern=pattern,
                            weight=weight,
                            evidence_label=pattern,
                            enabled=True,
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
                session.add(
                    TagPromptBindingModel(
                        category_id="customer_level",
                        tag_value=label,
                        prompt_block_id=block_id,
                        priority=priority,
                        enabled=True,
                    )
                )
        session.commit()


def classify_customer_level(*, message: str, user_state: UserState) -> CustomerLevelResult:
    _ensure_seeded()
    evidence_text = _evidence_text(message, user_state)
    with _get_session() as session:
        profiles = {
            row.level: row
            for row in session.scalars(
                select(CustomerLevelProfileModel).where(CustomerLevelProfileModel.enabled.is_(True))
            )
        }
        rules = session.scalars(
            select(CustomerLevelRuleModel).where(CustomerLevelRuleModel.enabled.is_(True))
        ).all()

    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for rule in rules:
        if rule.rule_type == "keyword_any" and rule.pattern.lower() in evidence_text:
            scores[rule.level] = scores.get(rule.level, 0.0) + rule.weight
            evidence.setdefault(rule.level, []).append(rule.evidence_label)

    candidates = [
        (level, score)
        for level, score in scores.items()
        if level in profiles and score >= profiles[level].min_score
    ]
    if not candidates:
        return CustomerLevelResult()

    level, score = sorted(candidates, key=lambda item: (_level_rank(item[0]), item[1]), reverse=True)[0]
    profile = profiles[level]
    live_category = get_tag_categories().get("customer_level")
    live_values = {value.name for value in live_category.values} if live_category else set()
    if profile.name not in live_values:
        return CustomerLevelResult()
    return CustomerLevelResult(
        level=level,
        label=profile.name,
        route=profile.default_route,
        confidence=round(min(0.99, score / (score + 3)), 2),
        score=score,
        matched_evidence=evidence.get(level, []),
        handoff_reason=profile.handoff_reason,
    )


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


def get_customer_level_prompt_blocks(block_ids: list[str]) -> dict[str, str]:
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


def prompt_blocks_for_customer_level_labels(labels: list[str]) -> list[str]:
    for label in labels:
        value = label.split(":", 1)[1] if ":" in label else label
        level = _level_from_label(value)
        if level in {"L1", "L2", "L3"}:
            return get_customer_level_prompt_block_ids(level)
    return []


def advanced_customer_level_from_labels(labels: list[str]) -> str | None:
    for label in labels:
        value = label.split(":", 1)[1] if ":" in label else label
        level = _level_from_label(value)
        if level in {"L4", "L5", "L6"}:
            return level
    return None


def clear_cache() -> None:
    _sessionmakers.clear()


def _ensure_seeded() -> None:
    with _get_session() as session:
        has_profile = session.scalar(select(CustomerLevelProfileModel.level).limit(1))
        has_catalog_binding = session.scalar(
            select(TagPromptBindingModel.id)
            .where(TagPromptBindingModel.category_id == "customer_level")
            .limit(1)
        )
    if has_profile is None:
        seed_customer_level_policy()
    elif has_catalog_binding is None:
        with _get_session() as session:
            profiles = {
                row.level: row.name
                for row in session.scalars(select(CustomerLevelProfileModel)).all()
            }
            bindings = session.scalars(select(CustomerLevelPromptBindingModel)).all()
            for binding in bindings:
                label = profiles.get(binding.level)
                if label:
                    session.add(
                        TagPromptBindingModel(
                            category_id="customer_level",
                            tag_value=label,
                            prompt_block_id=binding.prompt_block_id,
                            priority=binding.priority,
                            enabled=binding.enabled,
                        )
                    )
            session.commit()


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _evidence_text(message: str, user_state: UserState) -> str:
    del user_state
    # Customer-level evidence must be customer-authored. Product titles, tool
    # results, assistant replies, and inferred metadata are execution state and
    # must never raise the customer's expertise level.
    return str(message or "").lower()


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


def _level_rank(level: str) -> int:
    try:
        return int(level[1:])
    except (ValueError, IndexError):
        return 0
