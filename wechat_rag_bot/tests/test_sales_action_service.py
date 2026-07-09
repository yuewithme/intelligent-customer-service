from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.services.sales_action_service import apply_sales_action, decide_sales_action


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

    assert result.answer == "这款目前是199元。\n您大概有多少盆需要使用？"
