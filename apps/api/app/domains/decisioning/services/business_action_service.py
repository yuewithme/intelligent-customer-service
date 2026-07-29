import re

from app.domains.sales.schemas.sales_flow import SalesKnowledgeSource


CATALOG_SEARCH = "catalog_search"
CARE_ANSWER = "care_answer"
SELECTED_PRODUCT_DETAIL = "selected_product_detail"
ORDER_VERIFY = "order_verify"
CONVERSATION = "conversation"

_ORDER_CONFIRMED_PATTERN = re.compile(
    r"(?:已经|已|刚刚|刚才)?(?:付款|支付|下单)(?:成功|完成|过|了)"
    r"|(?:钱|款)(?:已经|已)?(?:付|支付)(?:了|过)?"
    r"|(?:订单|物流|快递|发货)(?:状态|信息|进度|到哪|怎么|哪里|查询|查一下)"
)
_SELECTED_PRODUCT_DETAIL_PATTERN = re.compile(
    r"(?:几|多少)\s*苗|多苗|规格|套餐|带盆|含盆|裸根|原盆|盆栽|"
    r"种好|栽好|价格|多少钱|库存|现货"
)
_BUDGET_PATTERN = re.compile(
    r"预算|性价比|便宜|实惠|划算|"
    r"\d+(?:\.\d+)?\s*元?\s*(?:以内|以下|之内|不超过|最多|左右|上下)|"
    r"[一二三四五六七八九十百两]{1,8}\s*(?:元|块)?"
    r"(?:以内|以下|之内|不超过|最多|左右|上下)"
)
_CARE_INTENTS = {
    "care_question",
    "orchid_care",
    "knowledge_question",
    "ask_care",
}
_CATALOG_INTENTS = {
    "product_query",
    "product_recommendation",
    "recommend_product",
    "order_intent",
}
_ORDER_INTENTS = {
    "order_query",
    "ask_logistics",
}


def resolve_business_action(*, message, intent, user_state) -> str:
    """Resolve one authoritative business action for the current turn."""

    text = str(getattr(message, "message", "") or "").strip()
    slots = getattr(intent, "slots", {})
    slots = slots if isinstance(slots, dict) else {}
    primary_intent = str(getattr(intent, "primary_intent", "") or "")
    primary_domain = str(getattr(intent, "primary_domain", "") or "")
    primary_goal = str(getattr(intent, "primary_goal", "") or "")
    issues = set(getattr(intent, "issues", []) or [])
    metadata = getattr(user_state, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}

    if (
        primary_intent in _ORDER_INTENTS
        or _ORDER_CONFIRMED_PATTERN.search(text)
        or (
            slots.get("conversation_topic") == "order_information"
            and any(marker in text for marker in ("订单", "付款", "支付", "物流", "快递"))
        )
    ):
        return ORDER_VERIFY

    if (
        primary_intent in {"price_objection", "discount_request", "hesitation"}
        and not re.search(r"\d+(?:\.\d+)?\s*元", text)
    ):
        return CONVERSATION

    selected_product_id = str(metadata.get("commerce_last_product_id") or "").strip()
    if slots.get("conversation_topic") == "order_information":
        return CONVERSATION
    if primary_intent == "payment_intent" and selected_product_id:
        return CATALOG_SEARCH
    if (
        selected_product_id
        and primary_intent not in {"price_objection", "discount_request", "hesitation"}
        and _SELECTED_PRODUCT_DETAIL_PATTERN.search(text)
    ):
        return SELECTED_PRODUCT_DETAIL

    if _is_budget_followup(text, metadata):
        return CATALOG_SEARCH

    if (
        primary_intent in _CATALOG_INTENTS
        or slots.get("conversation_topic") == "product_recommendation"
        or (
            primary_domain == "product"
            and primary_goal == "seek_help"
            and "product_selection" in issues
        )
    ):
        return CATALOG_SEARCH

    if (
        primary_intent in _CARE_INTENTS
        or primary_domain == "care"
        or bool(getattr(intent, "need_rag", False))
    ):
        return CARE_ANSWER

    return CONVERSATION


def knowledge_sources_for_action(action: str) -> frozenset[str] | None:
    """Return an exclusive source allowlist for actions with factual side effects."""

    if action == CATALOG_SEARCH:
        return frozenset(
            {
                SalesKnowledgeSource.PRODUCT_CATALOG.value,
                SalesKnowledgeSource.PRODUCT_VALUE.value,
                SalesKnowledgeSource.SKU_FACTS.value,
            }
        )
    if action == SELECTED_PRODUCT_DETAIL:
        return frozenset(
            {
                SalesKnowledgeSource.PRODUCT_CATALOG.value,
                SalesKnowledgeSource.SKU_FACTS.value,
            }
        )
    if action == ORDER_VERIFY:
        return frozenset(
            {
                SalesKnowledgeSource.ORDER_FACTS.value,
                SalesKnowledgeSource.SERVICE_SOP.value,
            }
        )
    if action == CARE_ANSWER:
        return frozenset(
            {
                SalesKnowledgeSource.CUSTOMER_CONTEXT.value,
                SalesKnowledgeSource.CARE_SAFE.value,
            }
        )
    return None


def _is_budget_followup(text: str, metadata: dict) -> bool:
    if not _BUDGET_PATTERN.search(text):
        return False
    sales_action = metadata.get("sales_action")
    sales_action = sales_action if isinstance(sales_action, dict) else {}
    opportunity = metadata.get("active_opportunity")
    opportunity = opportunity if isinstance(opportunity, dict) else {}
    asked_slots = opportunity.get("asked_slots")
    asked_slots = asked_slots if isinstance(asked_slots, list) else []
    return (
        sales_action.get("question_slot") == "budget"
        or "budget" in asked_slots
        or bool(metadata.get("commerce_last_catalog_query"))
        or "预算" in text
    )
