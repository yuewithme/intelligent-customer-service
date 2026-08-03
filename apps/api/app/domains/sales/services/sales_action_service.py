import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.sales.schemas.sales_flow import SalesStage
from app.domains.sales.services.sales_stage_catalog import get_sales_stage_definition
from app.domains.customers.schemas.state import UserState
from app.domains.sales.services.sales_stage_service import normalize_sales_stage


INTERNAL_SLOTS = {"original_route", "sales_stage_reason"}
AFTER_SALE_INTENTS = {"ask_after_sale", "refund_request", "complaint"}
OBJECTION_INTENTS = {"price_objection", "discount_request", "hesitation", "trust_issue"}
ORDER_INTENTS = {"order_intent", "payment_intent"}
PURCHASED_INTENTS = {"order_completed", "payment_success"}
REJECTED_INTENTS = {"purchase_rejection", "not_interested"}
NON_SALES_ACTIONS = {"build_rapport", "provide_service", "handoff_to_human"}
LONG_TERM_OPPORTUNITY_SLOTS = {
    "region",
    "experience_level",
    "owned_varieties",
    "color_preference",
    "fragrance_preference",
    "difficulty_preference",
    "collection_preference",
}
GENERIC_PAIN_POINTS = {
    "",
    "养不好",
    "不会养",
    "不懂养护",
    "想学习",
    "怕养不好",
    "新手",
    "不清楚",
    "unknown",
}
QUESTION_LANGUAGE_PATTERN = re.compile(
    r"(?:请问|想问|是否|能否|方便(?:说|发|提供|告诉)|"
    r"可以.{0,8}吗|有没有|怎么|如何|为什么|多少|"
    r"哪(?:个|些|种|款|里|儿)?|什么|"
    r"合不合适|好不好|行不行|可不可以|要不要|愿不愿意|能不能)"
)


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
    stage_objective: str | None = None
    prohibited_behaviors: list[str] = Field(default_factory=list)
    allow_diagnostic_question: bool = False
    brand_value_facts: list[dict] = Field(default_factory=list)


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

    if intent.slots.get("product_request_kind") == "membership":
        membership_kind = str(
            intent.slots.get("membership_question_kind") or "capability"
        )
        if membership_kind in {"capability", "price"}:
            return _decision(
                "先准确回答客户当前询问的会员权益或价格，不主动追加购买动作",
                "answer_current_question",
                known_slots,
                customer_signal="interested",
                reason="membership_question_priority",
            )
        return _decision(
            "回答客户当前问题后提供真实会员购买入口",
            "close_order",
            known_slots,
            customer_signal="ready_to_buy",
            reason="membership_purchase_priority",
        )

    loop_reason = str(intent.slots.get("sales_stage_reason") or "")
    if loop_reason == "customer_value_concern" and stage == SalesStage.VALUE_BUILT.value:
        return _decision(
            "回到方案价值、信任或养护风险，解决客户当前顾虑",
            "build_value",
            known_slots,
            customer_signal="objection",
            reason="controlled_loop",
        )
    if loop_reason in {"recommendation_blocker", "recommendation_context_changed"}:
        return _decision(
            "重新核对客户条件并调整推荐方案",
            "recommend_solution",
            known_slots,
            customer_signal="objection",
            reason="controlled_loop",
        )

    if (
        intent.primary_intent in {"price_objection", "discount_request"}
        and not known_slots.get("budget")
    ):
        return SalesActionDecision(
            reply_goal="先回应价格顾虑，再确认客户能接受的预算范围",
            sales_action="resolve_blocker",
            required_slots=["budget"],
            question_slot="budget",
            known_slots=known_slots,
            customer_signal="objection",
            reason="intent_priority",
        )

    priority = _priority_action(
        intent.primary_intent,
        intent.need_human,
        intent.route,
        set(intent.sales_signals),
        intent.primary_domain,
        intent.primary_goal,
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

    if intent.primary_domain == "care" and intent.primary_goal != "request_material":
        pain_is_specific = _has_specific_pain(known_slots)
        should_probe_pain = not pain_is_specific and "pain_point" not in asked_slots
        return SalesActionDecision(
            reply_goal=(
                (
                    "先回应客户当前情况，再用一个具体、自然的引导问题了解养兰痛点，"
                    "结合上下文从黑斑、黄叶、腐苗、烂根、焦尖、不开花等常见表现中"
                    "灵活选择一到三个方向，不照抄固定句式；不要追问植料、浇水等单一"
                    "诊断方向，也不要问客户想要服务还是产品"
                )
                if should_probe_pain
                else (
                    "先结合客户已经说出的痛点做专业分析，讲清相关养护基础为什么会让"
                    "问题反复，再自然说明有人长期陪伴、结合实际情况指导的重要性；"
                    "不要继续追问植料、浇水或其他痛点，不要急着推荐兰花"
                )
            ),
            sales_action="discover_pain",
            required_slots=["pain_point"] if should_probe_pain else [],
            question_slot="pain_point" if should_probe_pain else None,
            known_slots=known_slots,
            customer_signal="interested",
            reason="care_expertise_first",
            allow_diagnostic_question=False,
            prohibited_behaviors=[
                "没有专业分析就连续追问",
                "追着客户未回答的植料、浇水或其他诊断问题反复询问",
                "客户已说出具体痛点后继续挖其他方向",
                "用服务还是产品、想达到什么效果等空泛二选一问题挖需",
                "把可能原因说成确定诊断",
                "客户没有明确购兰需求时优先推荐兰花",
            ],
        )

    definition = get_sales_stage_definition(stage)
    explicit_product_need = known_slots.get("need_track") == "product"
    action_by_stage = {
        SalesStage.RAPPORT.value: ("自然回应并了解客户来意", "build_rapport"),
        SalesStage.NEED_DISCOVERY.value: (
            "围绕养兰困难引导客户说出具体痛点，优先了解是否有黑斑、黄叶、腐苗等问题，"
            "不要让客户在服务和产品之间做空泛选择",
            "discover_pain",
        ),
        SalesStage.PAIN_DISCOVERY.value: (
            "围绕已明确痛点讲清养护基础和问题反复的原因，塑造有人陪伴养兰的重要性",
            "discover_pain",
        ),
        SalesStage.SOLUTION_RECOMMENDED.value: (
            "首单优先推荐已核实的陪伴养兰服务；只有客户明确表达购兰或选品需求时才推荐兰花",
            "recommend_solution",
        ),
        SalesStage.VALUE_BUILT.value: (
            "优先说明陪伴养兰服务如何减少反复试错，再说明其他已核实方案价值",
            "build_value",
        ),
        SalesStage.TRIAL_CLOSE.value: ("给出可信方案并确认客户购买意愿", "trial_close"),
        SalesStage.CLOSING.value: ("针对当前阻碍推进真实下单", "resolve_blocker"),
    }
    goal, action = action_by_stage.get(
        stage, ("自然回应并了解客户来意", "build_rapport")
    )
    if explicit_product_need and stage in {
        SalesStage.NEED_DISCOVERY.value,
        SalesStage.PAIN_DISCOVERY.value,
    }:
        goal = "回应客户已经明确的购兰或选品需求，不强行转问养护痛点"
        action = "discover_need_track"
    if stage in {SalesStage.NEED_DISCOVERY.value, SalesStage.PAIN_DISCOVERY.value}:
        actionable_missing = (
            ["pain_point"]
            if not explicit_product_need
            and not _has_specific_pain(known_slots)
            and "pain_point" not in asked_slots
            else []
        )
    else:
        missing = _most_relevant_missing_slots(definition, known_slots)
        actionable_missing = [slot for slot in missing if slot not in asked_slots]
    question_slot = actionable_missing[0] if actionable_missing else None
    if question_slot:
        if question_slot == "pain_point":
            goal = (
                "先自然回应客户当前消息，再只问一个具体的养兰痛点问题，"
                "结合客户已说的信息从常见症状中灵活选择一到三个方向试探，"
                "每次自然改写，不照抄固定句式"
            )
        else:
            goal = f"先回答客户当前问题，再确认{_slot_label(question_slot)}"
    return SalesActionDecision(
        reply_goal=goal,
        sales_action=action,
        required_slots=actionable_missing,
        question_slot=question_slot,
        next_stage=_next_stage(stage),
        known_slots=known_slots,
        customer_signal="interested" if action != "build_rapport" else "none",
        stage_objective=definition.objective if definition else None,
        prohibited_behaviors=list(definition.prohibited_behaviors) if definition else [],
    )


def apply_sales_action(
    reply: FinalReply,
    decision: SalesActionDecision,
) -> FinalReply:
    if reply.need_human or not reply.answer.strip() or not decision.question_slot:
        return reply
    if _contains_slot_question(
        reply.answer,
        decision.question_slot,
    ):
        return reply.model_copy(
            update={
                "metadata": {
                    **reply.metadata,
                    "emitted_question_slot": decision.question_slot,
                }
            }
        )
    # The sales decision describes what information would be useful next; it must
    # not overwrite a natural model question or force a canned question into
    # every reply. A later customer turn can still fill the missing slot.
    return reply


def _contains_question(text: str) -> bool:
    return "？" in text or "?" in text or bool(QUESTION_LANGUAGE_PATTERN.search(text))


_SLOT_QUESTION_MARKERS = {
    "budget": ("预算", "价位", "能接受"),
    "region": ("地区", "哪里", "哪儿", "城市"),
    "placement": ("室内", "阳台", "放在"),
    "color_preference": ("花色", "颜色"),
    "selected_product_id": ("倾向", "哪款", "哪个方案"),
    "selected_sku_id": ("规格", "几苗", "哪种套餐"),
    "quantity": ("多少", "几份", "数量"),
    "plant_count": ("多少盆", "几盆", "多少株"),
    "pain_point": ("黑斑", "黄叶", "腐苗", "烂根", "焦尖", "不开花", "养护问题"),
}


def _contains_slot_question(text: str, slot: str) -> bool:
    if not _contains_question(text):
        return False
    return any(marker in text for marker in _SLOT_QUESTION_MARKERS.get(slot, ()))


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
        stable_slots = {
            key: value
            for key, value in _dict_value(opportunity.get("slots")).items()
            if key in LONG_TERM_OPPORTUNITY_SLOTS
        }
        opportunity = {
            "opportunity_id": f"opp_{uuid4().hex}",
            "kind": "first_order",
            "status": "active",
            "slots": stable_slots,
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
    question_slot = sales_action.get("emitted_question_slot")
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


def _most_relevant_missing_slots(definition, known_slots: dict) -> list[str]:
    if definition is None or not definition.required_slot_groups:
        return []
    groups = [
        (index, [slot for slot in group if known_slots.get(slot) in (None, "", [])])
        for index, group in enumerate(definition.required_slot_groups)
    ]
    if any(not missing for _, missing in groups):
        return []
    return min(groups, key=lambda item: (len(item[1]), item[0]))[1]


def _has_specific_pain(known_slots: dict) -> bool:
    value = str(known_slots.get("pain_point") or "").strip().lower()
    if value in GENERIC_PAIN_POINTS:
        return False
    return not any(
        marker in value
        for marker in ("养不好", "不会养", "不懂", "想学习", "想学", "怕养不好", "新手")
    )


def _next_stage(stage: str) -> str | None:
    sequence = [item.value for item in SalesStage]
    if stage not in sequence:
        return SalesStage.RAPPORT.value
    index = sequence.index(stage)
    return sequence[index + 1] if index + 1 < len(sequence) else None


def _priority_action(
    intent: str,
    need_human: bool,
    route: str,
    signals: set[str],
    primary_domain: str | None = None,
    primary_goal: str | None = None,
):
    if need_human or route == "human":
        return ("转交人工继续处理", "handoff_to_human", "none")
    if primary_domain == "customer_service" and primary_goal == "request_service":
        return ("优先完成客户当前的订单服务任务", "provide_service", "none")
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
        "need_track": "需求方向",
        "desired_outcome": "期望结果",
        "pain_point": "客户要解决的核心问题",
        "plant_count": "客户的使用数量",
        "region": "所在地区",
        "placement": "摆放环境",
        "budget": "预算范围",
        "color_preference": "花色偏好",
        "selected_product_id": "倾向的方案",
        "selected_sku_id": "具体规格",
        "quantity": "购买数量",
        "decision_blocker": "当前成交顾虑",
    }.get(slot, slot)
