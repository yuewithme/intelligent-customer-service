from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.sales.schemas.sales_flow import (
    CustomerSignal,
    SalesInterruption,
    SalesInterruptionType,
    SalesOpportunityStatus,
    SalesSignalResult,
    SalesStage,
)
from app.domains.customers.schemas.state import UserState
from app.domains.sales.schemas.tag import TagResult
from app.domains.sales.services.sales_action_service import evolve_opportunity
from app.domains.sales.services.sales_stage_service import decide_sales_stage


def _intent(primary_intent: str = "knowledge_question", **slots) -> IntentResult:
    return IntentResult(
        route="template_reply",
        primary_intent=primary_intent,
        confidence=0.9,
        slots=slots,
    )


def _tag(intent: IntentResult, *, risk_level: str = "normal") -> TagResult:
    return TagResult(
        intent=intent.primary_intent,
        route=intent.route,
        confidence=intent.confidence,
        risk_level=risk_level,
    )


def _signals(*signals: CustomerSignal, slots=None, incoming=None) -> SalesSignalResult:
    return SalesSignalResult(
        signals=signals,
        slots=slots or {},
        incoming_slots=tuple(incoming or (slots or {}).keys()),
    )


def _decide(state: UserState, signals: SalesSignalResult, intent=None):
    intent = intent or _intent()
    return decide_sales_stage(
        user_state=state,
        intent=intent,
        tag_result=_tag(intent),
        signal_result=signals,
    )


def test_seven_stage_happy_path_is_evidence_driven():
    stages = []
    state = UserState(user_id="user_1", sales_stage="rapport")
    cases = [
        (_signals(CustomerSignal.RESPONDED), SalesStage.NEED_DISCOVERY),
        (
            _signals(CustomerSignal.PAIN_REVEALED, slots={"pain_point": "烂根"}),
            SalesStage.PAIN_DISCOVERY,
        ),
        (
            _signals(
                CustomerSignal.PRODUCT_NEED,
                slots={
                    "need_track": "product",
                    "region": "四川",
                    "budget": "200",
                },
            ),
            SalesStage.SOLUTION_RECOMMENDED,
        ),
        (_signals(CustomerSignal.RECOMMENDATION_ENGAGED), SalesStage.VALUE_BUILT),
        (_signals(CustomerSignal.PRICE_INTEREST), SalesStage.TRIAL_CLOSE),
        (_signals(CustomerSignal.OBJECTION), SalesStage.VALUE_BUILT),
        (
            _signals(
                CustomerSignal.VALUE_ACKNOWLEDGED,
                slots={"selected_product_id": "product_1"},
                incoming=["value_acknowledged"],
            ),
            SalesStage.TRIAL_CLOSE,
        ),
        (_signals(CustomerSignal.READY_TO_BUY), SalesStage.CLOSING),
    ]

    for signals, expected in cases:
        decision = _decide(state, signals)
        stages.append(decision.stage)
        state.sales_stage = decision.stage.value

    assert stages == [
        SalesStage.NEED_DISCOVERY,
        SalesStage.PAIN_DISCOVERY,
        SalesStage.SOLUTION_RECOMMENDED,
        SalesStage.VALUE_BUILT,
        SalesStage.TRIAL_CLOSE,
        SalesStage.VALUE_BUILT,
        SalesStage.TRIAL_CLOSE,
        SalesStage.CLOSING,
    ]


def test_complete_customer_context_can_jump_directly_to_recommendation():
    result = _decide(
        UserState(user_id="user_1", sales_stage="rapport"),
        _signals(
            CustomerSignal.PRODUCT_NEED,
            CustomerSignal.PREFERENCE_REVEALED,
            slots={
                "need_track": "product",
                "region": "四川",
                "budget": "200",
                "fragrance_preference": "浓香",
            },
        ),
    )

    assert result.stage == SalesStage.SOLUTION_RECOMMENDED
    assert result.transition_type == "jump"


def test_price_without_recommendation_basis_stays_in_need_discovery():
    result = _decide(
        UserState(user_id="user_1", sales_stage="rapport"),
        _signals(CustomerSignal.PRICE_INTEREST),
    )

    assert result.stage == SalesStage.NEED_DISCOVERY
    assert result.opportunity_status == SalesOpportunityStatus.ACTIVE


def test_new_environment_fact_loops_value_stage_back_to_recommendation():
    state = UserState(
        user_id="user_1",
        sales_stage="value_built",
        metadata={
            "active_opportunity": {
                "status": "active",
                "current_stage": "value_built",
                "slots": {"region": "四川"},
            }
        },
    )

    result = _decide(
        state,
        _signals(slots={"region": "甘肃"}, incoming=["region"]),
    )

    assert result.stage == SalesStage.SOLUTION_RECOMMENDED
    assert result.transition_type == "loop"
    assert result.reason == "recommendation_context_changed"


