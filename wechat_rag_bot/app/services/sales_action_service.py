from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState


DISCOVERY_SLOTS = ("pain_point", "plant_count")
INTERNAL_SLOTS = {"original_route", "sales_stage_reason"}
AFTER_SALE_INTENTS = {"ask_after_sale", "refund_request", "complaint"}
OBJECTION_INTENTS = {"price_objection", "discount_request", "hesitation", "trust_issue"}
ORDER_INTENTS = {"order_intent", "payment_intent"}
PURCHASED_INTENTS = {"order_completed", "payment_success"}
REJECTED_INTENTS = {"purchase_rejection", "not_interested"}
NON_SALES_ACTIONS = {"build_rapport", "provide_service", "handoff_to_human"}


class SalesActionDecision(BaseModel):
    reply_goal: str
    sales_action: str
    required_slots: list[str] = Field(default_factory=list)
    question_slot: str | None = None
    next_stage: str | None = None
    known_slots: dict = Field(default_factory=dict)
    recommended_product_ids: list[str] = Field(default_factory=list)
    customer_signal: str = "none"
    reason: str = "stage_default"


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

    priority = _priority_action(intent.primary_intent, intent.need_human, intent.route)
    if priority:
        goal, action, signal = priority
        return _decision(
            goal,
            action,
            known_slots,
            customer_signal=signal,
            reason="intent_priority",
        )

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
    if stage in {"unknown", "greeting"} and not known_slots:
        return _decision("自然回应并了解客户来意", "build_rapport", known_slots)

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
        customer_signal="interested",
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
    bridge = {
        "pain_point": "为了给您更准确的建议，",
        "plant_count": "具体用量要结合您的实际使用数量判断，",
    }[decision.question_slot]
    follow_up = f"{bridge}{question}"
    return reply.model_copy(
        update={
            "answer": f"{reply.answer.rstrip()}{follow_up}",
            "answer_segments": [reply.answer.strip(), follow_up],
        }
    )


def evolve_opportunity(
    current: dict,
    *,
    sales_stage: str,
    sales_action: dict,
    now: str | None = None,
) -> dict:
    timestamp = now or datetime.now(timezone.utc).isoformat()
    opportunity = dict(current) if isinstance(current, dict) else {}
    known_slots = _dict_value(sales_action.get("known_slots"))
    if (
        sales_action.get("sales_action") in NON_SALES_ACTIONS
        and sales_action.get("customer_signal", "none") == "none"
        and not known_slots
    ):
        return opportunity
    replace_reason = _replace_reason(opportunity, known_slots, timestamp)
    if not opportunity.get("opportunity_id") or replace_reason:
        old_id = opportunity.get("opportunity_id")
        opportunity = {
            "opportunity_id": f"opp_{uuid4().hex}",
            "status": "active",
            "slots": {},
            "asked_slots": [],
            "started_at": timestamp,
        }
        if old_id:
            opportunity["replaced_opportunity_id"] = old_id
            opportunity["replace_reason"] = replace_reason

    opportunity["slots"] = {**_dict_value(opportunity.get("slots")), **known_slots}
    opportunity["sales_stage"] = sales_stage
    opportunity["last_sales_action"] = sales_action.get("sales_action")
    opportunity["last_reply_goal"] = sales_action.get("reply_goal")
    opportunity["last_customer_signal"] = sales_action.get("customer_signal", "none")
    opportunity["last_active_at"] = timestamp
    opportunity["recommended_product_ids"] = _string_list(
        sales_action.get("recommended_product_ids")
    )
    question_slot = sales_action.get("question_slot")
    asked_slots = _string_list(opportunity.get("asked_slots"))
    if isinstance(question_slot, str) and question_slot:
        asked_slots = _append_unique(asked_slots, question_slot)
    opportunity["asked_slots"] = asked_slots

    signal = opportunity["last_customer_signal"]
    if signal in {"purchased", "rejected"}:
        opportunity["status"] = "won" if signal == "purchased" else "lost"
        opportunity["closed_at"] = timestamp
        opportunity["close_reason"] = signal
    return opportunity


def _decision(
    goal: str,
    action: str,
    known_slots: dict,
    *,
    customer_signal: str = "none",
    reason: str = "stage_default",
) -> SalesActionDecision:
    return SalesActionDecision(
        reply_goal=goal,
        sales_action=action,
        known_slots=known_slots,
        customer_signal=customer_signal,
        reason=reason,
    )


def _priority_action(intent: str, need_human: bool, route: str):
    if need_human or route == "human":
        return ("转交人工继续处理", "handoff_to_human", "none")
    if intent in AFTER_SALE_INTENTS:
        return ("优先解决客户售后问题", "provide_service", "none")
    if intent in PURCHASED_INTENTS:
        return ("确认成交结果并完成服务交接", "close_order", "purchased")
    if intent in REJECTED_INTENTS:
        return ("尊重客户决定并结束本次销售推进", "handle_objection", "rejected")
    if intent in ORDER_INTENTS:
        return ("确认订单必要信息并推进成交", "close_order", "ready_to_buy")
    if intent in OBJECTION_INTENTS:
        return ("回应当前异议并确认客户是否接受", "handle_objection", "objection")
    return None


def _replace_reason(opportunity: dict, known_slots: dict, now: str) -> str | None:
    if opportunity.get("status") in {"won", "lost", "expired"}:
        return opportunity["status"]
    old_product = _dict_value(opportunity.get("slots")).get("product_category")
    new_product = known_slots.get("product_category")
    if old_product and new_product and old_product != new_product:
        return "product_changed"
    last_active = opportunity.get("last_active_at")
    if isinstance(last_active, str):
        try:
            if _parse_time(now) - _parse_time(last_active) > timedelta(days=30):
                return "expired"
        except ValueError:
            pass
    return None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _string_list(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _append_unique(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]


def _slot_label(slot: str) -> str:
    return {
        "pain_point": "客户要解决的核心问题",
        "plant_count": "客户的使用数量",
    }.get(slot, slot)
