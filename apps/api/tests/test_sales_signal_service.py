from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.domains.sales.schemas.sales_flow import CustomerSignal
from app.domains.customers.schemas.state import UserState
from app.domains.sales.schemas.tag import TagResult
from app.domains.sales.services.sales_signal_service import normalize_sales_signals


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_1",
        channel="api",
        user_id="user_1",
        session_id="session_1",
        message=text,
        kb_id="kb_default",
    )


def _intent(primary_intent: str, **slots) -> IntentResult:
    return IntentResult(
        route="template_reply",
        primary_intent=primary_intent,
        confidence=0.9,
        slots=slots,
    )


def _tag(intent: IntentResult, *, labels=None, entities=None) -> TagResult:
    return TagResult(
        intent=intent.primary_intent,
        route=intent.route,
        confidence=intent.confidence,
        labels=labels or [],
        entities=entities or intent.slots,
    )


def test_normalizes_combined_need_and_slot_aliases():
    intent = IntentResult(
        route="rag_answer",
        primary_intent="care_question",
        secondary_intents=["product_query"],
        confidence=0.9,
        slots={"province": "四川", "need_type": "both", "problem": "烂根"},
    )

    result = normalize_sales_signals(
        message=_message("我在四川，兰花烂根，也想重新买一盆"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent, labels=["pain_point:兰花烂根"]),
    )

    assert set(result.signals) >= {
        CustomerSignal.SERVICE_NEED,
        CustomerSignal.PRODUCT_NEED,
        CustomerSignal.COMBINED_NEED,
        CustomerSignal.PAIN_REVEALED,
    }
    assert result.slots["region"] == "四川"
    assert result.slots["need_track"] == "combined"
    assert result.slots["pain_point"] == "烂根"


def test_customer_payment_statement_is_only_a_claim():
    intent = _intent("payment_success")

    result = normalize_sales_signals(
        message=_message("我已经付了"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
        business_facts=BusinessFacts(),
    )

    assert CustomerSignal.PAYMENT_CLAIMED in result.signals
    assert CustomerSignal.PURCHASED not in result.signals


def test_verified_payment_fact_is_the_only_source_of_purchased():
    intent = _intent("payment_success")

    result = normalize_sales_signals(
        message=_message("我已经付了"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
        business_facts=BusinessFacts(tool_state={"payment_status": "paid"}),
    )

    assert CustomerSignal.PURCHASED in result.signals
    purchased = next(
        item for item in result.evidence if item.code == "signal:purchased"
    )
    assert purchased.source == "commerce"
    assert purchased.trusted is True


def test_verified_youzan_order_status_is_trusted_purchase_evidence():
    intent = _intent("order_query")

    result = normalize_sales_signals(
        message=_message("查一下订单"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
        business_facts=BusinessFacts(
            tool_state={
                "orders": [
                    {"order_no": "E001", "status": "WAIT_SELLER_SEND_GOODS"}
                ]
            }
        ),
    )

    assert CustomerSignal.PURCHASED in result.signals


def test_catalog_match_sets_existing_selected_product_slots():
    intent = _intent("product_query")

    result = normalize_sales_signals(
        message=_message("怎么加入会员"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
        business_facts=BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "found",
                "products": [
                    {
                        "item_id": "membership-39",
                        "title": "首单参与陪伴养兰客户 专享特惠链接",
                    }
                ],
            }
        ),
    )
    assert result.slots["selected_product_id"] == "membership-39"
    assert result.slots["selected_product_name"] == "首单参与陪伴养兰客户 专享特惠链接"
    assert CustomerSignal.RECOMMENDATION_ENGAGED in result.signals


def test_reply_after_service_recommendation_is_recommendation_engagement():
    intent = IntentResult(
        route="chitchat",
        primary_intent="greeting",
        primary_domain="conversation",
        primary_goal="social",
        confidence=0.99,
        slots={"chitchat_kind": "thanks"},
    )
    state = UserState(
        user_id="user_1",
        sales_stage="solution_recommended",
        metadata={
            "active_opportunity": {
                "last_sales_action": "recommend_solution",
                "service_offer_phase": "introduced",
                "slots": {"need_track": "service", "pain_point": "黑斑"},
            }
        },
    )

    result = normalize_sales_signals(
        user_state=state,
        intent=intent,
        tag_result=_tag(intent),
        message=_message("谢谢"),
    )

    assert CustomerSignal.RESPONDED in result.signals
    assert CustomerSignal.RECOMMENDATION_ENGAGED in result.signals

def test_supply_accessory_does_not_replace_selected_primary_product():
    intent = _intent("product_query")
    state = UserState(
        user_id="user_1",
        metadata={
            "active_opportunity": {
                "slots": {
                    "selected_product_id": "orchid-main",
                    "selected_product_name": "建兰龙岩素",
                }
            }
        },
    )

    result = normalize_sales_signals(
        message=_message("家里花盆不够"),
        user_state=state,
        intent=intent,
        tag_result=_tag(intent),
        business_facts=BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "found",
                "product_request_kind": "supply_shortage",
                "products": [{"item_id": "pot-1", "title": "兰花专用紫砂盆"}],
            }
        ),
    )

    assert result.slots["selected_product_id"] == "orchid-main"
    assert result.slots["selected_product_name"] == "建兰龙岩素"


def test_care_question_is_service_need_not_automatic_after_sale():
    intent = _intent("care_question", pain_point="黄叶")

    result = normalize_sales_signals(
        message=_message("兰花黄叶怎么处理"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
    )

    assert CustomerSignal.SERVICE_NEED in result.signals
    assert CustomerSignal.PURCHASED not in result.signals


def test_opening_profile_answer_does_not_imply_product_need():
    intent = IntentResult(
        route="chitchat",
        primary_intent="profile_answer",
        primary_domain="product",
        primary_goal="provide_information",
        confidence=0.98,
        slots={"plant_count": 4},
    )

    result = normalize_sales_signals(
        message=_message("目前还有四盆"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
    )

    assert CustomerSignal.PRODUCT_NEED not in result.signals
    assert result.slots.get("need_track") != "product"


def test_legacy_profile_product_track_is_corrected_to_service_default():
    intent = IntentResult(
        route="chitchat",
        primary_intent="profile_answer",
        primary_domain="product",
        primary_goal="provide_information",
        confidence=0.98,
        slots={"plant_count": 4},
    )
    state = UserState(
        user_id="user_1",
        metadata={
            "profile": {"last_intent": "profile_answer"},
            "active_opportunity": {
                "slots": {"plant_count": 4, "need_track": "product"},
                "last_sales_action": "discover_need_track",
            },
        },
    )

    result = normalize_sales_signals(
        message=_message("死了买，买了死"),
        user_state=state,
        intent=intent,
        tag_result=_tag(intent),
    )

    assert CustomerSignal.PRODUCT_NEED not in result.signals
    assert result.slots["need_track"] == "service"


def test_decision_blocker_uses_the_canonical_types():
    intent = _intent(
        "hesitation",
        decision_blocker={"type": "communication", "detail": "不想重复回答"},
    )

    result = normalize_sales_signals(
        message=_message("别再重复问了"),
        user_state=UserState(user_id="user_1"),
        intent=intent,
        tag_result=_tag(intent),
    )

    assert result.slots["decision_blocker"] == {
        "type": "other",
        "detail": "不想重复回答",
    }
    assert CustomerSignal.OBJECTION in result.signals
