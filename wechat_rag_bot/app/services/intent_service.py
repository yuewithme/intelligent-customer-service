import re

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.sales_flow import CustomerSignal
from app.schemas.state import UserState
from app.services.shipping_contact_service import extract_shipping_contact
from app.services.tag_catalog import normalize_system_value, system_tag_values


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
PURCHASE_REJECTION_WORDS = (
    "不要再推荐",
    "不要再给我推荐",
    "别再推荐",
    "别再给我推荐",
    "不用推荐",
    "不想买",
    "先不买",
)
SHIPPING_DAMAGE_WORDS = ("花盆碎", "盆碎", "苗歪", "运输破损", "收到后破损")
ORDER_INFO_WORDS = ("身份证号", "详细地址", "收货地址", "电话号码", "电话")
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
ORDER_QUERY_WORDS = (
    "查订单",
    "查询订单",
    "我的订单",
    "订单状态",
    "订单发货",
    "发货了吗",
    "快递到哪",
    "物流到哪",
)
PRODUCT_QUERY_WORDS = (
    "商品链接",
    "购买链接",
    "下单链接",
    "发我链接",
    "发一下链接",
    "有没有",
)
MOBILE_ONLY_PATTERN = re.compile(r"^1[3-9]\d{9}$")
PLANT_COUNT_PATTERN = re.compile(r"(?:养了|養了|养|養|有)?[^\d]{0,6}\d{1,5}\s*(?:盆|棵|株)")
ORCHID_VARIETY_WORDS = ("建兰", "春兰", "蕙兰", "墨兰", "寒兰", "春剑", "莲瓣兰")
PRODUCT_RECOMMENDATION_CONTEXT_WORDS = (
    "推荐",
    "品种",
    "哪款",
    "哪种",
    "性价比",
    "好养",
    "易活",
    "花香",
    "香味",
)
PRODUCT_PREFERENCE_WORDS = (
    "想找",
    "想要",
    "更看重",
    "好养",
    "养活",
    "易活",
    "新手",
    "性价比",
    "花香",
    "香味",
    "便宜",
)


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
                "sales_stage": "unknown",
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
                "sales_stage": "unknown",
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
                "sales_stage": "unknown",
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
    if hit_any(text, ORDER_QUERY_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_query",
                "sales_stage": "unknown",
                "confidence": 0.9,
                "need_template": True,
                "reason": "soft_rule_order_query",
            }
        )
    if hit_any(text, PRODUCT_QUERY_WORDS) and not has_care:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "product_query",
                "sales_stage": "closing",
                "confidence": 0.86,
                "need_template": True,
                "reason": "soft_rule_product_query",
            }
        )
    if hit_any(text, PURCHASE_REJECTION_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "purchase_rejection",
                "sales_stage": "closing",
                "confidence": 0.92,
                "need_template": True,
                "reason": "soft_rule_purchase_rejection",
            }
        )
    if hit_any(text, SHIPPING_DAMAGE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_after_sale",
                "sales_stage": "unknown",
                "confidence": 0.92,
                "need_template": True,
                "reason": "soft_rule_shipping_damage",
            }
        )
    if hit_any(text, ORDER_INFO_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.9,
                "need_template": True,
                "slots": {"conversation_topic": "order_information"},
                "reason": "soft_rule_order_information",
            }
        )
    if has_price and has_care:
        return _validated_intent(
            {
                "route": "template_then_rag",
                "primary_intent": "price_objection",
                "secondary_intents": ["care_question"],
                "sales_stage": "closing",
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
                "sales_stage": "closing" if price_intent == "price_objection" else "need_discovery",
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
                "sales_stage": "need_discovery",
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
                "sales_stage": "closing",
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
                "sales_stage": "pain_discovery",
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
                "sales_stage": "pain_discovery",
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
                "sales_stage": "unknown",
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
                "sales_stage": "rapport",
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
    del candidates
    from app.services import llm_service

    raw = await llm_service.classify_intent(
        _build_prompt(
            message.message,
            recent_turns=user_state.metadata.get("recent_turns", []),
        )
    )
    return _validated_intent(raw)


async def classify_intent(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
) -> IntentResult:
    hard_intent = classify_by_hard_rules(message.message)
    if hard_intent is not None:
        return _with_decision_blocker(hard_intent, message.message)

    normalized = normalize_intent_text(message.message)
    if (
        user_state.metadata.get("commerce_pending") == "order_mobile"
        and MOBILE_ONLY_PATTERN.fullmatch(normalized)
    ):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_query",
                "sales_stage": "unknown",
                "confidence": 0.99,
                "need_template": True,
                "reason": "pending_order_mobile",
            }
        )

    shipping_contact = extract_shipping_contact(message.message)
    if shipping_contact:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.98,
                "need_template": True,
                "slots": {"shipping_contact": shipping_contact},
                "reason": "structured_shipping_contact",
            }
        )

    opening_followup = classify_opening_followup(
        message.message,
        user_state.metadata.get("recent_turns", []),
    )
    if opening_followup is not None:
        return opening_followup

    recommendation_followup = classify_product_recommendation_followup(
        message.message,
        user_state.metadata.get("recent_turns", []),
    )
    if recommendation_followup is not None:
        return recommendation_followup

    settings = get_settings()
    llm_enabled = bool(getattr(settings, "intent_llm_enabled", False))
    confidence_threshold = getattr(settings, "intent_confidence_threshold", 0.6)
    rule_intent = classify_by_soft_rules(message.message)
    if (
        rule_intent.route != "clarify"
        and rule_intent.confidence >= confidence_threshold
    ):
        if candidates and rule_intent.route == candidates[0].get("route"):
            rule_intent = rule_intent.model_copy(
                update={"confidence": min(rule_intent.confidence + 0.05, 1.0)}
            )
        return _with_decision_blocker(rule_intent, message.message)
    if llm_enabled:
        try:
            llm_intent = await classify_by_llm(message, user_state, candidates)
            if llm_intent.confidence >= confidence_threshold:
                return _with_decision_blocker(llm_intent, message.message)
        except AppError:
            pass

    if candidates and rule_intent.route == candidates[0].get("route"):
        rule_intent = rule_intent.model_copy(
            update={"confidence": min(rule_intent.confidence + 0.05, 1.0)}
        )
    return _with_decision_blocker(rule_intent, message.message)


