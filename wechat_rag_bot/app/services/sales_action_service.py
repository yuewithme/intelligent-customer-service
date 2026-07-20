from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.sales_flow import SalesStage
from app.schemas.state import UserState
from app.services.sales_stage_service import normalize_sales_stage


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
    profile_opportunity = (
        profile.get("active_opportunity", {}) if isinstance(profile, dict) else {}
    )
    opportunity = user_state.metadata.get("active_opportunity") or profile_opportunity
    known_slots = {
        **_dict_value(opportunity.get("slots")),
        **{
            key: value
            for key, value in intent.slots.items()
            if key not in INTERNAL_SLOTS and value not in (None, "", [])
        },
    }
    asked_slots = set(_string_list(opportunity.get("asked_slots")))
    stage = normalize_sales_stage(intent.sales_stage)
    if stage == "unknown":
        stage = normalize_sales_stage(user_state.sales_stage)

    priority = _priority_action(
        intent.primary_intent,
        intent.need_human,
        intent.route,
        set(intent.sales_signals),
    )
    if priority:
        goal, action, signal = priority
        return _decision(
            goal,
            action,
            known_slots,
            customer_signal=signal,
            reason="intent_priority",
        )

    if stage == SalesStage.CLOSING.value:
        return _decision("针对当前阻碍推进真实下单", "resolve_blocker", known_slots)
    if stage == SalesStage.TRIAL_CLOSE.value:
        return _decision("给出可信方案并确认客户购买意愿", "trial_close", known_slots)
    if stage == SalesStage.VALUE_BUILT.value:
        return _decision("说明方案价值并确认客户是否认可", "build_value", known_slots)
    if stage == SalesStage.SOLUTION_RECOMMENDED.value:
        return _decision("基于客户信息推荐合适方案", "recommend_solution", known_slots)
    if stage == SalesStage.PAIN_DISCOVERY.value:
        return _decision("确认客户最想解决的问题", "discover_pain", known_slots)
    if stage in {"unknown", SalesStage.RAPPORT.value} and not known_slots:
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
        sales_action="discover_need_track",
        required_slots=[question_slot] if question_slot else [],
        question_slot=question_slot,
        next_stage=SalesStage.PAIN_DISCOVERY.value,
        known_slots=known_slots,
        customer_signal="interested",
    )


def apply_sales_action(
    reply: FinalReply,
    decision: SalesActionDecision,
) -> FinalReply:
    if reply.reply_type != "template" or not decision.question_slot:
        return reply
    if decision.question_slot == "pain_point":
        return reply
    question = {
        "plant_count": "您大概有多少盆需要使用？",
    }.get(decision.question_slot)
    if not question or question in reply.answer:
        return reply
    bridge = {
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
    stage_decision: dict | None = None,
    now: str | None = None,
) -> dict:
    timestamp = now or datetime.now(timezone.utc).isoformat()
    opportunity = dict(current) if isinstance(current, dict) else {}
    known_slots = dict(_dict_value(sales_action.get("known_slots")))
    stage_update = _dict_value(stage_decision)
    decision_blocker = known_slots.pop("decision_blocker", None)
    if (
        sales_action.get("sales_action") in NON_SALES_ACTIONS
        and sales_action.get("customer_signal", "none") == "none"
        and not known_slots
    ):
        if not opportunity or opportunity.get("status") in {"won", "lost", "expired"}:
            return opportunity
    replace_reason = _replace_reason(opportunity, known_slots, timestamp)
    if not opportunity.get("opportunity_id") or replace_reason:
        old_id = opportunity.get("opportunity_id")
        opportunity = {
            "opportunity_id": f"opp_{uuid4().hex}",
            "kind": "first_order",
            "status": "active",
            "slots": {},
            "asked_slots": [],
            "started_at": timestamp,
        }
        if old_id:
            opportunity["replaced_opportunity_id"] = old_id
            opportunity["replace_reason"] = replace_reason

    opportunity["slots"] = {**_dict_value(opportunity.get("slots")), **known_slots}
    if isinstance(decision_blocker, dict):
        opportunity["decision_blocker"] = decision_blocker
    decided_stage = stage_update.get("stage") or sales_stage
    normalized_stage = normalize_sales_stage(decided_stage)
    opportunity["sales_stage"] = normalized_stage
    opportunity["current_stage"] = normalized_stage
    previous_stage = normalize_sales_stage(stage_update.get("previous_stage"))
    if previous_stage != "unknown":
        opportunity["previous_stage"] = previous_stage
    if stage_update.get("reason"):
        opportunity["stage_reason"] = stage_update["reason"]
    if stage_update.get("transition_type"):
        opportunity["transition_type"] = stage_update["transition_type"]
    if isinstance(stage_update.get("evidence"), list):
        opportunity["stage_evidence"] = stage_update["evidence"]
    if isinstance(stage_update.get("signals"), list):
        opportunity["signals"] = _merge_strings(
            _string_list(opportunity.get("signals")),
            _string_list(stage_update["signals"]),
        )
    stage_status = stage_update.get("opportunity_status")
    if stage_status in {"active", "won", "lost", "paused", "expired"}:
        opportunity["status"] = stage_status
    interruption = stage_update.get("interruption")
    if isinstance(interruption, dict):
        opportunity["interruption"] = interruption
    elif stage_update.get("transition_type") == "resume":
        opportunity.pop("interruption", None)
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
    if signal in {"ready_to_buy", "purchased"}:
        opportunity.pop("decision_blocker", None)
    if signal in {"purchased", "rejected"}:
        opportunity["status"] = "won" if signal == "purchased" else "lost"
        opportunity["closed_at"] = timestamp
        opportunity["close_reason"] = signal
    elif opportunity.get("status") in {"won", "lost", "expired"}:
        opportunity["closed_at"] = timestamp
        opportunity["close_reason"] = stage_update.get("reason") or opportunity["status"]
    if opportunity.get("status") in {"won", "lost", "expired"}:
        opportunity["asked_slots"] = []
        opportunity.pop("decision_blocker", None)
        opportunity["recommended_product_ids"] = []
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


def _priority_action(
    intent: str,
    need_human: bool,
    route: str,
    signals: set[str],
):
    if need_human or route == "human":
        return ("转交人工继续处理", "handoff_to_human", "none")
    if intent in AFTER_SALE_INTENTS:
        return ("优先解决客户售后问题", "provide_service", "none")
    if "purchased" in signals:
        return ("确认成交结果并完成服务交接", "close_order", "purchased")
    if intent in PURCHASED_INTENTS or "payment_claimed" in signals:
        return ("核验订单或支付状态，暂不标记成交", "close_order", "payment_claimed")
    if intent in REJECTED_INTENTS:
        return ("尊重客户决定并结束本次销售推进", "resolve_blocker", "rejected")
    if intent in ORDER_INTENTS:
        return ("确认订单必要信息并推进成交", "close_order", "ready_to_buy")
    if intent in OBJECTION_INTENTS:
        return ("回应当前异议并确认客户是否接受", "resolve_blocker", "objection")
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


def _merge_strings(current: list[str], incoming: list[str]) -> list[str]:
    result = list(current)
    for value in incoming:
        if value not in result:
            result.append(value)
    return result


def _slot_label(slot: str) -> str:
    return {
        "pain_point": "客户要解决的核心问题",
        "plant_count": "客户的使用数量",
    }.get(slot, slot)
