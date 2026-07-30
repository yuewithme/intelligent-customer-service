from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply_plan import ReplyPlan
from app.domains.sales.services.sales_stage_knowledge_policy import (
    allowed_knowledge_sources,
    apply_stage_knowledge_policy,
)


def _intent(primary_intent: str, *, secondary: list[str] | None = None) -> IntentResult:
    return IntentResult(
        route="rag_answer",
        primary_intent=primary_intent,
        secondary_intents=secondary or [],
        confidence=0.9,
    )


def test_all_sales_stages_allow_customer_context_care_and_only_stage_scripts():
    for stage in (
        "rapport",
        "need_discovery",
        "pain_discovery",
        "solution_recommended",
        "value_built",
        "trial_close",
        "closing",
    ):
        sources = allowed_knowledge_sources(stage, _intent("greeting"))
        assert {"customer_context", "care_safe", "stage_script"} <= sources


def test_early_stage_explicit_product_query_gets_catalog_but_not_sales_value_or_sku():
    sources = allowed_knowledge_sources("rapport", _intent("product_query"))

    assert "product_catalog" in sources
    assert "product_value" not in sources
    assert "sku_facts" not in sources
    assert "promotion" not in sources
    assert "order_facts" not in sources


def test_product_selection_semantics_keep_catalog_recommendation_mode():
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={"conversation_topic": "product_recommendation"},
        confidence=0.9,
    )
    plan = ReplyPlan(
        action="rag_answer",
        reason="knowledge_intent",
        retrieval_policy={"mode": "product_recommendation"},
    )

    result = apply_stage_knowledge_policy(
        plan,
        stage="need_discovery",
        intent=intent,
    )

    assert "product_catalog" in allowed_knowledge_sources("need_discovery", intent)
    assert result.retrieval_policy["mode"] == "product_recommendation"
    assert "product_catalog" in result.retrieval_policy["allowed_source_groups"]


def test_early_price_question_does_not_release_live_sku_facts():
    sources = allowed_knowledge_sources("need_discovery", _intent("ask_price"))

    assert "sku_facts" not in sources


def test_order_intent_always_allows_product_card_facts():
    sources = allowed_knowledge_sources("need_discovery", _intent("order_intent"))

    assert "product_catalog" in sources
    assert "sku_facts" in sources


def test_trial_and_closing_sources_are_progressively_released():
    trial = allowed_knowledge_sources("trial_close", _intent("ask_activity"))
    closing = allowed_knowledge_sources("closing", _intent("payment_intent"))

    assert {"product_catalog", "product_value", "sku_facts", "promotion"} <= trial
    assert "order_facts" not in trial
    assert {
        "product_catalog",
        "product_value",
        "sku_facts",
        "promotion",
        "order_facts",
    } <= closing


def test_customer_order_service_query_can_read_order_facts_without_advancing_sales():
    sources = allowed_knowledge_sources("need_discovery", _intent("order_query"))

    assert "order_facts" in sources


def test_business_action_sources_are_exclusive_and_ignore_sales_stage():
    product_sources = allowed_knowledge_sources(
        "rapport",
        _intent("knowledge_question"),
        business_action="catalog_search",
    )
    care_sources = allowed_knowledge_sources(
        "closing",
        _intent("care_question"),
        business_action="care_answer",
    )
    order_sources = allowed_knowledge_sources(
        "need_discovery",
        _intent("order_intent"),
        business_action="order_verify",
    )

    assert product_sources == {"product_catalog", "product_value", "sku_facts"}
    assert care_sources == {"customer_context", "care_safe"}
    assert order_sources == {"order_facts", "service_sop"}


def test_care_action_keeps_safe_conversation_context_without_product_sources():
    plan = ReplyPlan(
        action="rag_answer",
        reason="care",
        context_policy={"recent_turns": 4, "include_profile_summary": True},
        retrieval_policy={"mode": "product_recommendation"},
    )

    result = apply_stage_knowledge_policy(
        plan,
        stage="closing",
        intent=_intent("care_question"),
        business_action="care_answer",
    )

    assert result.retrieval_policy["allowed_source_groups"] == [
        "care_safe",
        "customer_context",
    ]
    assert "mode" not in result.retrieval_policy
    assert result.context_policy["recent_turns"] == 4
    assert result.context_policy["include_profile_summary"] is True
    assert result.context_policy["profile_fields"] == ["ai_summary", "pain_points"]
    assert result.context_policy["include_session_state"] is True
    assert result.context_policy["include_memory_context"] is False


def test_reply_plan_rejects_non_care_kbs_and_records_stage_allowlist():
    plan = ReplyPlan(
        action="rag_answer",
        reason="knowledge_intent",
        knowledge_base_ids=["kb_orchid_basic", "kb_private_sales"],
    )

    result = apply_stage_knowledge_policy(
        plan,
        stage="pain_discovery",
        intent=_intent("care_question"),
    )

    assert result.knowledge_base_ids == ["kb_orchid_basic"]
    assert result.retrieval_policy["sales_stage"] == "pain_discovery"
    assert "care_safe" in result.retrieval_policy["allowed_source_groups"]
