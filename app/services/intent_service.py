from pydantic import ValidationError

from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


PRICE_WORDS = ("价格", "多少钱", "优惠", "便宜", "贵", "太贵", "有点贵")
CARE_WORDS = ("养护", "养不活", "怎么养", "浇水", "施肥", "护理", "方法")
LOGISTICS_WORDS = ("物流", "发货", "快递")
AFTER_SALE_WORDS = ("售后", "保修")


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _mock_classify(text: str) -> dict:
    if _has_any(text, ("你好", "您好", "hi", "hello")):
        return {
            "route": "chitchat",
            "primary_intent": "greeting",
            "confidence": 0.9,
        }

    has_price = _has_any(text, PRICE_WORDS) or "考虑" in text
    has_care = _has_any(text, CARE_WORDS) or "知识" in text or "资料" in text
    if has_price and has_care:
        return {
            "route": "template_then_rag",
            "primary_intent": "price_objection",
            "secondary_intents": ["care_question"],
            "sales_stage": "objection_handling",
            "confidence": 0.88,
            "need_template": True,
            "need_rag": True,
        }
    if has_price:
        return {
            "route": "template_reply",
            "primary_intent": "price_objection" if "贵" in text else "ask_price",
            "sales_stage": "objection_handling",
            "confidence": 0.86,
            "need_template": True,
        }
    if has_care:
        return {
            "route": "rag_answer",
            "primary_intent": "care_question",
            "confidence": 0.84,
            "need_rag": True,
        }
    if _has_any(text, LOGISTICS_WORDS):
        return {
            "route": "template_reply",
            "primary_intent": "ask_logistics",
            "confidence": 0.8,
            "need_template": True,
        }
    if _has_any(text, AFTER_SALE_WORDS):
        return {
            "route": "template_reply",
            "primary_intent": "ask_after_sale",
            "confidence": 0.8,
            "need_template": True,
        }
    return {
        "route": "clarify",
        "primary_intent": "unknown",
        "confidence": 0.45,
        "reason": "mock_low_confidence",
    }


async def classify_intent(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
) -> IntentResult:
    del user_state, candidates
    from app.services import llm_service

    if llm_service.get_model_config("intent").provider == "mock":
        raw = _mock_classify(message.message.strip())
    else:
        raw = await llm_service.classify_intent(_build_prompt(message.message))

    try:
        return IntentResult.model_validate(raw)
    except ValidationError as exc:
        raise AppError(ErrorCode.INTENT_SCHEMA_INVALID) from exc


def _build_prompt(message: str) -> str:
    return f"""你只做意图识别，只输出 JSON，不要生成用户回复。

可选 route: template_reply, rag_answer, template_then_rag, clarify, human, chitchat, unsupported。
可选 primary_intent: greeting, product_interest, ask_price, price_objection, discount_request,
hesitation, trust_issue, comparison, ask_logistics, ask_after_sale, knowledge_question,
care_question, order_intent, payment_intent, refund_request, complaint, human_request, unknown。

用户消息：{message}
"""
