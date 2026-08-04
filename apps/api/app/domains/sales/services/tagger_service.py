from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.sales.schemas.tag import TagResult
from app.domains.customers.services.customer_level_service import classify_customer_level
from app.domains.sales.services.tag_catalog import (
    filter_profile_tags,
    filter_runtime_labels,
    normalize_system_value,
)


async def build_tag_result(
    *,
    message: NormalizedMessage,
    user_state: UserState,
    intent: IntentResult,
) -> TagResult:
    segment = normalize_system_value(
        "customer_segment", _segment_from(message, user_state), fallback="unknown"
    )
    emotion = normalize_system_value(
        "customer_sentiment",
        intent.customer_sentiment or _emotion_from_message(message.message),
        fallback="neutral",
    )
    stage = normalize_system_value(
        "sales_stage",
        intent.sales_stage if intent.sales_stage != "unknown" else user_state.sales_stage,
        fallback="unknown",
    )
    risk_level = _risk_from(intent, user_state)
    primary_intent = normalize_system_value(
        "intent", intent.primary_intent, fallback="unknown"
    )
    customer_level = classify_customer_level(message=message.message, user_state=user_state)
    existing_customer_level_tags = [
        tag for tag in user_state.customer_tags if _is_customer_level_tag(tag)
    ]
    labels = [
        f"customer_tag:{tag}"
        for tag in filter_profile_tags(user_state.customer_tags)
        if not _is_customer_level_tag(tag)
    ]
    entities = dict(intent.slots)
    pain_point = _pain_point_from_text(message.message)
    if pain_point and not entities.get("pain_point"):
        entities["pain_point"] = pain_point
    if customer_level.level != "unknown" and customer_level.label:
        labels.append(f"customer_tag:{customer_level.label}")
        entities["customer_level"] = customer_level.model_dump()
    elif existing_customer_level_tags:
        labels.append(f"customer_tag:{existing_customer_level_tags[0]}")
    labels.extend(_memory_labels(message.message, intent))
    labels = filter_runtime_labels(labels)

    secondary_intents = []
    for value in intent.secondary_intents:
        normalized = normalize_system_value("intent", value, fallback="")
        if normalized and normalized != primary_intent and normalized not in secondary_intents:
            secondary_intents.append(normalized)

    return TagResult(
        intent=primary_intent,
        route=intent.route,
        segment=segment,
        emotion=emotion,
        stage=stage,
        risk_level=normalize_system_value(
            "risk_level", risk_level, fallback="normal"
        ),
        confidence=intent.confidence,
        secondary_intents=secondary_intents,
        entities=entities,
        labels=labels,
        reason=intent.reason,
    )


def _segment_from(message: NormalizedMessage, user_state: UserState) -> str:
    metadata_segment = message.metadata.get("segment")
    if isinstance(metadata_segment, str) and metadata_segment:
        return metadata_segment
    if "newbie" in user_state.customer_tags or "新手" in user_state.customer_tags:
        return "beginner"
    if "advanced" in user_state.customer_tags or "老手" in user_state.customer_tags:
        return "advanced"
    return "unknown"


def _memory_labels(text: str, intent: IntentResult) -> list[str]:
    labels: list[str] = []
    pain_point = _pain_point_from_text(text)
    if pain_point:
        labels.append(f"pain_point:兰花{pain_point}")
        labels.append("product_interest:兰花养护")
    elif _has_any(text, ("兰花", "蘭花", "orchid")):
        labels.append("product_interest:兰花养护")
    return filter_runtime_labels(_dedupe(labels))


def _pain_point_from_text(text: str) -> str:
    issues: list[str] = []
    markers = (
        ("黑斑", ("黑斑", "叶斑")),
        ("黄叶", ("黄叶", "黃葉", "叶子发黄", "葉子發黃")),
        ("烂根", ("烂根", "爛根", "黑根", "空根")),
        ("腐苗", ("腐苗", "烂苗", "软腐")),
        ("焦尖", ("焦尖", "干尖", "枯尖")),
        ("不开花", ("不开花", "不開花", "不来花")),
    )
    for label, words in markers:
        if _has_any(text, words):
            issues.append(label)
    return "、".join(issues)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _emotion_from_message(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["angry", "complaint", "terrible", "生气", "投诉", "太差", "不满"]):
        return "angry"
    if any(word in lowered for word in ["worried", "anxious", "what should i do", "担心", "害怕", "焦虑", "怎么办"]):
        return "anxious"
    return "neutral"


def _risk_from(intent: IntentResult, user_state: UserState) -> str:
    del user_state
    if intent.need_human or intent.primary_intent in {"complaint", "refund_request", "human_request"}:
        return "high"
    return "normal"


def _is_customer_level_tag(tag: str) -> bool:
    return any(tag.startswith(level) for level in ("L1", "L2", "L3", "L4", "L5", "L6"))
