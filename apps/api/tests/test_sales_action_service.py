import pytest

from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.customers.schemas.state import UserState
from app.domains.sales.services.sales_action_service import (
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


def test_discovery_uses_a_concrete_pain_probe_instead_of_service_product_choice():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )

    assert decision.sales_action == "discover_pain"
    assert decision.question_slot == "pain_point"
    assert decision.required_slots == ["pain_point"]
    assert "灵活选择一到三个方向" in decision.reply_goal
    assert "不照抄固定句式" in decision.reply_goal
    assert "服务还是产品" not in decision.reply_goal
    assert decision.next_stage == "pain_discovery"


def test_care_reply_without_specific_pain_asks_one_guided_pain_question():
    intent = _intent().model_copy(
        update={
            "primary_domain": "care",
            "primary_goal": "seek_help",
        }
    )

    decision = decide_sales_action(
        user_state=UserState(user_id="care_user", sales_stage="need_discovery"),
        intent=intent,
    )

    assert decision.sales_action == "discover_pain"
    assert decision.question_slot == "pain_point"
    assert decision.allow_diagnostic_question is False
    assert "灵活选择一到三个方向" in decision.reply_goal
    assert "不照抄固定句式" in decision.reply_goal


def test_care_reply_with_specific_pain_stops_questioning_and_builds_service_value():
    intent = _intent(pain_point="烂根").model_copy(
        update={"primary_domain": "care", "primary_goal": "seek_help"}
    )

    decision = decide_sales_action(
        user_state=UserState(user_id="care_user", sales_stage="pain_discovery"),
        intent=intent,
    )

    assert decision.sales_action == "discover_pain"
    assert decision.question_slot is None
    assert decision.required_slots == []
    assert decision.allow_diagnostic_question is False
    assert "问题反复" in decision.reply_goal
    assert "长期陪伴" in decision.reply_goal
    assert "不要继续追问" in decision.reply_goal


def test_product_interest_still_discovers_need_before_recommendation():
    decision = decide_sales_action(
        user_state=UserState(user_id="product_user", sales_stage="need_discovery"),
        intent=_intent(need_track="product", color_preference="红色"),
    )

    assert decision.sales_action == "discover_pain"
    assert decision.question_slot == "pain_point"
    assert "具体的养兰痛点" in decision.reply_goal


def test_pain_discovery_replaces_premature_product_and_watering_question():
    decision = decide_sales_action(
        user_state=UserState(user_id="product_user", sales_stage="need_discovery"),
        intent=_intent(need_track="product"),
    )
    reply = FinalReply(
        answer=(
            "没问题，咱们就从最适合新手的品种开始看。"
            "您之前养花时，是更容易忘记浇水，还是担心浇多了烂根呢？"
        ),
        reply_type="llm",
        route="llm_reply",
    )

    guarded = apply_sales_action(reply, decision)

    assert "品种开始看" not in guarded.answer
    assert "浇水" not in guarded.answer
    assert any(marker in guarded.answer for marker in ("黑斑", "黄叶", "腐苗"))
    assert guarded.metadata["emitted_question_slot"] == "pain_point"
    assert guarded.metadata["sales_flow_guard"] == "pain_discovery_required"


@pytest.mark.parametrize(
    ("question_kind", "expected_action"),
    [
        ("capability", "answer_current_question"),
        ("price", "answer_current_question"),
        ("purchase", "close_order"),
        ("combined", "close_order"),
    ],
)
def test_membership_sales_action_follows_current_question(question_kind, expected_action):
    intent = IntentResult(
        route="template_reply",
        primary_intent="product_query",
        primary_domain="product",
        primary_goal="seek_help",
        sales_stage="closing",
        confidence=0.99,
        slots={
            "product_request_kind": "membership",
            "membership_question_kind": question_kind,
        },
    )

    decision = decide_sales_action(
        user_state=UserState(user_id="member-user"),
        intent=intent,
    )

    assert decision.sales_action == expected_action
    assert decision.question_slot is None


def test_pain_brand_value_is_not_injected_again_after_it_was_presented():
    from app.domains.conversations.services.chat_orchestrator import (
        _pain_brand_value_already_present,
    )

    state = UserState(
        user_id="u1",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": (
                        "萧岚苑有老师结合具体情况做指导，"
                        "能帮您少走反复试错的弯路。"
                    ),
                }
            ]
        },
    )

    assert _pain_brand_value_already_present(state) is True


def test_discovery_does_not_repeat_an_asked_slot():
    state = UserState(
        user_id="user_1",
        sales_stage="need_discovery",
        metadata={
            "profile": {
                "active_opportunity": {
                    "slots": {},
                    "asked_slots": ["pain_point"],
                }
            }
        },
    )

    decision = decide_sales_action(user_state=state, intent=_intent())

    assert decision.question_slot is None
    assert decision.required_slots == []
    assert decision.sales_action == "discover_pain"


