import json

from app.config import get_settings
from app.schemas.persona import PersonaContext, ReplySpec
from app.services.customer_reply_formatter import plain_customer_text
from app.services.llm_service import generate_messages, get_model_config
from app.services.persona_service import persona_system_prompt


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

    payload = {
        "customer_message": current_message,
        "reply_spec": {
            "reply_goal": spec.reply_goal,
            "must_include": spec.must_include,
            "optional_points": spec.optional_points,
            "verified_facts": spec.verified_facts,
            "question_slot": spec.question_slot,
            "prohibited_claims": spec.prohibited_claims,
            "suggested_copy": spec.suggested_copy,
        },
        "relationship_state": context.relationship_state,
        "relevant_memories": context.relevant_memories,
        "style_examples": context.examples,
    }
    result = await generate_messages(
        [
            {"role": "system", "content": persona_system_prompt(context)},
            {
                "role": "user",
                "content": (
                    "请依据下列数据生成这一轮的微信客户回复。"
                    "优先完成 reply_goal；question_slot 有值时只自然追问该项，"
                    "不得输出字段名。不得增加 verified_facts 之外的业务事实。\n"
                    + json.dumps(payload, ensure_ascii=False, sort_keys=True)
                ),
            },
        ],
        purpose="persona",
        temperature=get_settings().persona_reply_temperature,
    )
    answer = plain_customer_text(str(result.get("answer") or "")).strip()
    if not answer:
        return spec.model_copy(update={"metadata": metadata})
    metadata["persona"]["rendered"] = True
    metadata["persona"]["sales_action_rendered"] = bool(spec.question_slot)
    return spec.model_copy(
        update={
            "suggested_copy": answer,
            "answer_segments": [],
            "usage": _merge_usage(spec.usage, result.get("usage")),
            "metadata": metadata,
        }
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
