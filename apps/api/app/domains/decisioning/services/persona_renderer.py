import json
import re

from app.core.config import get_settings
from app.domains.decisioning.schemas.persona import PersonaContext, ReplySpec
from app.domains.decisioning.services.customer_reply_formatter import plain_customer_text
from app.integrations.ai.services.llm_service import generate_messages, get_model_config
from app.domains.decisioning.services.persona_service import persona_system_prompt
from app.domains.decisioning.services.reply_guard_service import (
    persona_extension_violation,
)


async def render_persona_reply(
    *,
    spec: ReplySpec,
    context: PersonaContext,
    current_message: str,
) -> ReplySpec:
    metadata = {
        **spec.metadata,
        "persona": {
            "persona_id": context.persona_id,
            "version": context.persona_version,
            "mode": context.mode,
            "render_mode": spec.render_mode,
        },
    }
    if spec.render_mode != "persona" or not get_settings().persona_reply_enabled:
        return spec.model_copy(update={"metadata": metadata})
    if get_model_config("persona").provider == "mock":
        return spec.model_copy(
            update={
                "metadata": metadata,
            }
        )

    tool_state = spec.verified_facts.get("tool_state")
    commerce_type = (
        str(tool_state.get("commerce_type") or "")
        if isinstance(tool_state, dict)
        else ""
    )
    if spec.composition_mode == "anchor_plus_persona":
        if commerce_type == "product":
            extension_scope = (
                "第二条只做不含新事实的点击/查看商品卡片引导，"
                "不要解释购买好处、优惠或价值；"
            )
        elif commerce_type == "order":
            extension_scope = (
                "第二条只做不含新事实的自然承接，不得判断订单、付款、"
                "物流、发货或系统同步状态；"
            )
        else:
            extension_scope = ""
        composition_instruction = (
            "business_anchor 会作为第一条消息原样发送。你只生成紧接其后的"
            "第二条自然消息，不要复述、改写或总结 business_anchor，也不要新增"
            "商品卖点、价格、库存、权益、服务、时效或其他业务事实。"
            f"{extension_scope}question_slot 为空时不要询问客户是否合适。"
        )
    else:
        composition_instruction = ""
    question_instruction = (
        "question_slot 为 pain_point 时，只问一个具体自然的问题，"
        "结合上下文从黑斑、黄叶、腐苗、烂根、焦尖、不开花等表现中"
        "灵活选一到三个方向，每次更换选择和说法，不得照抄示例或固定话术；"
        "不要问想要服务还是产品，也不要同时追问植料、浇水、地区。"
        if spec.question_slot == "pain_point"
        else ""
    )
    profile_ack_instruction = (
        "customer_profile_facts 是客户本轮刚提供并已识别的信息。第一句只用这些事实做"
        "简短客观确认，例如确认所在地区、数量和品种；不得评价有氛围、有感觉、"
        "有品位、很惬意或随意补充养护判断。然后再完成 question_slot 对应的问题。"
        if spec.verified_facts.get("customer_profile_facts")
        else ""
    )
    rag_instruction = (
        "grounded_knowledge_answer 是已经知识库校验的原始答案。"
        "必须保留其核心结论、必要条件和风险边界，只做人格化改写、"
        "精简和分消息，不得新增事实或改变原意。"
        if spec.reply_type == "rag"
        else ""
    )
    brand_instruction = (
        "verified_facts 中存在 brand_value_facts 时，不要另起广告话题。"
        "先用第一条消息回答客户当前问题并给一个关键建议；如果确实需要体现品牌价值，"
        "第二条必须用因果关系承接前面的具体判断。代表品牌说话时用‘我们萧岚苑’或‘我们这边’，"
        "不要用第三方介绍口吻说‘萧岚苑提供’。优先从 service_value_points 中只选择与当前痛点"
        "最相关的一项，用‘能帮您’‘让您’这类客户收益表达，说明我们为什么能帮助减少问题反复；"
        "可以自然表达‘能够帮您有效避开反复踩坑’，但不要照抄成固定话术，也不要同时罗列"
        "视频课程、一对一指导或其他权益。"
        if spec.verified_facts.get("brand_value_facts")
        else ""
    )
    objection_instruction = ""
    if (
        isinstance(tool_state, dict)
        and tool_state.get("membership_question_kind") == "objection"
    ):
        if tool_state.get("membership_objection_round") == "followup":
            objection_instruction = (
                "这是客户连续第二次询价。必须先自然接住，再根据 verified_facts 直接说明首单体验价"
                "目前不能再优惠。不要重复课程、一对一指导或整段价值介绍；"
                "最后必须明确引导客户点击已经发送的商品卡片开通，推进成交。"
            )
        else:
            objection_instruction = (
                "这是本轮首次处理价格顾虑。先自然接住，再根据 verified_facts 明确说明首单体验价"
                "目前不能再优惠，只用一句客户能感受到的价值解释；"
                "最后明确引导客户点击商品卡片开通，不追问预算。"
            )
    payload = {
        "customer_message": current_message,
        "reply_spec": {
            "reply_goal": spec.reply_goal,
            "must_include": spec.must_include,
            "optional_points": spec.optional_points,
            "verified_facts": spec.verified_facts,
            "question_slot": spec.question_slot,
            "prohibited_claims": spec.prohibited_claims,
            "business_anchor": (
                spec.suggested_copy
                if spec.composition_mode == "anchor_plus_persona"
                else ""
            ),
            "suggested_copy": (
                spec.suggested_copy if spec.composition_mode == "replace" else ""
            ),
        },
        "relationship_state": context.relationship_state,
        "relevant_memories": context.relevant_memories,
        "style_examples": context.examples,
    }
    messages = [
        {"role": "system", "content": persona_system_prompt(context)},
        {
            "role": "user",
            "content": (
                f"请依据下列数据生成这一轮的微信客户回复。{composition_instruction}"
                f"{question_instruction}{profile_ack_instruction}{rag_instruction}{brand_instruction}"
                f"{objection_instruction}"
                "先完成 reply_goal。question_slot 有值时，只能自然追问该项，"
                "不要顺带询问相邻信息；question_slot 为空时，不得出现问句，"
                "也不得用命令句索要手机号、订单号、图片或其他资料。"
                "suggested_copy 只供参考表达，不是事实来源。不得输出字段名，"
                "不得增加 verified_facts 之外的商品、价格、库存、订单、物流、"
                "优惠、服务能力或时效事实。默认总共不超过两条消息："
                "第一条直接回答并给一个关键建议，第二条只放必要的自然承接；"
                "每条1到2句、约60字。不同消息之间必须用一个空行分隔，"
                "同一语义不要机械拆开。\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]
    result = await generate_messages(
        messages,
        purpose="persona",
        temperature=get_settings().persona_reply_temperature,
    )
    answer = _plain_persona_answer(str(result.get("answer") or ""))
    answer = _apply_approved_care_brand_script(
        spec=spec,
        answer=answer,
        current_message=current_message,
    )
    violation = _candidate_violation(spec, answer)
    if violation:
        product_extension = _is_product_extension(spec)
        retry = await generate_messages(
            [
                *messages,
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": (
                        f"上一条违反输出合同（{violation}）。请重新生成一次。"
                        + (
                            "不要解释错误；只输出客户可见的第二条消息。"
                            + _retry_contract(spec)
                            if product_extension
                            else "不要解释错误；请完整重写本轮客户可见回复。"
                            + _full_reply_retry_contract(spec, violation)
                        )
                    ),
                },
            ],
            purpose="persona",
            temperature=0.1,
        )
        answer = _plain_persona_answer(str(retry.get("answer") or ""))
        answer = _apply_approved_care_brand_script(
            spec=spec,
            answer=answer,
            current_message=current_message,
        )
        result = {
            **retry,
            "usage": _merge_usage(result.get("usage") or {}, retry.get("usage")),
        }
        metadata["persona"]["retried"] = True
        metadata["persona"]["retry_reason"] = violation
    if not answer:
        return spec.model_copy(update={"metadata": metadata})
    metadata["persona"]["rendered"] = True
    metadata["persona"]["sales_action_rendered"] = bool(spec.question_slot)
    update = {
        "usage": _merge_usage(spec.usage, result.get("usage")),
        "metadata": metadata,
    }
    if spec.composition_mode == "anchor_plus_persona":
        update["persona_copy"] = answer
    else:
        update["suggested_copy"] = answer
        update["answer_segments"] = []
    return spec.model_copy(
        update=update
    )


def _plain_persona_answer(text: str) -> str:
    paragraphs = re.split(r"\r?\n\s*\r?\n+", str(text or ""))
    cleaned = [plain_customer_text(paragraph).strip() for paragraph in paragraphs]
    return "\n\n".join(paragraph for paragraph in cleaned if paragraph)


def _apply_approved_care_brand_script(
    *,
    spec: ReplySpec,
    answer: str,
    current_message: str,
) -> str:
    if not answer.strip():
        return answer
    action_context = spec.verified_facts.get("sales_action_context")
    action_context = action_context if isinstance(action_context, dict) else {}
    reason = str(action_context.get("reason") or "")
    script_name = {
        "service_solution_first_offer": "care_pain",
        "service_solution_question_followup": "care_question_followup",
        "service_offer_soft_decline_value_card": "soft_decline_value",
    }.get(reason)
    if script_name is None:
        return answer
    script_names = [script_name]
    if script_name == "care_pain":
        category = _care_pain_script_category(
            " ".join(
                (
                    current_message,
                    str(action_context.get("service_need") or ""),
                )
            )
        )
        if category:
            script_names.insert(0, f"care_pain_{category}")
    facts = spec.verified_facts.get("brand_value_facts")
    if not isinstance(facts, list):
        facts = []
    script = ""
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        scripts = fact.get("approved_sales_scripts")
        if isinstance(scripts, dict):
            script = next(
                (
                    str(scripts.get(name) or "").strip()
                    for name in script_names
                    if str(scripts.get(name) or "").strip()
                ),
                "",
            )
        if script:
            break
    if not script:
        tool_state = spec.verified_facts.get("tool_state")
        tool_state = tool_state if isinstance(tool_state, dict) else {}
        scripts = tool_state.get("approved_sales_scripts")
        if isinstance(scripts, dict):
            script = next(
                (
                    str(scripts.get(name) or "").strip()
                    for name in script_names
                    if str(scripts.get(name) or "").strip()
                ),
                "",
            )
    if not script:
        return answer
    service_need = next(
        (
            f"{marker}这类问题"
            for marker in (
                "烂根",
                "腐苗",
                "黑斑",
                "黄叶",
                "焦尖",
                "不开花",
            )
            if marker in current_message
        ),
        "",
    )
    if not service_need:
        known_need = str(
            action_context.get("service_need")
            or action_context.get("pain_point")
            or ""
        ).strip()
        need_kind = str(action_context.get("service_need_kind") or "")
        if known_need and need_kind == "desired_outcome":
            service_need = f"{known_need}这个目标"
        elif known_need and need_kind == "failed_history":
            service_need = f"{known_need}这段经历"
        elif known_need:
            service_need = (
                known_need
                if known_need.endswith("这类问题")
                else f"{known_need}这类问题"
            )
        else:
            service_need = "这类养护需求"
    bridge = (
        script.replace("{service_need}", service_need)
        .replace("{pain_point}", service_need)
        .replace("{profile_lead}", _customer_profile_lead(action_context))
    )
    if reason == "service_offer_soft_decline_value_card":
        return bridge
    body_parts = [
        part
        for part in re.split(r"(?<=[。！？!?])", answer)
        if not any(
            marker in part
            for marker in (
                "萧岚苑",
                "陪伴养兰",
                "一对一指导",
                "一对一服务",
                "服务过很多兰友",
            )
        )
    ]
    body = _plain_persona_answer("".join(body_parts))
    return "\n\n".join(part for part in (body, bridge) if part)


def _care_pain_script_category(text: str) -> str:
    value = str(text or "")
    for category, markers in (
        ("root_rot", ("烂根", "腐苗", "腐烂", "黑根")),
        ("black_spot", ("黑斑", "病害", "僵苗")),
        ("yellow_tip", ("焦尖", "黄叶", "叶黄")),
        ("no_bloom", ("不开花", "消苞", "不复花")),
        ("loss_fear", ("养死", "不敢买", "怕养不好", "养不好")),
    ):
        if any(marker in value for marker in markers):
            return category
    return ""


def _customer_profile_lead(action_context: dict) -> str:
    region = str(action_context.get("region") or "").strip()
    varieties = action_context.get("owned_varieties")
    if isinstance(varieties, list):
        variety = "、".join(
            str(item).strip() for item in varieties[:2] if str(item).strip()
        )
    else:
        variety = str(varieties or "").strip()
    if region and variety:
        return f"像您在{region}养的{variety}，"
    if variety:
        return f"像您养的{variety}，"
    if region:
        return f"您在{region}养兰，"
    return ""


def _candidate_violation(spec: ReplySpec, answer: str) -> str | None:
    if not answer:
        return "empty_persona_reply"
    candidate = (
        spec.model_copy(update={"persona_copy": answer})
        if spec.composition_mode == "anchor_plus_persona"
        else spec.model_copy(update={"suggested_copy": answer})
    )
    return persona_extension_violation(candidate, answer)


def _is_product_extension(spec: ReplySpec) -> bool:
    tool_state = spec.verified_facts.get("tool_state")
    return (
        spec.composition_mode == "anchor_plus_persona"
        and isinstance(tool_state, dict)
        and tool_state.get("commerce_type") == "product"
    )


def _retry_contract(spec: ReplySpec) -> str:
    if spec.question_slot:
        return (
            f"必须只自然追问“{spec.question_slot}”，使用一个问句并以问号结尾；"
            "不得顺带追问其他信息，也不得提优惠、权益、服务或价值。"
        )
    return (
        "只能用一句不超过40字的话引导客户点击或查看商品卡片，"
        "不得提优惠、权益、服务、价值或提出问题。"
    )


def _full_reply_retry_contract(spec: ReplySpec, violation: str) -> str:
    contracts = {
        "unexpected_question": (
            "question_slot 为空，本轮不要提出任何问题，也不要用‘吗、呢、怎么、什么’收尾；"
            "直接把判断、建议和必要的品牌承接自然说完。"
        ),
        "multiple_questions": "最多只能保留一个与 question_slot 完全对应的问题。",
        "missing_required_question": (
            f"结尾必须自然询问 question_slot 对应的‘{spec.question_slot}’，只问这一项。"
        ),
        "missing_discount_answer": "必须直接说明当前首单体验价不能再优惠。",
        "passive_sales_close": "不要让客户慢慢考虑，必须按 reply_goal 继续推进成交。",
        "missing_purchase_cta": "结尾必须明确引导客户点击已经发送的商品卡片开通。",
        "repeated_membership_value": "不要重复罗列课程、一对一指导或整段服务价值。",
        "hollow_profile_ack": (
            "第一句只客观确认 customer_profile_facts，不要评价氛围、感觉、品位或生活方式。"
        ),
    }
    correction = contracts.get(violation, "严格遵守 reply_goal 和输出合同。")
    return (
        f"{correction}保留 grounded_knowledge_answer 的核心结论和必要边界，"
        "只能使用 verified_facts 中已核实的品牌与服务事实。表达要像微信里真人在沟通，"
        "少用‘多因、导致、提供服务’这类报告口吻，优先说‘常见原因就是、容易、"
        "我们萧岚苑、能帮您’。总共不超过两条消息。"
    )


def _merge_usage(current: dict, extra) -> dict:
    merged = dict(current)
    if not isinstance(extra, dict):
        return merged
    for key, value in extra.items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] += value
        else:
            merged[key] = value
    return merged
