import re

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.tag import TagResult
from app.services.customer_level_service import classify_customer_level


async def build_tag_result(
    *,
    message: NormalizedMessage,
    user_state: UserState,
    intent: IntentResult,
) -> TagResult:
    segment = _segment_from(message, user_state)
    emotion = intent.customer_sentiment or _emotion_from_message(message.message)
    stage = intent.sales_stage if intent.sales_stage != "unknown" else user_state.sales_stage
    risk_level = _risk_from(intent, user_state)
    customer_level = classify_customer_level(message=message.message, user_state=user_state)
    existing_customer_level_tags = [
        tag for tag in user_state.customer_tags if _is_customer_level_tag(tag)
    ]
    labels = [
        f"customer_tag:{tag}"
        for tag in user_state.customer_tags
        if not _is_customer_level_tag(tag)
    ]
    entities = dict(intent.slots)
    if customer_level.level != "unknown" and customer_level.label:
        labels.append(f"customer_tag:{customer_level.label}")
        entities["customer_level"] = customer_level.model_dump()
    elif existing_customer_level_tags:
        labels.append(f"customer_tag:{existing_customer_level_tags[0]}")
    labels.extend(_memory_labels(message.message, intent))

    return TagResult(
        intent=intent.primary_intent,
        route=intent.route,
        segment=segment,
        emotion=emotion,
        stage=stage,
        risk_level=risk_level,
        confidence=intent.confidence,
        secondary_intents=intent.secondary_intents,
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
    slots = intent.slots if isinstance(intent.slots, dict) else {}
    city = _slot_text(slots, ("city", "location", "region"))
    budget = _slot_text(slots, ("budget", "price_budget", "price"))
    if not city:
        city = _city_from_text(text)
    if not budget:
        budget = _budget_from_text(text)
    if city:
        labels.append(f"region:{city}")
    if budget:
        labels.append(f"budget:{budget}")
    if _has_any(text, ("烂根", "爛根", "root rot")):
        labels.append("pain_point:兰花烂根")
        labels.append("product_interest:兰花养护")
    elif _has_any(text, ("兰花", "蘭花", "orchid")):
        labels.append("product_interest:兰花养护")
    return _dedupe(labels)


def _slot_text(slots: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = slots.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _city_from_text(text: str) -> str:
    known_cities = ("杭州", "北京", "上海", "广州", "深圳", "成都", "南京", "苏州", "宁波")
    for city in known_cities:
        if city in text:
            return city
    return ""


def _budget_from_text(text: str) -> str:
    match = re.search(r"(?:预算|預算|budget|价格|價位|价位)[^\d]{0,8}(\d{2,6})", text, re.I)
    return match.group(1) if match else ""


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
    if intent.need_human or intent.primary_intent in {"complaint", "refund_request", "human_request"}:
        return "high"
    if user_state.risk_level and user_state.risk_level != "normal":
        return user_state.risk_level
    return "normal"


def _is_customer_level_tag(tag: str) -> bool:
    return any(tag.startswith(level) for level in ("L1", "L2", "L3", "L4", "L5", "L6"))
