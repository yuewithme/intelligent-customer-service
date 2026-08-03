import json

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
        "可用“有没有遇到黑斑、黄叶、腐苗等问题？”帮助客户表达；"
        "不要问想要服务还是产品，也不要同时追问植料、浇水、地区。"
        if spec.question_slot == "pain_point"
        else ""
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
                f"{question_instruction}"
                "先完成 reply_goal。question_slot 有值时，只能自然追问该项，"
                "不要顺带询问相邻信息；question_slot 为空时，不得出现问句，"
                "也不得用命令句索要手机号、订单号、图片或其他资料。"
                "suggested_copy 只供参考表达，不是事实来源。不得输出字段名，"
                "不得增加 verified_facts 之外的商品、价格、库存、订单、物流、"
                "优惠、服务能力或时效事实。\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]
    result = await generate_messages(
        messages,
        purpose="persona",
        temperature=get_settings().persona_reply_temperature,
    )
    answer = plain_customer_text(str(result.get("answer") or "")).strip()
    violation = _candidate_violation(spec, answer)
    if violation and _is_product_extension(spec):
        retry = await generate_messages(
            [
                *messages,
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": (
                        f"上一条违反输出合同（{violation}）。请重新生成一次。"
                        "不要解释错误；只输出客户可见的第二条消息。"
                        + _retry_contract(spec)
                    ),
                },
            ],
            purpose="persona",
            temperature=0.1,
        )
        answer = plain_customer_text(str(retry.get("answer") or "")).strip()
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
