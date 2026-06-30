from pydantic import ValidationError

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


HUMAN_WORDS = ("人工", "转人工", "真人", "人工客服")
REFUND_WORDS = ("退款", "退货", "退钱", "退单")
COMPLAINT_WORDS = ("投诉", "举报", "骗子", "骗我", "不满意", "差评", "强烈不满")
PRICE_ASK_WORDS = ("价格", "多少钱", "报价", "优惠", "便宜")
PRICE_OBJECTION_WORDS = ("太贵", "有点贵", "好贵", "贵了", "价格贵", "不便宜")
HESITATION_WORDS = ("再考虑一下", "考虑一下", "考虑考虑", "再想想", "再看看")
CUSTOMER_SERVICE_REQUEST_WORDS = ("转客服", "找客服", "接客服", "人工客服", "客服介入", "客服处理")
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
    "注意什么",
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


def match_human_request(text: str) -> bool:
    return hit_any(text, HUMAN_WORDS) or hit_any(text, CUSTOMER_SERVICE_REQUEST_WORDS)


def match_price_intent(text: str) -> str | None:
    if hit_any(text, PRICE_OBJECTION_WORDS) or hit_any(text, HESITATION_WORDS):
        return "price_objection"
    if hit_any(text, PRICE_ASK_WORDS):
        return "ask_price"
    return None


def classify_by_hard_rules(text: str) -> IntentResult | None:
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
    if match_human_request(text):
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

    if hit_any(text, UNSUPPORTED_WORDS):
        return _validated_intent(
            {
                "route": "unsupported",
                "primary_intent": "unsupported",
                "confidence": 0.88,
                "reason": "rule_unsupported",
            }
        )
    return None


def classify_by_soft_rules(text: str) -> IntentResult | None:
    text = normalize_intent_text(text)
    if not text:
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    price_intent = match_price_intent(text)
    has_price = price_intent is not None
    has_care = hit_any(text, CARE_WORDS)
    has_knowledge = hit_any(text, KNOWLEDGE_PATTERNS) or "知识" in text or "资料" in text
    if has_price and has_care:
        return _validated_intent(
            {
                "route": "template_then_rag",
                "primary_intent": "price_objection",
                "secondary_intents": ["care_question"],
                "sales_stage": "objection_handling",
                "confidence": 0.78,
                "need_template": True,
                "need_rag": True,
                "reason": "soft_rule_mixed_price_care",
            }
        )
    if has_price:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": price_intent,
                "sales_stage": "objection_handling" if price_intent == "price_objection" else "interest",
                "confidence": 0.76,
                "need_template": True,
                "reason": "soft_rule_price",
            }
        )
    if hit_any(text, LOGISTICS_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_logistics",
                "sales_stage": "interest",
                "confidence": 0.74,
                "need_template": True,
                "reason": "soft_rule_logistics",
            }
        )
    if hit_any(text, ORDER_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "payment_intent" if hit_any(text, ("付款", "支付")) else "order_intent",
                "sales_stage": "order_intent",
                "confidence": 0.74,
                "need_template": True,
                "reason": "soft_rule_order",
            }
        )
    if has_care:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": "care_question",
                "sales_stage": "knowledge_consulting",
                "confidence": 0.75,
                "need_rag": True,
                "reason": "soft_rule_care",
            }
        )
    if has_knowledge:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": _knowledge_primary_intent(text),
                "sales_stage": "knowledge_consulting",
                "confidence": 0.72,
                "need_rag": True,
                "reason": "soft_rule_knowledge",
            }
        )
    if hit_any(text, AFTER_SALE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_after_sale",
                "sales_stage": "after_sale",
                "confidence": 0.72,
                "need_template": True,
                "reason": "soft_rule_after_sale",
            }
        )
    if hit_any(text, GREETING_WORDS):
        return _validated_intent(
            {
                "route": "chitchat",
                "primary_intent": "greeting",
                "sales_stage": "greeting",
                "confidence": 0.76,
                "reason": "soft_rule_greeting",
            }
        )
    return _validated_intent(
        {
            "route": "clarify",
            "primary_intent": "unknown",
            "confidence": 0.45,
            "reason": "soft_rule_no_match",
        }
    )