def test_sales_action_reads_synchronous_active_opportunity():
    state = UserState(
        user_id="user_1",
        sales_stage="need_discovery",
        metadata={
            "active_opportunity": {
                "slots": {"budget": "100"},
                "asked_slots": ["pain_point"],
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
                "emitted_question_slot": "plant_count",
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


@pytest.mark.asyncio
async def test_state_update_does_not_mark_a_planned_but_unemitted_question():
    from app.services import state_service

    state_service._state_store.clear()
    intent = _intent()
    reply = FinalReply(
        answer="好的，已经记下了。",
        reply_type="chitchat",
        route="chitchat",
        metadata={
            "sales_action": {
                "sales_action": "discover_need",
                "customer_signal": "interested",
                "known_slots": {},
                "question_slot": "need_track",
            },
        },
    )

    await state_service.update_user_state("user_unemitted", "s1", intent, reply)
    state = await state_service.get_user_state("user_unemitted", "s1")

    assert state.metadata["active_opportunity"]["asked_slots"] == []


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


def test_order_service_does_not_append_sales_discovery_question():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="closing"),
        intent=IntentResult(
            route="template_reply",
            primary_intent="order_query",
            primary_domain="customer_service",
            primary_goal="request_service",
            sales_stage="closing",
            confidence=0.99,
            slots={"order_action": "shipping_date_change"},
        ),
    )
    reply = FinalReply(
        answer="请把下单手机号发给我，我先核对订单。",
        reply_type="template",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert decision.sales_action == "provide_service"
    assert decision.question_slot is None
    assert result.answer == reply.answer


def test_neutral_discovery_reply_without_question_is_preserved():
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

    assert result.answer == "这款目前是199元。"
    assert "emitted_question_slot" not in result.metadata


def test_chitchat_discovery_reply_without_question_is_preserved():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="好的，已经记下了。",
        reply_type="chitchat",
        route="chitchat",
    )

    result = apply_sales_action(reply, decision)

    assert result.answer == "好的，已经记下了。"
    assert "emitted_question_slot" not in result.metadata


def test_llm_fallback_must_execute_the_current_pain_discovery_action():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="好的，我接着帮您看看。",
        reply_type="llm_fallback",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert result.answer.startswith("好的，我接着帮您看看。")
    assert any(marker in result.answer for marker in ("黄叶", "黑斑", "烂根", "腐苗"))
    assert result.metadata["emitted_question_slot"] == "pain_point"


def test_unrelated_discovery_question_is_replaced_with_pain_probe():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="您目前放在室内还是室外？",
        reply_type="rag",
        route="rag_answer",
    )

    result = apply_sales_action(reply, decision)

    assert "室内还是室外" not in result.answer
    assert any(marker in result.answer for marker in ("黄叶", "黑斑", "烂根", "腐苗"))
    assert result.metadata["emitted_question_slot"] == "pain_point"


def test_diagnostic_discovery_question_without_punctuation_is_replaced():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="先按您说的情况排查，您平时用什么植料种的",
        reply_type="rag",
        route="rag_answer",
    )

    result = apply_sales_action(reply, decision)

    assert "植料" not in result.answer
    assert any(marker in result.answer for marker in ("黄叶", "黑斑", "烂根", "腐苗"))
    assert result.metadata["emitted_question_slot"] == "pain_point"


def test_discovery_guard_removes_product_card_before_pain_is_clear():
    decision = decide_sales_action(
        user_state=UserState(user_id="user_1", sales_stage="need_discovery"),
        intent=_intent(),
    )
    reply = FinalReply(
        answer="我会为您挑选几款皮实好养的品种，请问放室内还是阳台？",
        answer_segments=["我会为您挑选几款皮实好养的品种，请问放室内还是阳台？"],
        outbound_messages=[
            OutboundMessage(type="text", content="我会为您挑选几款皮实好养的品种，请问放室内还是阳台？"),
            OutboundMessage(type="link_card", content="https://example.com/product"),
        ],
        reply_type="template",
        route="template_reply",
    )

    result = apply_sales_action(reply, decision)

    assert decision.question_slot == "pain_point"
    assert "挑选" not in result.answer
    assert "室内还是阳台" not in result.answer
    assert any(marker in result.answer for marker in ("黄叶", "黑斑", "烂根", "腐苗"))
    assert [message.type for message in result.outbound_messages] == ["text"]
    assert result.outbound_messages[0].content == result.answer
    assert result.metadata["emitted_question_slot"] == "pain_point"


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