def _with_decision_blocker(intent: IntentResult, text: str) -> IntentResult:
    normalized = normalize_intent_text(text)
    blocker = None
    if (
        intent.primary_intent in {"price_objection", "discount_request"}
        or match_price_intent(normalized) == "price_objection"
    ):
        blocker = {"type": "price", "detail": "客户认为价格偏高"}
    elif hit_any(normalized, ("一直问", "反复问", "别再问", "直接看商品", "直接告诉")):
        blocker = {
            "type": "other",
            "detail": "客户不愿继续回答重复问题，希望直接查看商品",
        }
    else:
        candidate = intent.slots.get("decision_blocker")
        if (
            isinstance(candidate, dict)
            and candidate.get("type")
            in {
                "price",
                "trust",
                "care_risk",
                "product_fit",
                "choice",
                "timing",
                "other",
            }
        ):
            blocker = {
                "type": candidate["type"],
                "detail": str(candidate.get("detail") or "").strip(),
            }
    if blocker is None:
        return intent
    return intent.model_copy(
        update={"slots": {**intent.slots, "decision_blocker": blocker}}
    )


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
        intent = IntentResult.model_validate(raw)
    except ValidationError as exc:
        raise AppError(ErrorCode.INTENT_SCHEMA_INVALID) from exc
    primary_intent = normalize_system_value(
        "intent", intent.primary_intent, fallback="unknown"
    )
    secondary_intents = []
    for value in intent.secondary_intents:
        normalized = normalize_system_value("intent", value, fallback="")
        if normalized and normalized != primary_intent and normalized not in secondary_intents:
            secondary_intents.append(normalized)
    sentiment = (
        normalize_system_value(
            "customer_sentiment", intent.customer_sentiment, fallback="neutral"
        )
        if intent.customer_sentiment
        else None
    )
    sales_signals = []
    for value in intent.sales_signals:
        try:
            signal = CustomerSignal(value)
        except ValueError:
            continue
        if signal is not CustomerSignal.PURCHASED and signal.value not in sales_signals:
            sales_signals.append(signal.value)
    return intent.model_copy(
        update={
            "primary_intent": primary_intent,
            "secondary_intents": secondary_intents,
            "sales_stage": normalize_system_value(
                "sales_stage", intent.sales_stage, fallback="unknown"
            ),
            "sales_signals": sales_signals,
            "customer_sentiment": sentiment,
        }
    )


