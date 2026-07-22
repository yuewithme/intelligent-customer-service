from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.sales.schemas.tag import TagResult
from app.domains.sales.services.sales_stage_service import decide_sales_stage


def _intent(primary_intent: str, sales_stage: str = "unknown") -> IntentResult:
    return IntentResult(
        route="template_reply",
        primary_intent=primary_intent,
        sales_stage=sales_stage,
        confidence=0.9,
    )


def _tag(intent: str, stage: str = "unknown", labels: list[str] | None = None) -> TagResult:
    return TagResult(
        intent=intent,
        route="template_reply",
        stage=stage,
        confidence=0.9,
        labels=labels or [],
    )


def test_ask_price_without_need_stays_in_discovery():
    state = UserState(user_id="user_1", sales_stage="unknown")

    result = decide_sales_stage(
        user_state=state,
        intent=_intent("ask_price", sales_stage="price_discussed"),
        tag_result=_tag("ask_price"),
    )

    assert result.stage == "need_discovery"
    assert result.reason == "price_before_need_discovery"


def test_ask_price_after_solution_moves_to_trial_close():
    state = UserState(user_id="user_1", sales_stage="solution_recommended")

    result = decide_sales_stage(
        user_state=state,
        intent=_intent("ask_price", sales_stage="price_discussed"),
        tag_result=_tag("ask_price", labels=["product_interest:orchid_care"]),
    )

    assert result.stage == "trial_close"
    assert result.reason == "price_after_need_or_solution"


def test_trial_close_objection_loops_back_to_value_building():
    state = UserState(user_id="user_1", sales_stage="price_discussed")

    result = decide_sales_stage(
        user_state=state,
        intent=_intent("price_objection", sales_stage="objection_handling"),
        tag_result=_tag("price_objection"),
    )

    assert result.stage == "value_built"
    assert result.reason == "customer_value_concern"
    assert result.transition_type == "loop"


def test_trial_close_choice_blocker_loops_back_to_solution():
    state = UserState(user_id="user_1", sales_stage="trial_close")
    intent = _intent("hesitation", sales_stage="trial_close").model_copy(
        update={"slots": {"decision_blocker": {"type": "choice", "detail": "不知道选哪个"}}}
    )

    result = decide_sales_stage(
        user_state=state,
        intent=intent,
        tag_result=_tag("hesitation"),
    )

    assert result.stage == "solution_recommended"
    assert result.reason == "recommendation_blocker"


def test_weak_intent_does_not_regress_order_stage():
    state = UserState(user_id="user_1", sales_stage="order_intent")

    result = decide_sales_stage(
        user_state=state,
        intent=_intent("care_question", sales_stage="pain_confirmed"),
        tag_result=_tag("care_question"),
    )

    assert result.stage == "closing"
    assert result.reason == "keep_current_stage"


def test_greeting_does_not_regress_price_stage():
    state = UserState(user_id="user_1", sales_stage="price_discussed")

    result = decide_sales_stage(
        user_state=state,
        intent=_intent("greeting", sales_stage="greeting"),
        tag_result=_tag("greeting"),
    )

    assert result.stage == "trial_close"
    assert result.reason == "keep_current_stage"