def classify_by_rules(text: str) -> IntentResult | None:
    return classify_by_hard_rules(text) or classify_by_soft_rules(text)


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
    hard_intent = classify_by_hard_rules(message.message)
    if hard_intent is not None:
        return hard_intent

    settings = get_settings()
    llm_enabled = bool(getattr(settings, "intent_llm_enabled", False))
    confidence_threshold = getattr(settings, "intent_confidence_threshold", 0.6)
    if llm_enabled:
        try:
            llm_intent = await classify_by_llm(message, user_state, candidates)
            if llm_intent.confidence >= confidence_threshold:
                return llm_intent
        except AppError:
            pass

    rule_intent = classify_by_soft_rules(message.message)
    if candidates and rule_intent.route == candidates[0].get("route"):
        return rule_intent.model_copy(
            update={"confidence": min(rule_intent.confidence + 0.05, 1.0)}
        )
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
    return f"""你只做兰花客服消息的意图识别，只输出一个 JSON 对象，不要生成客服回复，不要输出 Markdown。

必须输出完整字段：
{{
  "route": "template_reply | rag_answer | template_then_rag | clarify | human | chitchat | unsupported",
  "primary_intent": "greeting | ask_price | price_objection | discount_request | ask_logistics | ask_after_sale | order_intent | payment_intent | knowledge_question | care_question | process_question | usage_question | refund_request | complaint | human_request | unsupported | unknown",
  "secondary_intents": [],
  "sales_stage": "greeting | interest | objection_handling | order_intent | after_sale | knowledge_consulting | human_pending | unknown",
  "confidence": 0.0,
  "need_template": false,
  "need_rag": false,
  "need_human": false,
  "reason": "简短说明"
}}

分类边界：
- 养护、浇水、施肥、换盆、修根、植料、光照、通风、地区差异、病虫害、黄叶、烂根、不开花等兰花知识问题，route 用 "rag_answer"，primary_intent 用 "care_question" 或 "knowledge_question"，need_rag 为 true。
- “浇水需要多少天”“多久浇水”“浇多少水”是养护问题，不是价格问题。
- 只有明确问价格、多少钱、报价、优惠，才是 "ask_price"。
- 只有明确嫌贵、太贵、有点贵、再考虑一下，才是 "price_objection"。
- 只有明确要求转人工/找人工/退款/投诉，才用 "human"。
- 不确定时用 "clarify"，primary_intent 用 "unknown"，confidence 低于 0.6。

判定优先级：
1. 退款、投诉、明确转人工、人身攻击或高风险售后异常 -> human。
2. 明确价格、优惠、下单、物流、售后政策 -> template_reply。
3. 兰花养护知识、病虫害、浇水施肥、换盆修根、地区环境 -> rag_answer。
4. 同时包含成交犹豫和养护顾虑 -> template_then_rag。
5. 问候感谢 -> chitchat。
6. 仍不确定 -> clarify。

反例边界：
- “多少天”“多久”“多少水”不是价格。
- “名贵兰花”里的“贵”不是价格异议。
- “考虑换盆”不是成交犹豫。
- “客服指导养护”不是转人工。
- “售后怎么养护”如果是在咨询养护方法，不是投诉或退款。

confidence 规则：
- 0.90-1.00：用户表达直接命中单一意图，如“多少钱”“我要退款”。
- 0.75-0.89：语义明确但有少量上下文依赖，如“多久浇水”。
- 0.60-0.74：可能有两个意图，但主意图可判断。
- 低于 0.60：表达不完整、指代不明、需要追问。

示例：
用户消息：老师，下一次浇水需要多少天？
输出：{{"route":"rag_answer","primary_intent":"care_question","secondary_intents":[],"sales_stage":"knowledge_consulting","confidence":0.86,"need_template":false,"need_rag":true,"need_human":false,"reason":"询问兰花浇水频率"}}

用户消息：这个多少钱？
输出：{{"route":"template_reply","primary_intent":"ask_price","secondary_intents":[],"sales_stage":"interest","confidence":0.86,"need_template":true,"need_rag":false,"need_human":false,"reason":"明确询价"}}

用户消息：我再考虑一下，怕养不好
输出：{{"route":"template_then_rag","primary_intent":"price_objection","secondary_intents":["care_question"],"sales_stage":"objection_handling","confidence":0.82,"need_template":true,"need_rag":true,"need_human":false,"reason":"成交犹豫并伴随养护顾虑"}}

用户消息：{message}
输出："""
