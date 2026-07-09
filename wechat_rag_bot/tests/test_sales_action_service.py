from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.services.sales_action_service import (
    apply_sales_action,
    decide_sales_action,
    evolve_opportunity,
)


def _intent(**slots) -> IntentResult:
    return IntentResult(
        route="rag_answer",
        primary_intent="care_question",
        sales_stage="need_discovery",
        confidence=0.9,
        slots=slots,
    )


def test_discovery_asks_for_first_missing_sales_slot():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(pain_point="root_rot"),
    )

    assert decision.sales_action == "discover_need"
    assert decision.question_slot == "plant_count"
    assert decision.required_slots == ["plant_count"]
    assert decision.next_stage == "pain_confirmed"


def test_discovery_does_not_repeat_an_asked_slot():
    state = UserState(
        user_id="user_1",
        sales_stage="need_discovery",
        metadata={
            "profile": {
                "active_opportunity": {
                    "slots": {"pain_point": "root_rot"},
                    "asked_slots": ["plant_count"],
                }
            }
        },
    )

    decision = decide_sales_action(user_state=state, intent=_intent())

    assert decision.question_slot is None
    assert decision.required_slots == []


def test_after_sale_disables_sales_progression():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="after_sale"),
        intent=IntentResult(
            route="template_reply",
            primary_intent="ask_after_sale",
            sales_stage="after_sale",
            confidence=0.9,
        ),
    )

    assert decision.sales_action == "provide_service"
    assert decision.question_slot is None
    assert decision.next_stage is None


def test_template_reply_executes_single_sales_question():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(pain_point="root_rot"),
    )
    reply = FinalReply(
        answer="这款目前是199元。",
        reply_type="template",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert result.answer == (
        "这款目前是199元。具体用量要结合您的实际使用数量判断，"
        "您大概有多少盆需要使用？"
    )
    assert result.answer_segments == [
        "这款目前是199元。",
        "具体用量要结合您的实际使用数量判断，您大概有多少盆需要使用？",
    ]


def test_objection_intent_overrides_stage_default_action():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="price_discussed"),
        intent=IntentResult(
            route="template_reply",
            primary_intent="price_objection",
            sales_stage="price_discussed",
            confidence=0.9,
        ),
    )

    assert decision.sales_action == "handle_objection"
    assert decision.customer_signal == "objection"
    assert decision.reason == "intent_priority"


def test_order_intent_overrides_discovery_stage():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=IntentResult(
            route="template_reply",
            primary_intent="order_intent",
            sales_stage="need_discovery",
            confidence=0.9,
        ),
    )

    assert decision.sales_action == "close_order"
    assert decision.customer_signal == "ready_to_buy"


def test_opportunity_is_won_only_after_completed_order_signal():
    opportunity = evolve_opportunity(
        {},
        sales_stage="order_intent",
        sales_action={
            "sales_action": "close_order",
            "customer_signal": "purchased",
            "known_slots": {},
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert opportunity["status"] == "won"
    assert opportunity["closed_at"] == "2026-07-09T10:00:00+00:00"
    assert opportunity["close_reason"] == "purchased"


def test_opportunity_closes_when_customer_rejects_purchase():
    opportunity = evolve_opportunity(
        {"opportunity_id": "opp_1", "status": "active", "slots": {}},
        sales_stage="objection_handling",
        sales_action={
            "sales_action": "handle_objection",
            "customer_signal": "rejected",
            "known_slots": {},
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert opportunity["status"] == "lost"
    assert opportunity["close_reason"] == "rejected"


def test_stale_opportunity_is_replaced_after_thirty_days():
    opportunity = evolve_opportunity(
        {
            "opportunity_id": "opp_old",
            "status": "active",
            "slots": {},
            "last_active_at": "2026-05-01T10:00:00+00:00",
        },
        sales_stage="need_discovery",
        sales_action={
            "sales_action": "discover_need",
            "customer_signal": "interested",
            "known_slots": {},
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert opportunity["opportunity_id"] != "opp_old"
    assert opportunity["replace_reason"] == "expired"


def test_new_product_replaces_active_opportunity():
    opportunity = evolve_opportunity(
        {
            "opportunity_id": "opp_old",
            "status": "active",
            "slots": {"product_category": "orchid_care"},
            "asked_slots": [],
            "started_at": "2026-07-01T10:00:00+00:00",
        },
        sales_stage="need_discovery",
        sales_action={
            "sales_action": "discover_need",
            "customer_signal": "interested",
            "known_slots": {"product_category": "orchid_purchase"},
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert opportunity["opportunity_id"] != "opp_old"
    assert opportunity["status"] == "active"
    assert opportunity["replaced_opportunity_id"] == "opp_old"
    assert opportunity["replace_reason"] == "product_changed"


def test_greeting_does_not_create_or_reopen_opportunity():
    sales_action = {
        "sales_action": "build_rapport",
        "customer_signal": "none",
        "known_slots": {},
    }

    assert evolve_opportunity(
        {},
        sales_stage="greeting",
        sales_action=sales_action,
        now="2026-07-09T10:00:00+00:00",
    ) == {}

    closed = {"opportunity_id": "opp_1", "status": "won", "slots": {}}
    assert evolve_opportunity(
        closed,
        sales_stage="greeting",
        sales_action=sales_action,
        now="2026-07-09T10:00:00+00:00",
    ) == closed
