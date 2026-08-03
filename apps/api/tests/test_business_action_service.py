from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.services.business_action_service import (
    CARE_ANSWER,
    CATALOG_SEARCH,
    CONVERSATION,
    ORDER_VERIFY,
    SELECTED_PRODUCT_DETAIL,
    resolve_business_action,
)


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="business-action-trace",
        channel="wechat",
        user_id="customer-1",
        session_id="session-1",
        message=text,
        kb_id="kb_default",
    )


def _intent(primary_intent: str, **updates) -> IntentResult:
    return IntentResult(
        route=updates.pop("route", "template_reply"),
        primary_intent=primary_intent,
        confidence=0.95,
        **updates,
    )


def test_classic_sales_dialogue_resolves_one_authoritative_action_per_turn():
    state = UserState(user_id="customer-1")

    recommendation = _intent(
        "knowledge_question",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={"conversation_topic": "product_recommendation"},
    )
    assert resolve_business_action(
        message=_message("推荐几款叶子细长、花香浓、新手适合的兰花"),
        intent=recommendation,
        user_state=state,
        sales_stage="solution_recommended",
    ) == CATALOG_SEARCH

    state.metadata.update(
        {
            "commerce_last_product_id": "orchid-1",
            "commerce_last_product_keyword": "建兰忆香荷",
            "commerce_last_catalog_query": "推荐几款叶子细长、花香浓、新手适合的兰花",
        }
    )
    care = _intent("care_question", route="rag_answer", need_rag=True)
    assert resolve_business_action(
        message=_message("兰花烂根一般是什么原因？"),
        intent=care,
        user_state=state,
    ) == CARE_ANSWER

    objection = _intent("price_objection")
    assert resolve_business_action(
        message=_message("这个价格有点高，我想再看看性价比高一点的"),
        intent=objection,
        user_state=state,
    ) == CONVERSATION

    state.metadata["sales_action"] = {"question_slot": "budget"}
    mistaken_knowledge_intent = _intent(
        "knowledge_question",
        route="rag_answer",
        need_rag=True,
    )
    assert resolve_business_action(
        message=_message("100元以内，最好五六十左右"),
        intent=mistaken_knowledge_intent,
        user_state=state,
        sales_stage="solution_recommended",
    ) == CATALOG_SEARCH
    assert resolve_business_action(
        message=_message("五六十左右"),
        intent=mistaken_knowledge_intent,
        user_state=state,
        sales_stage="solution_recommended",
    ) == CATALOG_SEARCH

    supply_intent = _intent(
        "product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection", "medium_repotting"],
        slots={"product_request_kind": "supply_shortage"},
    )
    assert resolve_business_action(
        message=_message("刚才这款是几苗？有没有带盆的规格？"),
        intent=supply_intent,
        user_state=state,
    ) == SELECTED_PRODUCT_DETAIL

    assert resolve_business_action(
        message=_message("现在付款吗？"),
        intent=_intent("payment_intent"),
        user_state=state,
    ) == CATALOG_SEARCH

    order_information = _intent(
        "order_intent",
        slots={"conversation_topic": "order_information"},
    )
    assert resolve_business_action(
        message=_message("我已经通过微信付款了，地址也发给你了"),
        intent=order_information,
        user_state=state,
    ) == ORDER_VERIFY


def test_pending_order_mobile_overrides_generic_order_intent():
    state = UserState(
        user_id="customer-1",
        metadata={
            "commerce_pending": "order_mobile",
            "commerce_last_product_id": "3997017282",
        },
    )
    intent = _intent(
        "order_intent",
        slots={"shipping_contact": {"mobile": "13000000000"}},
    )

    assert resolve_business_action(
        message=_message("下单手机号是13000000000。"),
        intent=intent,
        user_state=state,
    ) == ORDER_VERIFY


def test_persisted_order_task_routes_status_followup_back_to_order_query():
    state = UserState(
        user_id="customer-1",
        metadata={
            "active_task": {
                "domain": "order",
                "task_type": "order_query",
                "status": "query_failed",
            }
        },
    )

    assert resolve_business_action(
        message=_message("查到了吗？"),
        intent=_intent("ask_after_sale"),
        user_state=state,
    ) == ORDER_VERIFY