def test_resolved_blocker_loops_closing_back_to_trial_close():
    state = UserState(
        user_id="user_1",
        sales_stage="closing",
        metadata={
            "active_opportunity": {
                "status": "active",
                "current_stage": "closing",
                "slots": {},
            }
        },
    )

    result = _decide(
        state,
        _signals(slots={"blocker_resolved": True}, incoming=["blocker_resolved"]),
    )

    assert result.stage == SalesStage.TRIAL_CLOSE
    assert result.transition_type == "loop"


def test_interruption_remains_paused_until_explicitly_resolved():
    after_sale = _intent("refund_request")
    state = UserState(user_id="user_1", sales_stage="trial_close")
    paused = decide_sales_stage(
        user_state=state,
        intent=after_sale,
        tag_result=_tag(after_sale),
        signal_result=_signals(),
    )
    assert paused.transition_type == "interrupt"
    assert paused.opportunity_status == SalesOpportunityStatus.PAUSED
    assert paused.interruption.resume_stage == SalesStage.TRIAL_CLOSE

    state.metadata["active_opportunity"] = {
        "status": "paused",
        "current_stage": "trial_close",
        "interruption": paused.interruption.model_dump(mode="json"),
    }
    unresolved = _decide(state, _signals(CustomerSignal.RESPONDED))
    assert unresolved.opportunity_status == SalesOpportunityStatus.PAUSED

    resumed = _decide(
        state,
        _signals(slots={"interruption_resolved": True}),
    )
    assert resumed.stage == SalesStage.TRIAL_CLOSE
    assert resumed.transition_type == "resume"
    assert resumed.interruption is None


def test_payment_claim_does_not_win_but_trusted_purchase_does():
    state = UserState(user_id="user_1", sales_stage="closing")

    claimed = _decide(state, _signals(CustomerSignal.PAYMENT_CLAIMED))
    assert claimed.stage == SalesStage.CLOSING
    assert claimed.opportunity_status == SalesOpportunityStatus.ACTIVE
    assert claimed.transition_type == "keep"

    purchased = _decide(state, _signals(CustomerSignal.PURCHASED))
    assert purchased.stage == SalesStage.CLOSING
    assert purchased.opportunity_status == SalesOpportunityStatus.WON
    assert purchased.transition_type == "close"


def test_stage_evidence_and_interruption_are_persisted_with_the_opportunity():
    interruption = SalesInterruption(
        type=SalesInterruptionType.AFTER_SALE,
        reason="after_sale_intent",
        resume_stage=SalesStage.TRIAL_CLOSE,
        started_at="2026-07-20T10:00:00Z",
    )
    opportunity = evolve_opportunity(
        {
            "opportunity_id": "opp_1",
            "status": "active",
            "slots": {"selected_product_id": "p1"},
            "asked_slots": ["quantity"],
        },
        sales_stage="trial_close",
        sales_action={
            "sales_action": "provide_service",
            "customer_signal": "none",
            "known_slots": {},
        },
        stage_decision={
            "stage": "trial_close",
            "previous_stage": "trial_close",
            "reason": "after_sale_intent",
            "transition_type": "interrupt",
            "opportunity_status": "paused",
            "signals": [],
            "evidence": [
                {
                    "code": "signal:responded",
                    "source": "message",
                    "value": True,
                    "trusted": False,
                }
            ],
            "interruption": interruption.model_dump(mode="json"),
        },
        now="2026-07-20T10:00:00+00:00",
    )

    assert opportunity["current_stage"] == "trial_close"
    assert opportunity["stage_reason"] == "after_sale_intent"
    assert opportunity["transition_type"] == "interrupt"
    assert opportunity["status"] == "paused"
    assert opportunity["interruption"]["resume_stage"] == "trial_close"
    assert opportunity["stage_evidence"][0]["code"] == "signal:responded"


def test_new_need_after_closed_opportunity_starts_from_a_fresh_stage():
    state = UserState(
        user_id="user_1",
        sales_stage="closing",
        metadata={
            "active_opportunity": {
                "opportunity_id": "opp_closed",
                "status": "won",
                "current_stage": "closing",
                "slots": {},
            }
        },
    )

    decision = _decide(state, _signals(CustomerSignal.SERVICE_NEED))

    assert decision.stage == SalesStage.NEED_DISCOVERY
    assert decision.opportunity_status == SalesOpportunityStatus.ACTIVE
    assert decision.reason == "new_first_order_opportunity"
