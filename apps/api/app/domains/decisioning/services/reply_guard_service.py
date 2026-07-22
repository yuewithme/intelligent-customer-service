import re

from app.domains.decisioning.schemas.persona import PersonaContext, ReplySpec
from app.domains.decisioning.schemas.reply import FinalReply


INTERNAL_MARKERS = {
    "question_slot",
    "reply_goal",
    "sales_action",
    "verified_facts",
    "prohibited_claims",
    "knowledge_base_ids",
    "prompt_block_ids",
    "用户画像",
    "知识库",
}
UNSOLICITED_REQUEST_PATTERNS = (
    re.compile(r"(?:你|您)?(?:把|将).{1,40}(?:发|告诉|提供|留给)我"),
    re.compile(r"(?:请|麻烦)(?:提供|发送|告知|填写|留下|留一下)"),
    re.compile(r"(?:手机号|订单号|地址|图片|照片).{0,12}(?:发我|给我|提供一下)"),
)


def guard_reply_spec(*, spec: ReplySpec, context: PersonaContext) -> ReplySpec:
    if spec.render_mode in {"locked", "silent"}:
        return spec
    answer = spec.suggested_copy.strip()
    fallback_reason = None
    if not answer:
        fallback_reason = "empty_persona_reply"
    elif _question_count(answer) > 1:
        fallback_reason = "multiple_questions"
    elif spec.question_slot is None and _question_count(answer):
        fallback_reason = "unexpected_question"
    elif spec.question_slot is None and any(
        pattern.search(answer) for pattern in UNSOLICITED_REQUEST_PATTERNS
    ):
        fallback_reason = "unsolicited_information_request"
    elif any(marker in answer for marker in INTERNAL_MARKERS):
        fallback_reason = "internal_marker"
    elif any(phrase in answer for phrase in context.anti_patterns):
        fallback_reason = "persona_anti_pattern"

    if fallback_reason is None:
        return spec
    original = str(spec.metadata.get("persona_original_copy") or "").strip()
    if not original:
        return spec.model_copy(
            update={
                "metadata": _guard_metadata(spec, "failed", fallback_reason),
            }
        )
    return spec.model_copy(
        update={
            "suggested_copy": original,
            "metadata": _guard_metadata(spec, "fallback", fallback_reason),
        }
    )


def _question_count(answer: str) -> int:
    return sum(answer.count(mark) for mark in ("?", "？"))


def finalize_reply_spec(spec: ReplySpec) -> FinalReply:
    outbound_messages = list(spec.outbound_messages)
    if spec.render_mode == "persona" and outbound_messages:
        outbound_messages = _replace_first_text(outbound_messages, spec.suggested_copy)
    metadata = dict(spec.metadata)
    metadata.pop("persona_original_copy", None)
    return FinalReply(
        answer=spec.suggested_copy,
        answer_segments=spec.answer_segments,
        outbound_messages=outbound_messages,
        reply_type=spec.reply_type,
        route=spec.route,
        template_id=spec.template_id,
        sources=spec.sources,
        usage=spec.usage,
        need_human=spec.need_human,
        next_action=spec.next_action,
        metadata=metadata,
    )


def _guard_metadata(spec: ReplySpec, status: str, reason: str) -> dict:
    metadata = dict(spec.metadata)
    persona = dict(metadata.get("persona") or {})
    persona["sales_action_rendered"] = False
    persona["accepted"] = False
    metadata["persona"] = persona
    metadata["persona_guard"] = {"status": status, "reason": reason}
    return metadata


def _replace_first_text(messages, answer: str):
    replaced = False
    updated = []
    for message in messages:
        if message.type == "text" and not replaced:
            updated.append(message.model_copy(update={"content": answer}))
            replaced = True
        else:
            updated.append(message)
    return updated