def classify_opening_followup(
    text: str, recent_turns: list[dict] | None
) -> IntentResult | None:
    recent_turns = recent_turns if isinstance(recent_turns, list) else []
    asked_for_profile = any(
        isinstance(turn, dict)
        and turn.get("role") == "assistant"
        and "多少盆" in str(turn.get("content") or "")
        and "品种" in str(turn.get("content") or "")
        for turn in recent_turns[-6:]
    )
    if not asked_for_profile:
        return None
    normalized = normalize_intent_text(text)
    if not (
        PLANT_COUNT_PATTERN.search(normalized)
        or hit_any(normalized, ORCHID_VARIETY_WORDS)
    ):
        return None
    return IntentResult(
        route="chitchat",
        primary_intent="profile_answer",
        sales_stage="need_discovery",
        confidence=0.98,
        reason="opening_profile_answer",
    )


def classify_product_recommendation_followup(
    text: str, recent_turns: list[dict] | None
) -> IntentResult | None:
    recent_turns = recent_turns if isinstance(recent_turns, list) else []
    normalized = normalize_intent_text(text)
    if not hit_any(normalized, PRODUCT_PREFERENCE_WORDS):
        return None
    has_recommendation_context = any(
        isinstance(turn, dict)
        and turn.get("role") == "assistant"
        and hit_any(
            normalize_intent_text(str(turn.get("content") or "")),
            PRODUCT_RECOMMENDATION_CONTEXT_WORDS,
        )
        for turn in recent_turns[-4:]
    )
    if not has_recommendation_context:
        return None
    return IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        sales_stage="need_discovery",
        confidence=0.9,
        need_rag=True,
        slots={"conversation_topic": "product_recommendation"},
        reason="contextual_product_preference",
    )


