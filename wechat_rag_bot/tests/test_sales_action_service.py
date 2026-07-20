import pytest

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
        intent=_intent(),
    )

    assert decision.sales_action == "discover_need_track"
    assert decision.question_slot == "need_track"
    assert decision.required_slots == ["need_track"]
    assert decision.next_stage == "pain_discovery"


def test_discovery_does_not_repeat_an_asked_slot():
    state = UserState(
        user_id="user_1",
        sales_stage="need_discovery",
        metadata={
            "profile": {
                "active_opportunity": {
                    "slots": {},
                    "asked_slots": ["need_track"],
                }
            }
        },
    )

    decision = decide_sales_action(user_state=state, intent=_intent())

    assert decision.question_slot is None
    assert decision.required_slots == []


def test_sales_action_reads_synchronous_active_opportunity():
    state = UserState(
        user_id="user_1",
        sales_stage="need_discovery",
        metadata={
            "active_opportunity": {
                "slots": {"budget": "100"},
                "asked_slots": ["need_track"],
            }
        },
    )

    decision = decide_sales_action(user_state=state, intent=_intent())

    assert decision.known_slots["budget"] == "100"
    assert decision.question_slot is None


@pytest.mark.asyncio
async def test_state_update_persists_sales_slots_without_profile_analysis():
    from app.services import state_service

    state_service._state_store.clear()
    intent = _intent(budget="100", region="四川", pain_point="root_rot")
    reply = FinalReply(
        answer="reply",
        reply_type="template",
        route="template_reply",
        metadata={
            "sales_action": {
                "sales_action": "discover_need",
                "customer_signal": "interested",
                "known_slots": intent.slots,
                "question_slot": "plant_count",
            },
            "tag_result": {
                "labels": ["budget:100", "region:四川"],
                "entities": {"budget": "100", "region": "四川"},
            },
        },
    )

    await state_service.update_user_state("user_1", "s1", intent, reply)
    state = await state_service.get_user_state("user_1", "s1")

    assert state.metadata["active_opportunity"]["slots"]["budget"] == "100"
    assert state.metadata["active_opportunity"]["asked_slots"] == ["plant_count"]
    assert "budget:100" not in state.customer_tags


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
        intent=_intent(),
    )
    reply = FinalReply(
        answer="这款目前是199元。",
        reply_type="template",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert result.answer == (
        "这款目前是199元。为了更准确地判断，"
        "您这次更需要养护指导、选购产品，还是两者都需要？"
    )
    assert result.answer_segments == [
        "这款目前是199元。",
        "为了更准确地判断，您这次更需要养护指导、选购产品，还是两者都需要？",
    ]


def test_template_reply_appends_only_the_catalog_question_slot():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="好的，我先按您说的情况处理。",
        reply_type="template",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert decision.question_slot == "need_track"
    assert result.answer.count("？") == 1
    assert "养护指导、选购产品" in result.answer


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

    assert decision.sales_action == "resolve_blocker"
    assert decision.customer_signal == "objection"
    assert decision.reason == "intent_priority"


def test_trial_close_value_concern_uses_value_action_instead_of_closing_action():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="trial_close"),
        intent=IntentResult(
            route="template_reply",
            primary_intent="price_objection",
            sales_stage="value_built",
            confidence=0.9,
            slots={
                "sales_stage_reason": "customer_value_concern",
                "decision_blocker": {"type": "price", "detail": "觉得偏贵"},
            },
        ),
    )

    assert decision.sales_action == "build_value"
    assert decision.customer_signal == "objection"
    assert decision.reason == "controlled_loop"


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


def test_opportunity_persists_decision_blocker_outside_slots():
    opportunity = evolve_opportunity(
        {},
        sales_stage="objection_handling",
        sales_action={
            "sales_action": "handle_objection",
            "known_slots": {
                "decision_blocker": {
                    "type": "price",
                    "detail": "客户认为价格偏高",
                }
            },
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert opportunity["decision_blocker"] == {
        "type": "price",
        "detail": "客户认为价格偏高",
    }
    assert "decision_blocker" not in opportunity["slots"]


def test_ready_to_buy_clears_previous_decision_blocker():
    opportunity = evolve_opportunity(
        {
            "opportunity_id": "opp_1",
            "status": "active",
            "slots": {},
            "decision_blocker": {
                "type": "price",
                "detail": "客户认为价格偏高",
            },
        },
        sales_stage="order_intent",
        sales_action={
            "sales_action": "close_order",
            "customer_signal": "ready_to_buy",
            "known_slots": {},
        },
        now="2026-07-09T10:00:00+00:00",
    )

    assert "decision_blocker" not in opportunity


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
