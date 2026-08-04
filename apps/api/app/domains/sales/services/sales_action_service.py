import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.sales.schemas.sales_flow import SalesStage
from app.domains.sales.services.sales_stage_catalog import get_sales_stage_definition
from app.domains.sales.services.service_need_service import (
    has_resolved_service_need,
)
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

    if intent.primary_goal == "request_material":
        if known_slots.get("material_request_phase") == "delivery":
            return _decision(
                "直接发送客户已经确认领取的养兰资料，不再重复挖需；"
                "不要被上一轮商品或会员上下文带偏，不得主动谈价格或优惠",
                "provide_service",
                known_slots,
                customer_signal="interested",
                reason="material_request_priority",
            )
        return SalesActionDecision(
            reply_goal=(
                "先回应客户领取养兰资料的请求，再只用一个自然、具体的问题了解客户"
                "目前最想解决的养兰痛点；不要被上一轮商品或会员上下文带偏，"
                "不得主动谈价格、优惠或不能再优惠"
            ),
            sales_action="discover_pain",
            required_slots=["pain_point"],
            question_slot="pain_point",
            known_slots=known_slots,
            customer_signal="interested",
            reason="material_request_priority",
        )

    soft_service_decline = _service_offer_started(
        opportunity, known_slots
    ) and _is_soft_service_decline(intent)
    explicit_non_membership_turn = (
        intent.primary_domain in {"care", "conversation"}
        or intent.primary_goal in {"request_material", "socialize"}
    )
    membership_context = (
        not soft_service_decline
        and not explicit_non_membership_turn
        and (
            intent.slots.get("product_request_kind") == "membership"
            or (
                user_state.metadata.get("commerce_last_product_kind") == "membership"
                and bool(user_state.metadata.get("commerce_last_product_id"))
                and (
                    intent.primary_domain in {"product", "commerce"}
                    or intent.primary_intent in OBJECTION_INTENTS | ORDER_INTENTS
                )
            )
        )
    )
    if membership_context:
        membership_kind = str(
            intent.slots.get("membership_question_kind")
            or (
                "objection"
                if intent.primary_intent
                in {"price_objection", "discount_request", "hesitation"}
                else "capability"
            )
        )
        is_membership_objection = (
            membership_kind == "objection"
            or intent.primary_intent
            in {"price_objection", "discount_request", "hesitation"}
        )
        repeated_membership_objection = is_membership_objection and (
            _has_prior_price_objection(user_state.metadata.get("recent_turns"))
        )
        if is_membership_objection:
            return _decision(
                (
                    "客户已经连续询价，先自然接住，再根据已核实事实直接说明首单体验价不能再优惠；"
                    "不要重复课程和一对一指导，不追问预算，最后明确引导客户点击已发送卡片开通"
                    if repeated_membership_objection
                    else "先自然接住价格顾虑，再结合已核实的首单体验价和会员权益说明价值；"
                    "明确回答不能再优惠，不说空话、不追问预算，最后推进客户点击卡片开通"
                ),
                "close_order",
                known_slots,
                customer_signal="objection",
                reason="membership_objection_close_priority",
            )
        if membership_kind == "capability":
            return _decision(
                "直接回答陪伴养兰服务是什么，结合已核实权益说明它如何帮助客户少走弯路；"
                "可以发送真实商品卡片推进开通，但客户本轮没有议价，不得主动提优惠、"
                "不能再优惠或价格异议",
                "answer_current_question",
                known_slots,
                customer_signal="interested",
                reason="membership_capability_priority",
            )
        if membership_kind == "price":
            return _decision(
                "直接说明陪伴养兰服务的当前价格和对应价值，并引导客户点击卡片开通；"
                "客户本轮只是询价，没有议价，不得主动说不能再优惠或不能再少",
                "answer_current_question",
                known_slots,
                customer_signal="interested",
                reason="membership_price_priority",
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

    if soft_service_decline:
        return _decision(
            (
                "客户是礼貌回应、犹豫或软拒绝，不能用‘不客气’结束对话。"
                "直接进入陪伴养兰服务的塑品阶段，按照‘同行差异、同类兰友人群、产品具体交付’"
                "三层语义分开说：说清不是卖完就不管，很多兰友也有类似痛点，"
                "以及单品养护资料包含收苗处理上盆、浇水、施肥、防病害、花期管理和分株，"
                "再由老师结合客户的实际操作一对一指导。最后发送真实的陪伴养兰商品卡推进成交；"
                "不得把软拒绝当成明确终止，不得主动谈优惠或不能再优惠"
            ),
            "build_value",
            known_slots,
            customer_signal="objection",
            reason="service_offer_soft_decline_value_card",
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
        service_need_is_clear = has_resolved_service_need(known_slots)
        should_probe_pain = not service_need_is_clear
        service_offer_started = _service_offer_started(opportunity, known_slots)
        if service_need_is_clear and stage in {
            SalesStage.SOLUTION_RECOMMENDED.value,
            SalesStage.VALUE_BUILT.value,
            SalesStage.TRIAL_CLOSE.value,
            SalesStage.CLOSING.value,
        }:
            return SalesActionDecision(
                reply_goal=(
                    (
                        "先用一条消息回答客户当前的养护问题，说清可能原因和可执行处理；"
                        "再用独立消息结合这个需求继续介绍陪伴养兰服务，说清资料和视频负责理顺基础，"
                        "老师再结合家里的环境和实际操作一对一带着调整；不得只做免费问答后结束"
                        if service_offer_started
                        else
                        "先用一条消息专业回答客户已经说清的养兰需求，给出有针对性的答复或可执行处理；"
                        "需求刚明确的这一轮只做自然承接：结合客户的地区、品种和具体痛点，"
                        "参考对应痛点话术先共情，再简单介绍我们萧岚苑会陪兰友把养护思路理顺、"
                        "结合实际环境调整。不要展开课程和权益，不发商品卡；等客户继续互动后再进入详细推品塑品"
                    )
                ),
                sales_action=("build_value" if service_offer_started else "recommend_solution"),
                known_slots=known_slots,
                customer_signal="interested",
                reason=(
                    "service_solution_question_followup"
                    if service_offer_started
                    else "service_solution_first_offer"
                ),
                allow_diagnostic_question=False,
                prohibited_behaviors=[
                    "需求明确且已回答后仍停留在挖需阶段",
                    "只回答养护问题而不推进陪伴养兰服务",
                    "需求刚明确就一次性讲完全部服务权益或发送商品卡",
                    "客户没有明确购兰需求时推荐兰花",
                ],
            )
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
    if stage in {SalesStage.NEED_DISCOVERY.value, SalesStage.PAIN_DISCOVERY.value}:
        actionable_missing = (
            ["pain_point"]
            if not has_resolved_service_need(known_slots)
            and "pain_point" not in asked_slots
            else []
        )
    else:
        missing = _most_relevant_missing_slots(definition, known_slots)
        actionable_missing = [slot for slot in missing if slot not in asked_slots]
    question_slot = actionable_missing[0] if actionable_missing else None
    if question_slot:
        if question_slot == "pain_point":
            if intent.primary_intent == "profile_answer":
                goal = (
                    "先用一句话客观确认客户本轮提供的地区、养兰数量和品种，只复述"
                    "已识别事实，不评价氛围、品位或感觉；再只问一个具体的养兰痛点问题，"
                    "从黄叶、黑斑、烂根、腐苗、不开花等常见表现中自然选择一到三个方向"
                )
            else:
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
    if reply.reply_type in {"fixed_resource", "fixed_workflow"}:
        return reply
    persona = reply.metadata.get("persona")
    if isinstance(persona, dict) and persona.get("sales_action_rendered") is True:
        return reply
    if reply.need_human or not reply.answer.strip() or not decision.question_slot:
        return reply
    pain_discovery_required = (
        decision.sales_action == "discover_pain"
        and decision.question_slot == "pain_point"
    )
    if _contains_slot_question(
        reply.answer,
        decision.question_slot,
    ) and not (
        pain_discovery_required
        and _violates_pain_discovery_flow(reply.answer)
    ):
        return reply.model_copy(
            update={
                "metadata": {
                    **reply.metadata,
                    "emitted_question_slot": decision.question_slot,
                }
            }
        )
    if pain_discovery_required:
        return _enforce_pain_discovery(reply, decision=decision)
    # Other missing slots remain guidance rather than mandatory questions. Pain
    # discovery is stricter because recommending before the need is clear breaks
    # the service-first sales flow.
    return reply


_PAIN_DISCOVERY_FALLBACKS = (
    "养兰能不能养稳，关键还是要先看具体卡在哪里。您现在比较头疼的是黄叶黑斑，还是烂根、腐苗这类问题？",
    "先不急着选品种，我想先了解您平时养护最难的地方。您手上的兰花更常见黄叶焦尖，还是烂根腐苗？",
    "品种只是一个方面，日常养护里的问题没找准，后面还是容易反复。您目前遇到更多的是黑斑黄叶，还是烂根、腐苗？",
)
_DISCOVERY_PRODUCT_PUSH_MARKERS = (
    "为您挑",
    "帮您挑",
    "给您挑",
    "推荐品种",
    "推荐几款",
    "购买",
    "下单",
)
_DISCOVERY_PREMATURE_RECOMMENDATION_MARKERS = (
    "适合新手的品种",
    "品种开始看",
    "开始选品种",
    "挑品种",
    "推荐品种",
    "推荐几款",
)
_DISCOVERY_NARROW_DIAGNOSTIC_MARKERS = (
    "浇水",
    "植料",
    "施肥",
    "摆放",
    "室内",
    "阳台",
    "通风",
)


def _violates_pain_discovery_flow(text: str) -> bool:
    if any(marker in text for marker in _DISCOVERY_PREMATURE_RECOMMENDATION_MARKERS):
        return True
    return _contains_question(text) and any(
        marker in text for marker in _DISCOVERY_NARROW_DIAGNOSTIC_MARKERS
    )


def _enforce_pain_discovery(
    reply: FinalReply,
    *,
    decision: SalesActionDecision,
) -> FinalReply:
    answer = reply.answer.strip()
    has_commerce_card = any(
        message.type in {"link_card", "mini_program"}
        for message in reply.outbound_messages
    )
    should_replace = _contains_question(answer) or any(
        marker in answer for marker in _DISCOVERY_PRODUCT_PUSH_MARKERS
    )
    profile_fallback = _profile_pain_discovery_fallback(decision.known_slots)
    if (
        not should_replace
        and not has_commerce_card
        and reply.reply_type != "llm_fallback"
        and not profile_fallback
    ):
        return reply
    fallback = profile_fallback or (
        _PAIN_DISCOVERY_FALLBACKS[
            sum(ord(character) for character in answer)
            % len(_PAIN_DISCOVERY_FALLBACKS)
        ]
    )
    corrected = (
        fallback
        if should_replace or has_commerce_card or profile_fallback
        else f"{answer.rstrip('。')}。{fallback}"
    )

    outbound_messages: list[OutboundMessage] = []
    text_added = False
    for message in reply.outbound_messages:
        if message.type == "text":
            if not text_added:
                outbound_messages.append(OutboundMessage(type="text", content=corrected))
                text_added = True
            continue
        if message.type in {"link_card", "mini_program"}:
            continue
        outbound_messages.append(message)
    if reply.outbound_messages and not text_added:
        outbound_messages.insert(0, OutboundMessage(type="text", content=corrected))

    return reply.model_copy(
        update={
            "answer": corrected,
            "answer_segments": [corrected],
            "outbound_messages": outbound_messages,
            "metadata": {
                **reply.metadata,
                "emitted_question_slot": "pain_point",
                "sales_flow_guard": "pain_discovery_required",
            },
        }
    )


def _profile_pain_discovery_fallback(known_slots: dict) -> str:
    region = str(known_slots.get("region") or "").strip()
    plant_count = known_slots.get("plant_count")
    varieties = known_slots.get("owned_varieties")
    variety_text = "、".join(
        str(item).strip()
        for item in (varieties if isinstance(varieties, list) else [])
        if str(item).strip()
    )
    if not any((region, plant_count not in (None, ""), variety_text)):
        return ""
    if region and plant_count not in (None, "") and variety_text:
        acknowledgement = f"了解了，您在{region}养了{plant_count}盆{variety_text}。"
    elif plant_count not in (None, "") and variety_text:
        acknowledgement = f"了解了，您现在养了{plant_count}盆{variety_text}。"
    elif region and variety_text:
        acknowledgement = f"了解了，您在{region}主要养{variety_text}。"
    elif plant_count not in (None, ""):
        acknowledgement = f"了解了，您现在养了{plant_count}盆兰花。"
    elif region:
        acknowledgement = f"了解了，您在{region}养兰。"
    else:
        acknowledgement = f"了解了，您现在主要养{variety_text}。"
    return acknowledgement + "您现在养护上最困扰的是黄叶、烂根，还是一直不开花？"


def _contains_question(text: str) -> bool:
    return "？" in text or "?" in text or bool(QUESTION_LANGUAGE_PATTERN.search(text))


_PRICE_OBJECTION_MARKERS = (
    "贵",
    "便宜",
    "优惠",
    "打折",
    "少一点",
    "少点",
    "降一点",
    "降点",
    "再少",
    "再低",
)


def _has_prior_price_objection(value) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        isinstance(turn, dict)
        and str(turn.get("role") or "") in {"user", "customer"}
        and any(
            marker
            in str(turn.get("content") or turn.get("text") or "")
            for marker in _PRICE_OBJECTION_MARKERS
        )
        for turn in value[-6:]
    )


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
    action_reason = str(sales_action.get("reason") or "")
    if action_reason == "service_solution_first_offer":
        opportunity["service_offer_phase"] = "introduced"
    elif action_reason == "service_solution_question_followup":
        opportunity["service_offer_phase"] = "deepened"
    elif action_reason == "service_offer_soft_decline_value_card":
        opportunity["service_offer_phase"] = "card_sent"
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


def _service_offer_started(opportunity: dict, known_slots: dict) -> bool:
    if not has_resolved_service_need(known_slots):
        return False
    return str(opportunity.get("service_offer_phase") or "") in {
        "introduced",
        "deepened",
        "card_sent",
    } or str(opportunity.get("last_sales_action") or "") in {
        "recommend_solution",
        "build_value",
    }


def _is_soft_service_decline(intent: IntentResult) -> bool:
    slots = intent.slots if isinstance(intent.slots, dict) else {}
    return bool(
        slots.get("chitchat_kind") == "thanks"
        or slots.get("rejection_kind") == "polite_decline"
        or intent.primary_intent == "hesitation"
        or intent.primary_goal == "defer_decision"
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
