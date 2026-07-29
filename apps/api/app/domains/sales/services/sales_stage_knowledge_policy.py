from collections.abc import Iterable

from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply_plan import ReplyPlan
from app.domains.sales.schemas.sales_flow import SalesKnowledgeSource, SalesStage
from app.domains.sales.services.sales_stage_catalog import get_sales_stage_definition
from app.domains.sales.services.sales_stage_service import normalize_sales_stage


CARE_KNOWLEDGE_BASE_IDS = {
    "kb_orchid_basic",
    "kb_orchid_advanced",
    "kb_best_practices",
}
PRODUCT_INTENTS = {
    "product_query",
    "product_recommendation",
    "recommend_product",
    "comparison",
    "order_intent",
}
SKU_INTENTS = {
    "ask_price",
    "price_query",
    "ask_inventory",
    "stock_query",
}
PROMOTION_INTENTS = {
    "ask_activity",
    "ask_promotion",
    "discount_request",
}
ORDER_FACT_INTENTS = {
    "order_query",
    "ask_logistics",
    "order_intent",
    "payment_intent",
    "order_completed",
    "payment_success",
}
SERVICE_INTENTS = {"ask_after_sale", "refund_request", "complaint"}


def allowed_knowledge_sources(
    stage: str | SalesStage,
    intent: IntentResult,
) -> frozenset[str]:
    """Resolve the stage allowlist plus explicit, narrowly scoped exceptions."""

    definition = get_sales_stage_definition(normalize_sales_stage(stage))
    allowed = {
        source.value
        for source in (definition.allowed_knowledge_sources if definition else ())
    }
    primary = intent.primary_intent
    all_intents = {primary, *intent.secondary_intents}
    product_recommendation = bool(
        intent.slots.get("conversation_topic") == "product_recommendation"
        or (
            intent.primary_domain == "product"
            and intent.primary_goal == "seek_help"
            and "product_selection" in intent.issues
        )
    )

    if all_intents & PRODUCT_INTENTS or product_recommendation:
        allowed.add(SalesKnowledgeSource.PRODUCT_CATALOG.value)
    if primary == "order_intent":
        allowed.add(SalesKnowledgeSource.SKU_FACTS.value)
    if all_intents & SKU_INTENTS and definition and (
        SalesKnowledgeSource.SKU_FACTS in definition.allowed_knowledge_sources
        or SalesKnowledgeSource.SKU_FACTS in definition.conditional_knowledge_sources
    ):
        allowed.add(SalesKnowledgeSource.SKU_FACTS.value)
    if all_intents & PROMOTION_INTENTS and definition and (
        SalesKnowledgeSource.PROMOTION in definition.conditional_knowledge_sources
    ):
        allowed.add(SalesKnowledgeSource.PROMOTION.value)
    if all_intents & ORDER_FACT_INTENTS:
        if definition and (
            SalesKnowledgeSource.ORDER_FACTS in definition.allowed_knowledge_sources
            or SalesKnowledgeSource.ORDER_FACTS in definition.conditional_knowledge_sources
        ):
            allowed.add(SalesKnowledgeSource.ORDER_FACTS.value)
        elif primary in {"order_query", "ask_logistics"}:
            # A customer-requested order service query is independent of sales progression.
            allowed.add(SalesKnowledgeSource.ORDER_FACTS.value)
    if all_intents & SERVICE_INTENTS:
        allowed.update(
            {
                SalesKnowledgeSource.SERVICE_SOP.value,
                SalesKnowledgeSource.ORDER_FACTS.value,
            }
        )
    return frozenset(allowed)


def apply_stage_knowledge_policy(
    plan: ReplyPlan,
    *,
    stage: str | SalesStage,
    intent: IntentResult,
) -> ReplyPlan:
    sources = allowed_knowledge_sources(stage, intent)
    retrieval_policy = {
        **plan.retrieval_policy,
        "sales_stage": normalize_sales_stage(stage),
        "allowed_source_groups": sorted(sources),
    }
    if (
        retrieval_policy.get("mode") == "product_recommendation"
        and SalesKnowledgeSource.PRODUCT_CATALOG.value not in sources
    ):
        retrieval_policy.pop("mode", None)

    knowledge_base_ids = _safe_care_kb_ids(plan.knowledge_base_ids)
    if (
        plan.action == "rag_answer"
        and retrieval_policy.get("mode") != "product_recommendation"
    ):
        knowledge_base_ids = knowledge_base_ids or ["kb_orchid_basic"]
    return plan.model_copy(
        update={
            "knowledge_base_ids": knowledge_base_ids,
            "retrieval_policy": retrieval_policy,
        }
    )


def _safe_care_kb_ids(values: Iterable[str]) -> list[str]:
    return [value for value in values if value in CARE_KNOWLEDGE_BASE_IDS]
