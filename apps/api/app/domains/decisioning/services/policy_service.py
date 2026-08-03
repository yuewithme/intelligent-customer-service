from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.customers.schemas.state import UserState
from app.core.config import get_settings


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
HUMAN_GOALS = {"request_refund_return", "complain", "request_human"}
KNOWLEDGE_INTENTS = {"knowledge_question", "care_question", "process_question", "usage_question"}
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
    "purchase_rejection",
}
PRODUCT_RECOMMENDATION_MARKERS = (
    "推荐",
    "哪款",
    "哪种",
    "想找",
    "想要",
    "性价比",
)


async def decide_route(
    intent: IntentResult,
    user_state: UserState,
    message: NormalizedMessage,
) -> PolicyDecision:
    del user_state
    if intent.route not in VALID_ROUTES:
        return PolicyDecision(
            route="chitchat",
            reason="invalid_route_continue_automatically",
            fallback_route=intent.route or "clarify",
            original_route=intent.route or "clarify",
        )
    if (
        intent.primary_intent in HUMAN_INTENTS
        or intent.primary_goal in HUMAN_GOALS
    ):
        return _handoff_decision("human_required", intent.route)
    if intent.route == "clarify":
        return PolicyDecision(
            route="chitchat",
            reason="ambiguous_continue_automatically",
            fallback_route="clarify",
            original_route="clarify",
        )
    if intent.route == "unsupported":
        return PolicyDecision(
            route="chitchat",
            reason="unsupported_continue_automatically",
            fallback_route="unsupported",
            original_route="unsupported",
        )
    if intent.route == "human" or intent.need_human:
        return PolicyDecision(
            route="template_reply",
            reason="unverified_handoff_blocked",
            fallback_route=intent.route,
            original_route=intent.route,
        )
    if intent.confidence < get_settings().intent_confidence_threshold:
        if intent.primary_intent in KNOWLEDGE_INTENTS or intent.route in {
            "rag_answer",
            "template_then_rag",
            "clarify",
        }:
            return PolicyDecision(
                route="rag_answer",
                reason="low_confidence_knowledge_lookup",
                fallback_route=intent.route or "clarify",
                original_route=intent.route or "clarify",
            )
        return PolicyDecision(
            route=intent.route if intent.route in VALID_ROUTES else "rag_answer",
            reason="low_confidence_intent_route",
            original_route=intent.route,
        )
    if (
        intent.primary_intent in {"price_objection", "hesitation"}
        and {"care_concern", "knowledge_question", "care_question"}
        & set(intent.secondary_intents)
    ):
        return PolicyDecision(route="template_then_rag", reason="mixed_sales_knowledge")
    if (
        intent.slots.get("conversation_topic") == "product_recommendation"
        or (
            intent.primary_domain == "product"
            and intent.primary_goal == "seek_help"
            and "product_selection" in intent.issues
        )
        or (
            intent.primary_intent in KNOWLEDGE_INTENTS
            and any(marker in message.message for marker in PRODUCT_RECOMMENDATION_MARKERS)
        )
    ):
        return PolicyDecision(
            route="template_reply",
            reason="local_product_catalog_query",
        )
    if intent.primary_intent in KNOWLEDGE_INTENTS:
        return PolicyDecision(
            route="rag_answer",
            reason="knowledge_intent",
        )
    if intent.primary_intent in TEMPLATE_INTENTS:
        return PolicyDecision(route="template_reply", reason="template_intent")
    return PolicyDecision(route=intent.route, reason="intent_route")


def _handoff_decision(reason: str, original_route: str | None) -> PolicyDecision:
    return PolicyDecision(
        route="human",
        reason=reason,
        fallback_route=original_route,
        original_route=original_route,
        next_action="human_handoff",
    )