def _build_prompt(message: str, recent_turns: list[dict] | None = None) -> str:
    intent_values = " | ".join(system_tag_values("intent")) or "unknown"
    stage_values = " | ".join(system_tag_values("sales_stage")) or "unknown"
    signal_values = " | ".join(
        signal.value for signal in CustomerSignal if signal is not CustomerSignal.PURCHASED
    )
    sentiment_values = (
        " | ".join(system_tag_values("customer_sentiment")) or "neutral"
    )
    recent_lines = []
    for turn in (recent_turns or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            recent_lines.append(f"{role}: {content[:500]}")
    recent_context = (
        "\n## 最近对话\n\n" + "\n".join(recent_lines) + "\n"
        if recent_lines
        else ""
    )
    return f"""# 角色

你只负责做兰花私域客服消息的意图识别。

# 任务

读取【用户消息】，判断用户的真实意图，并只输出一个 JSON 对象。

不要生成客服回复。  
不要输出 Markdown。  
不要输出解释文字。  
不要输出代码块。  
不要在 JSON 前后添加任何内容。

## 用户消息

{message}
{recent_context}

# 必须输出的 JSON 字段

{{
  "route": "template_reply | rag_answer | template_then_rag | clarify | human | chitchat | unsupported",
  "primary_intent": "{intent_values}",
  "secondary_intents": [],
  "sales_signals": [],
  "slots": {{}},
  "sales_stage": "{stage_values}",
  "customer_sentiment": "{sentiment_values}",
  "confidence": 0.0,
  "need_template": false,
  "need_rag": false,
  "need_human": false,
  "reason": "简短说明"
}}

# 字段说明

1. `route`：后续处理路径。
2. `primary_intent`：用户最主要的意图，只能选择一个。
3. `secondary_intents`：用户同时表达的次要意图，没有则输出空数组。
4. `sales_signals`：只输出明确观察到的候选信号，可选值为 `{signal_values}`。客户口述付款只能输出 `payment_claimed`，禁止输出 `purchased`。
5. `sales_stage`：兼容字段，只输出候选阶段，最终阶段由后续状态机决定。
6. `customer_sentiment`：只能从标签管理中的客户情绪分类选择。
7. `confidence`：判断置信度，范围为 `0.00` 到 `1.00`。
8. `need_template`：是否需要调用固定话术模板。
9. `need_rag`：是否需要调用兰花知识资料回答。
10. `need_human`：是否需要转人工。
11. `reason`：用一句简短中文说明分类原因，不超过 20 个字。

`slots.decision_blocker` 格式为 {{"type": "price | trust | care_risk | product_fit | choice | timing | other", "detail": ""}}。
只记录客户明确表达的成交阻碍；没有明确阻碍时不要输出该槽位。
detail 使用中性中文概括，不复述辱骂或攻击性原话；售后问题本身不算成交阻碍。

# route 判定规则

## 1. template_reply

适用于明确的销售、交易、订单、物流、售后政策类问题。

包括：询价、优惠、议价、下单、付款、物流、发货、售后政策等。

字段要求：

- `need_template = true`
- `need_rag = false`
- `need_human = false`

## 2. rag_answer

适用于兰花知识和养护咨询。

包括：浇水、施肥、光照、通风、植料、换盆、修根、服盆、催花、不开花、黄叶、烂根、病虫害、地区养护差异等。

字段要求：

- `need_template = false`
- `need_rag = true`
- `need_human = false`

## 3. template_then_rag

适用于用户同时表达成交犹豫和养护顾虑。

例如：怕养不好、先看看、再考虑、担心不会养，同时又涉及购买决策。

字段要求：

- `need_template = true`
- `need_rag = true`
- `need_human = false`

## 4. clarify

适用于用户表达不完整、指代不明、无法判断真实意图。

字段要求：

- `primary_intent = unknown`
- `confidence < 0.60`

## 5. human

适用于必须人工处理的高风险或强诉求场景。

包括：明确要求人工、退款、投诉、赔付、补发、订单异常、严重售后纠纷、人身攻击、高风险售后异常等。

字段要求：

- `need_human = true`

## 6. chitchat

适用于问候、感谢、简单寒暄。

例如：你好、在吗、谢谢、好的、辛苦了。

## 7. unsupported

适用于与兰花、订单、客服服务无关，且无法通过普通寒暄处理的内容。

# primary_intent 判定规则

## greeting

问候、寒暄、感谢、确认收到。  
例如：你好、在吗、谢谢、好的。

## ask_price

明确询问价格、多少钱、报价、怎么卖。  
例如：这个多少钱、价格多少、怎么卖。

## price_objection

明确表达价格贵、预算犹豫或价格异议。  
例如：太贵了、有点贵、我再考虑一下。

注意：“名贵兰花”里的“贵”不是价格异议。

## discount_request

明确要求优惠、便宜点、打折、包邮、少一点。  
例如：能优惠吗、便宜点、可以包邮吗。

## ask_logistics

询问发货、快递、物流、到货时间、运费。  
例如：什么时候发货、发什么快递、几天到。

## ask_after_sale

询问售后政策、保障、养死是否处理、售后怎么负责。  
例如：养死包赔吗、有售后吗、收到坏了怎么办。

## order_intent

明确表达想买、下单、要一盆、怎么拍。  
例如：我要了、怎么下单、给我留一盆。

## payment_intent

询问付款方式、付款链接、转账、支付问题。  
例如：怎么付款、发我付款码、可以微信支付吗。

## care_question

具体兰花养护操作问题。

包括：浇水、施肥、换盆、修根、植料、光照、通风、温湿度、服盆、催花等。

## knowledge_question

兰花知识类问题，但不一定是具体操作。

包括：品种、花期、习性、香味、名贵程度、真假鉴别等。

## process_question

询问操作流程或处理步骤。  
例如：收到后怎么处理、上盆流程是什么。

## usage_question

询问某个养护用品、工具、药剂、植料的使用方式。  
例如：这个植料怎么用、杀菌剂怎么用。

涉及具体药剂搭配或剂量不确定时，可转 `human`。

## refund_request

明确要求退款、退货、退钱。

必须：

- `route = human`
- `need_human = true`

## complaint

明确投诉、强烈不满、责怪商家、要求赔付。

必须：

- `route = human`
- `need_human = true`

## human_request

明确要求人工、客服、老板、售后人员介入。

必须：

- `route = human`
- `need_human = true`

## unsupported

与兰花、销售、订单、售后无关的问题。

## unknown

信息不足，无法判断意图。

# sales_stage 判定规则

`sales_stage` 只描述首单七阶段。售后、退款、投诉和人工介入不是销售阶段，
此类消息输出 `sales_stage = unknown`，由后续服务记录中断并保留当前销售阶段。

## rapport

用户处于问候或寒暄阶段。

## need_discovery

正在判断客户是服务需求、产品需求还是复合需求。

## pain_discovery

客户已经表达核心困难、期望结果或购买动机。

## solution_recommended

已有足够依据，可以推荐产品或服务方案。

## value_built

客户正在了解或认可苗质、服务和适配价值。

## trial_close

客户开始确认价格、规格、数量或购买方案。

## closing

客户有明确下单意向或提出需要解决的成交阻碍。

## unknown

无法判断阶段。

# 分类优先级

按以下优先级从高到低判断：

1. 明确退款、投诉、赔付、补发、强烈售后纠纷、明确转人工、人身攻击、高风险异常  
   → `route = human`

2. 明确价格、优惠、下单、付款、物流、售后政策  
   → `route = template_reply`

3. 兰花养护知识、病虫害、浇水施肥、换盆修根、植料、光照通风、地区环境  
   → `route = rag_answer`

4. 同时包含成交犹豫和养护顾虑  
   → `route = template_then_rag`

5. 问候、感谢、简单寒暄  
   → `route = chitchat`

6. 与兰花及服务无关  
   → `route = unsupported`

7. 仍不确定  
   → `route = clarify`

# 重要边界

1. “浇水需要多少天”“多久浇水”“浇多少水”“多少天浇一次”属于养护问题，不是价格问题。
2. 只有明确问价格、多少钱、报价、怎么卖，才是 `ask_price`。
3. 只有明确说太贵、有点贵、再考虑、预算不够，才是 `price_objection`。
6. “客服指导养护”不是转人工，属于养护咨询。
7. “售后怎么养护”如果只是问养护方法。
8. 用户说“怕养不好”“不会养”，如果没有购买犹豫语境，优先归为 `care_question`；如果同时出现“再考虑”“不敢买”“先不买”，归为 `template_then_rag`。
9. 病虫害、烂根、严重黄叶等如果只是咨询养护，`route = rag_answer`；如果要求赔付、退换、投诉，`route = human`。
10. 用户消息同时包含多个意图时，`primary_intent` 选择最需要优先处理的意图，其余放入 `secondary_intents`。

# confidence 规则

## 0.90 - 1.00

用户表达直接命中单一意图。  
例如：“多少钱”“我要退款”“帮我转人工”。

## 0.75 - 0.89

语义明确，但可能需要少量上下文。  
例如：“多久浇水”“收到后怎么养”。

## 0.60 - 0.74

可能包含两个意图，但主意图基本可判断。  
例如：“这个贵吗，我怕养不好”。

## 0.00 - 0.59

表达不完整、指代不明、缺少关键信息，需要追问。

字段要求：

- `route = clarify`
- `primary_intent = unknown`

# 输出格式要求

1. 只能输出一个合法 JSON 对象。
2. JSON 必须包含所有字段。
3. 字段名必须与要求完全一致。
4. 字段值必须使用规定枚举值。
5. `secondary_intents` 必须是数组。
6. `confidence` 必须是数字，不要写成字符串。
7. `need_template`、`need_rag`、`need_human` 必须是布尔值。
8. `reason` 必须简短，不超过 20 个字。
9. 不要输出 Markdown、代码块、注释或额外解释。

# 示例

## 示例 1

用户消息：老师，下一次浇水需要多少天？

输出：

{{
  "route": "rag_answer",
  "primary_intent": "care_question",
  "secondary_intents": [],
  "sales_stage": "pain_discovery",
  "confidence": 0.86,
  "need_template": false,
  "need_rag": true,
  "need_human": false,
  "reason": "询问浇水频率"
}}

## 示例 2

用户消息：这个多少钱？

输出：

{{
  "route": "template_reply",
  "primary_intent": "ask_price",
  "secondary_intents": [],
  "sales_stage": "need_discovery",
  "confidence": 0.92,
  "need_template": true,
  "need_rag": false,
  "need_human": false,
  "reason": "明确询价"
}}

## 示例 3

用户消息：我再考虑一下，怕养不好

输出：

{{
  "route": "template_then_rag",
  "primary_intent": "price_objection",
  "secondary_intents": ["care_question"],
  "sales_stage": "closing",
  "confidence": 0.82,
  "need_template": true,
  "need_rag": true,
  "need_human": false,
  "reason": "犹豫且担心养护"
}}

## 示例 4

用户消息：我要退款

输出：

{{
  "route": "human",
  "primary_intent": "refund_request",
  "secondary_intents": [],
  "sales_stage": "unknown",
  "confidence": 0.95,
  "need_template": false,
  "need_rag": false,
  "need_human": true,
  "reason": "明确要求退款"
}}"""
