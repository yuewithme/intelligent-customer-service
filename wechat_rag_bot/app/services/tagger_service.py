from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.tag import TagResult


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

    return TagResult(
        intent=intent.primary_intent,
        route=intent.route,
        segment=segment,
        emotion=emotion,
        stage=stage,
        risk_level=risk_level,
        confidence=intent.confidence,
        secondary_intents=intent.secondary_intents,
        entities=dict(intent.slots),
        labels=[f"customer_tag:{tag}" for tag in user_state.customer_tags],
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
