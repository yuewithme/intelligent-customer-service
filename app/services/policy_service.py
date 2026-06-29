from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.policy import PolicyDecision
from app.schemas.state import UserState


VALID_ROUTES = {
    "template_reply",
    "rag_answer",
    "template_then_rag",
    "clarify",
    "human",
    "chitchat",
    "unsupported",
}
HUMAN_INTENTS = {"complaint", "refund_request", "human_request"}
KNOWLEDGE_INTENTS = {"knowledge_question", "care_question"}
TEMPLATE_INTENTS = {
    "ask_price",
    "price_objection",
    "discount_request",
    "hesitation",
    "trust_issue",
    "comparison",
    "ask_logistics",
    "ask_after_sale",
    "order_intent",
    "payment_intent",
}


async def decide_route(
    intent: IntentResult,
    user_state: UserState,
    message: NormalizedMessage,
) -> PolicyDecision:
    del user_state, message
    if intent.route not in VALID_ROUTES:
        return PolicyDecision(
            route="clarify",
            reason="invalid_route",
            fallback_route="clarify",
        )
    if intent.need_human or intent.primary_intent in HUMAN_INTENTS:
        return PolicyDecision(route="human", reason="human_required")
    if intent.confidence < 0.6:
        return PolicyDecision(route="clarify", reason="low_confidence")
    if (
        intent.primary_intent in {"price_objection", "hesitation"}
        and {"care_concern", "knowledge_question", "care_question"}
        & set(intent.secondary_intents)
    ):
        return PolicyDecision(route="template_then_rag", reason="mixed_sales_knowledge")
    if intent.primary_intent in KNOWLEDGE_INTENTS:
        return PolicyDecision(route="rag_answer", reason="knowledge_intent")
    if intent.primary_intent in TEMPLATE_INTENTS:
        return PolicyDecision(route="template_reply", reason="template_intent")
    return PolicyDecision(route=intent.route, reason="intent_route")
