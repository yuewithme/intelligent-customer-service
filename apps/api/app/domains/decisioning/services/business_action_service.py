import re

from app.domains.sales.schemas.sales_flow import SalesKnowledgeSource
from app.domains.sales.services.sales_stage_service import normalize_sales_stage


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
    r"(?:几|多少)\s*苗|多苗|规格|套餐|带盆|含盆|花盆|裸根|原盆|盆栽|"
    r"种好|栽好|价格|多少钱|库存|现货"
)
_CATALOG_PURCHASE_PATTERN = re.compile(
    r"橱窗|商品卡片|购买卡片|购买链接|下单链接|发链接|"
    r"(?:这个|这款|这种|推荐的|刚才说的).{0,8}(?:能买吗|怎么买|哪里买|在哪买)"
)
_ORDER_LOOKUP_CONTINUATION_PATTERN = re.compile(
    r"手机号|订单号|刚才买|刚买|刚下|查订单|查询订单|按手机号查"
)
_ORDER_STATUS_FOLLOWUP_PATTERN = re.compile(
    r"查到了|查到没|有没有查|订单|物流|快递|发货|我的货|货发了|到哪里|到哪了"
)
_BUDGET_PATTERN = re.compile(
    r"预算|性价比|便宜|实惠|划算|"
    r"\d+(?:\.\d+)?\s*元?\s*(?:以内|以下|之内|不超过|最多|左右|上下)|"
    r"[一二三四五六七八九十百两]{1,8}\s*(?:元|块)?"
    r"(?:以内|以下|之内|不超过|最多|左右|上下)"
)
_PURCHASE_REJECTION_PATTERN = re.compile(
    r"不要再推荐|不要再给我推荐|别再推荐|别再给我推荐|不用推荐|"
    r"不想买|先不买|暂时不买|暂时不考虑|先不考虑|不考虑了|"
    r"不买了|算了不买|别发链接|不用发链接"
)
_EXPLICIT_SALES_OBJECTION_PATTERN = re.compile(
    r"太贵|有点贵|好贵|贵了|价格贵|不便宜|"
    r"再考虑一下|考虑考虑|再想想|暂时不买|先不买"
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


def resolve_business_action(
    *, message, intent, user_state, sales_stage: str | None = None
) -> str:
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
    recommendation_stage_reached = _recommendation_stage_reached(
        sales_stage,
        user_state,
    )
    active_task = metadata.get("active_task")
    active_task = active_task if isinstance(active_task, dict) else {}
    if (
        primary_intent in {"purchase_rejection", "not_interested"}
        or _PURCHASE_REJECTION_PATTERN.search(text)
    ):
        return CONVERSATION
    if (
        active_task.get("domain") == "order"
        and (
            active_task.get("status")
            in {"awaiting_identity", "verified_requires_human", "requires_human"}
            or (
                active_task.get("status")
                in {"querying", "query_failed", "completed", "awaiting_order_evidence"}
                and _ORDER_STATUS_FOLLOWUP_PATTERN.search(text)
            )
        )
    ):
        return ORDER_VERIFY

    shipping_contact = slots.get("shipping_contact")
    has_shipping_mobile = (
        isinstance(shipping_contact, dict)
        and bool(str(shipping_contact.get("mobile") or "").strip())
    )
    if (
        metadata.get("commerce_pending") == "order_mobile"
        and (
            has_shipping_mobile
            or re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)
            or _ORDER_LOOKUP_CONTINUATION_PATTERN.search(text)
        )
    ):
        return ORDER_VERIFY

    if (
        primary_intent in _ORDER_INTENTS
        or _ORDER_CONFIRMED_PATTERN.search(text)
        or (
            slots.get("conversation_topic") == "order_information"
            and any(marker in text for marker in ("订单", "付款", "支付", "物流", "快递"))
        )
    ):
        return ORDER_VERIFY

    selected_product_id = str(metadata.get("commerce_last_product_id") or "").strip()
    selected_product_kind = str(
        slots.get("product_request_kind")
        or metadata.get("commerce_last_product_kind")
        or ""
    ).strip()
    selected_membership = selected_product_kind == "membership" and bool(
        selected_product_id or slots.get("product_request_kind") == "membership"
    )

    if (
        primary_intent in {"price_objection", "discount_request", "hesitation"}
        and _EXPLICIT_SALES_OBJECTION_PATTERN.search(text)
        and not re.search(r"\d+(?:\.\d+)?\s*元", text)
        and not selected_membership
    ):
        return CONVERSATION

    if slots.get("product_request_kind") == "membership":
        return CATALOG_SEARCH
    if slots.get("conversation_topic") == "order_information":
        return CONVERSATION
    if primary_intent == "payment_intent" and selected_product_id:
        return CATALOG_SEARCH
    if (
        selected_product_id
        and selected_product_kind == "membership"
        and (
            slots.get("product_request_kind") == "membership"
            or primary_intent in {
                *_CATALOG_INTENTS,
                "payment_intent",
                "ask_price",
                "price_objection",
                "discount_request",
                "hesitation",
            }
            or _CATALOG_PURCHASE_PATTERN.search(text)
            or _SELECTED_PRODUCT_DETAIL_PATTERN.search(text)
        )
    ):
        return CATALOG_SEARCH
    if (
        selected_product_id
        and primary_intent not in {"price_objection", "discount_request", "hesitation"}
        and _SELECTED_PRODUCT_DETAIL_PATTERN.search(text)
    ):
        return SELECTED_PRODUCT_DETAIL

    if _CATALOG_PURCHASE_PATTERN.search(text):
        return CATALOG_SEARCH

    if _is_budget_followup(text, metadata):
        return CATALOG_SEARCH if recommendation_stage_reached else CONVERSATION

    if (
        primary_intent in _CATALOG_INTENTS
        or slots.get("conversation_topic") == "product_recommendation"
        or (
            primary_domain == "product"
            and primary_goal == "seek_help"
            and "product_selection" in issues
        )
    ):
        if primary_intent == "order_intent" and primary_goal in {
            "confirm",
            "purchase",
            "transact",
        }:
            return CATALOG_SEARCH
        return CATALOG_SEARCH if recommendation_stage_reached else CONVERSATION

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


def _recommendation_stage_reached(sales_stage: str | None, user_state) -> bool:
    candidates = [sales_stage, getattr(user_state, "sales_stage", None)]
    metadata = getattr(user_state, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    opportunity = metadata.get("active_opportunity")
    if isinstance(opportunity, dict):
        candidates.extend(
            [opportunity.get("current_stage"), opportunity.get("sales_stage")]
        )
    profile = metadata.get("profile")
    if isinstance(profile, dict):
        candidates.append(profile.get("current_stage"))
    return any(
        normalize_sales_stage(candidate)
        in {
            "solution_recommended",
            "value_built",
            "trial_close",
            "closing",
        }
        for candidate in candidates
    )
