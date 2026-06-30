from pydantic import ValidationError

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


HUMAN_WORDS = ("人工", "转人工", "客服", "真人", "人工客服")
REFUND_WORDS = ("退款", "退货", "退钱", "退单")
COMPLAINT_WORDS = ("投诉", "举报", "骗子", "骗我", "不满意", "差评", "强烈不满")
PRICE_WORDS = ("价格", "多少钱", "多少", "报价", "优惠", "便宜", "贵", "太贵", "有点贵")
LOGISTICS_WORDS = ("物流", "发货", "快递", "多久到", "什么时候到", "运费")
ORDER_WORDS = ("怎么买", "下单", "付款", "支付", "购买", "拍下")
AFTER_SALE_WORDS = ("售后", "坏了", "破损", "质量问题")
KNOWLEDGE_PATTERNS = (
    "是什么",
    "怎么",
    "如何",
    "为什么",
    "有什么",
    "有哪些",
    "流程",
    "步骤",
    "方法",
    "注意事项",
    "区别",
    "适合",
    "能不能",
    "可以吗",
    "需要什么",
    "怎么使用",
    "怎么养",
    "怎么浇水",
    "怎么申请",
    "怎么处理",
    "说明",
    "资料",
    "材料",
)
CARE_WORDS = ("养护", "养不活", "不会养", "新手", "浇水", "施肥", "护理", "怕养死", "怕养不好")
GREETING_WORDS = ("你好", "您好", "在吗", "hello", "hi", "谢谢", "感谢")
UNSUPPORTED_WORDS = ("写代码", "彩票", "股票推荐", "医疗诊断", "法律意见", "无关业务")


def normalize_intent_text(text: str) -> str:
    return "".join((text or "").strip().lower().split())


def hit_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def classify_by_rules(text: str) -> IntentResult | None:
    text = normalize_intent_text(text)
    if not text:
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    if hit_any(text, REFUND_WORDS):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "refund_request",
                "sales_stage": "human_pending",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_refund",
            }
        )
    if hit_any(text, COMPLAINT_WORDS):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "complaint",
                "sales_stage": "human_pending",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_complaint",
            }
        )
    if hit_any(text, HUMAN_WORDS):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "human_request",
                "sales_stage": "human_pending",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_human_request",
            }
        )

    has_price = hit_any(text, PRICE_WORDS) or "考虑" in text
    has_care = hit_any(text, CARE_WORDS)
    has_knowledge = hit_any(text, KNOWLEDGE_PATTERNS) or "知识" in text or "资料" in text
    if has_price and has_care:
        return _validated_intent(
            {
                "route": "template_then_rag",
                "primary_intent": "price_objection",
                "secondary_intents": ["care_question"],
                "sales_stage": "objection_handling",
                "confidence": 0.9,
                "need_template": True,
                "need_rag": True,
                "reason": "rule_mixed_price_care",
            }
        )
    if has_price:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "price_objection" if "贵" in text else "ask_price",
                "sales_stage": "objection_handling" if "贵" in text else "interest",
                "confidence": 0.88,
                "need_template": True,
                "reason": "rule_price",
            }
        )
    if hit_any(text, LOGISTICS_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_logistics",
                "sales_stage": "interest",
                "confidence": 0.86,
                "need_template": True,
                "reason": "rule_logistics",
            }
        )
    if hit_any(text, ORDER_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "payment_intent" if hit_any(text, ("付款", "支付")) else "order_intent",
                "sales_stage": "order_intent",
                "confidence": 0.86,
                "need_template": True,
                "reason": "rule_order",
            }
        )
    if has_care:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": "care_question",
                "sales_stage": "knowledge_consulting",
                "confidence": 0.87,
                "need_rag": True,
                "reason": "rule_care",
            }
        )
    if has_knowledge:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": _knowledge_primary_intent(text),
                "sales_stage": "knowledge_consulting",
                "confidence": 0.84,
                "need_rag": True,
                "reason": "rule_knowledge",
            }
        )
    if hit_any(text, AFTER_SALE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_after_sale",
                "sales_stage": "after_sale",
                "confidence": 0.84,
                "need_template": True,
                "reason": "rule_after_sale",
            }
        )
    if hit_any(text, GREETING_WORDS):
        return _validated_intent(
            {
                "route": "chitchat",
                "primary_intent": "greeting",
                "sales_stage": "greeting",
                "confidence": 0.92,
                "reason": "rule_greeting",
            }
        )
    if hit_any(text, UNSUPPORTED_WORDS):
        return _validated_intent(
            {
                "route": "unsupported",
                "primary_intent": "unsupported",
                "confidence": 0.88,
                "reason": "rule_unsupported",
            }
        )
    return _validated_intent(
        {
            "route": "clarify",
            "primary_intent": "unknown",
            "confidence": 0.45,
            "reason": "rule_no_match",
        }
    )


async def classify_by_llm(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
) -> IntentResult:
    del user_state, candidates
    from app.services import llm_service

    raw = await llm_service.classify_intent(_build_prompt(message.message))
    return _validated_intent(raw)


async def classify_intent(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
) -> IntentResult:
    rule_intent = classify_by_rules(message.message)
    if candidates and rule_intent.route == candidates[0].get("route"):
        return rule_intent.model_copy(
            update={"confidence": min(rule_intent.confidence + 0.05, 1.0)}
        )

    settings = get_settings()
    llm_enabled = bool(getattr(settings, "intent_llm_enabled", False))
    fallback_threshold = getattr(settings, "intent_llm_fallback_threshold", 0.5)
    if llm_enabled and rule_intent.confidence < fallback_threshold:
        return await classify_by_llm(message, user_state, candidates)
    return rule_intent


def _knowledge_primary_intent(text: str) -> str:
    if hit_any(text, ("养护", "养不活", "不会养", "浇水", "施肥", "护理", "怕养死", "怕养不好")):
        return "care_question"
    if hit_any(text, ("流程", "步骤", "怎么申请", "怎么处理")):
        return "process_question"
    if hit_any(text, ("怎么使用", "如何使用", "使用方法")):
        return "usage_question"
    return "knowledge_question"


def _validated_intent(raw: dict) -> IntentResult:
    try:
        return IntentResult.model_validate(raw)
    except ValidationError as exc:
        raise AppError(ErrorCode.INTENT_SCHEMA_INVALID) from exc


def _build_prompt(message: str) -> str:
    return f"""你只做意图识别，只输出 JSON，不要生成用户回复。

可选 route: template_reply, rag_answer, template_then_rag, clarify, human, chitchat, unsupported。
可选 primary_intent: greeting, ask_price, price_objection, discount_request, ask_logistics,
ask_after_sale, order_intent, payment_intent, knowledge_question, care_question,
process_question, usage_question, refund_request, complaint, human_request, unsupported, unknown。

用户消息：{message}
"""
