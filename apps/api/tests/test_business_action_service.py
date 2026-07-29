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
    ) == CATALOG_SEARCH
    assert resolve_business_action(
        message=_message("五六十左右"),
        intent=mistaken_knowledge_intent,
        user_state=state,
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