def test_explicit_purchase_entry_switches_from_rag_to_catalog():
    state = UserState(user_id="customer-1")
    mistaken_knowledge_intent = _intent(
        "knowledge_question",
        route="rag_answer",
        need_rag=True,
    )

    assert resolve_business_action(
        message=_message("橱窗里能买吗？"),
        intent=mistaken_knowledge_intent,
        user_state=state,
    ) == CATALOG_SEARCH


def test_flowerpot_question_uses_selected_product_details():
    state = UserState(
        user_id="customer-1",
        metadata={"commerce_last_product_id": "3997017282"},
    )
    mistaken_knowledge_intent = _intent(
        "knowledge_question",
        route="rag_answer",
        need_rag=True,
    )

    assert resolve_business_action(
        message=_message("链接显示是花盆？"),
        intent=mistaken_knowledge_intent,
        user_state=state,
    ) == SELECTED_PRODUCT_DETAIL


def test_rejection_blocks_product_action_even_when_intent_is_wrong():
    mistaken_membership_intent = _intent(
        "product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={"product_request_kind": "membership"},
    )

    assert resolve_business_action(
        message=_message("会员暂时不买了，别发链接"),
        intent=mistaken_membership_intent,
        user_state=UserState(user_id="customer-1"),
    ) == CONVERSATION


def test_membership_price_and_purchase_followups_keep_catalog_action():
    state = UserState(
        user_id="customer-1",
        metadata={
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_kind": "membership",
        },
    )

    assert resolve_business_action(
        message=_message("会员现在多少钱？"),
        intent=_intent("ask_price"),
        user_state=state,
    ) == CATALOG_SEARCH
    assert resolve_business_action(
        message=_message("可以，39.9元我能接受，把购买链接发我吧。"),
        intent=_intent(
            "order_intent",
            primary_domain="product",
            primary_goal="purchase",
        ),
        user_state=state,
    ) == CATALOG_SEARCH


def test_product_recommendation_is_blocked_until_discovery_is_complete():
    intent = _intent(
        "product_recommendation",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={"conversation_topic": "product_recommendation"},
    )

    assert resolve_business_action(
        message=_message("给我推荐一盆好养的兰花"),
        intent=intent,
        user_state=UserState(user_id="customer-1", sales_stage="need_discovery"),
        sales_stage="need_discovery",
    ) == CONVERSATION
    assert resolve_business_action(
        message=_message("给我推荐一盆好养的兰花"),
        intent=intent,
        user_state=UserState(user_id="customer-1", sales_stage="pain_discovery"),
        sales_stage="pain_discovery",
    ) == CONVERSATION
    assert resolve_business_action(
        message=_message("给我推荐一盆好养的兰花"),
        intent=intent,
        user_state=UserState(user_id="customer-1", sales_stage="solution_recommended"),
        sales_stage="solution_recommended",
    ) == CATALOG_SEARCH


def test_explicit_transaction_bypasses_discovery_gate():
    intent = _intent(
        "order_intent",
        primary_domain="commerce",
        primary_goal="transact",
        issues=["order_process"],
    )

    assert resolve_business_action(
        message=_message("这款我确定要，怎么买？"),
        intent=intent,
        user_state=UserState(user_id="customer-1", sales_stage="need_discovery"),
        sales_stage="need_discovery",
    ) == CATALOG_SEARCH


def test_low_confidence_membership_semantics_start_catalog_without_previous_card():
    intent = _intent(
        "order_intent",
        primary_domain="commerce",
        primary_goal="transact",
        issues=["order_process", "price_value"],
        slots={
            "product_request_kind": "membership",
            "membership_question_kind": "combined",
        },
    ).model_copy(update={"confidence": 0.32})

    assert resolve_business_action(
        message=_message("那你们的服务怎么进？多少钱？"),
        intent=intent,
        user_state=UserState(user_id="customer-1"),
    ) == CATALOG_SEARCH
