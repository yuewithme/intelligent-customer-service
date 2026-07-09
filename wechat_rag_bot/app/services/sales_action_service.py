from pydantic import BaseModel, Field

from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState


DISCOVERY_SLOTS = ("pain_point", "plant_count")
INTERNAL_SLOTS = {"original_route", "sales_stage_reason"}


class SalesActionDecision(BaseModel):
    reply_goal: str
    sales_action: str
    required_slots: list[str] = Field(default_factory=list)
    question_slot: str | None = None
    next_stage: str | None = None
    known_slots: dict = Field(default_factory=dict)
    recommended_product_ids: list[str] = Field(default_factory=list)


def decide_sales_action(
    *,
    user_state: UserState,
    intent: IntentResult,
) -> SalesActionDecision:
    profile = user_state.metadata.get("profile", {})
    opportunity = profile.get("active_opportunity", {}) if isinstance(profile, dict) else {}
    known_slots = {
        **_dict_value(opportunity.get("slots")),
        **{
            key: value
            for key, value in intent.slots.items()
            if key not in INTERNAL_SLOTS and value not in (None, "", [])
        },
    }
    asked_slots = set(_string_list(opportunity.get("asked_slots")))
    stage = intent.sales_stage or user_state.sales_stage

    if stage == "human_pending":
        return _decision("转交人工继续处理", "handoff_to_human", known_slots)
    if stage == "after_sale":
        return _decision("优先解决客户售后问题", "provide_service", known_slots)
    if stage == "order_intent":
        return _decision("确认订单必要信息并推进成交", "close_order", known_slots)
    if stage == "objection_handling":
        return _decision("回应当前异议并确认客户是否接受", "handle_objection", known_slots)
    if stage == "price_discussed":
        return _decision("先回答当前问题，再确认客户的购买意向", "explain_value", known_slots)
    if stage == "solution_recommended":
        return _decision("说明方案价值并确认客户是否认可", "explain_value", known_slots)
    if stage == "pain_confirmed":
        return _decision("基于明确痛点推荐合适方案", "recommend_solution", known_slots)

    missing = [
        slot
        for slot in DISCOVERY_SLOTS
        if slot not in known_slots and slot not in asked_slots
    ]
    question_slot = missing[0] if missing else None
    goal = (
        f"先回答客户当前问题，再确认{_slot_label(question_slot)}"
        if question_slot
        else "先回答客户当前问题，并自然推进需求确认"
    )
    return SalesActionDecision(
        reply_goal=goal,
        sales_action="discover_need",
        required_slots=[question_slot] if question_slot else [],
        question_slot=question_slot,
        next_stage="pain_confirmed",
        known_slots=known_slots,
    )


def apply_sales_action(
    reply: FinalReply,
    decision: SalesActionDecision,
) -> FinalReply:
    if reply.reply_type != "template" or not decision.question_slot:
        return reply
    question = {
        "pain_point": "您现在最想解决的具体问题是什么？",
        "plant_count": "您大概有多少盆需要使用？",
    }.get(decision.question_slot)
    if not question or question in reply.answer:
        return reply
    return reply.model_copy(update={"answer": f"{reply.answer.rstrip()}\n{question}"})


def _decision(goal: str, action: str, known_slots: dict) -> SalesActionDecision:
    return SalesActionDecision(
        reply_goal=goal,
        sales_action=action,
        known_slots=known_slots,
    )


def _dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _string_list(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _slot_label(slot: str) -> str:
    return {
        "pain_point": "客户要解决的核心问题",
        "plant_count": "客户的使用数量",
    }.get(slot, slot)
