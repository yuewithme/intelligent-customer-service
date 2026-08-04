import re

from app.domains.decisioning.schemas.persona import PersonaContext, ReplySpec
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.services.customer_reply_formatter import (
    split_customer_messages,
)


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
UNVERIFIED_PERSONA_FACT_PATTERNS = (
    re.compile(
        r"(?:推荐|建议选|带花苞|气候|天气|香气|养护难度|库存|现货|"
        r"配套|课程|视频|在线答疑|权益|服务|指导|对接|赠送|包含|享受|锁定|"
        r"优惠|折扣|省钱|回本|专属|划算|订单|付款|支付|物流|发货|同步)"
    ),
    re.compile(r"\d+(?:\.\d+)?\s*元"),
)
QUESTION_LANGUAGE_PATTERN = re.compile(
    r"(?:请问|想问|是否|能否|方便(?:说|发|提供|告诉)|"
    r"可以.{0,8}吗|有没有|怎么|怎么样|如何|为什么|多少|"
    r"哪(?:个|些|种|款|里|儿)?|什么|"
    r"是.{1,12}还是.{1,12}|"
    r"合不合适|好不好|行不行|可不可以|要不要|愿不愿意|能不能|"
    r"吗(?:[啊呢呀吧])?(?:$|[，,。！!]))"
)
TRAILING_QUESTION_PATTERN = re.compile(
    r"([^。！!\n]*?(?:"
    r"吗(?:[啊呢呀吧])?|呢|什么|怎么|怎么样|如何|为什么|多少|"
    r"哪(?:个|些|种|款|里|儿)?|"
    r"是.{1,12}还是.{1,12}|"
    r"合不合适|好不好|行不行|可不可以|要不要|愿不愿意|能不能"
    r"))\s*[？?]?\s*$"
)
PRODUCT_EXTENSION_ACTION_MARKERS = (
    "卡片",
    "链接",
    "点开",
    "点击",
    "查看",
    "看看",
    "下单",
)
PRODUCT_CARD_ACTION_MARKERS = ("卡片", "链接", "点开", "点击", "下单", "购买入口")
NO_FURTHER_DISCOUNT_MARKERS = (
    "不能再少",
    "没法再少",
    "没办法再少",
    "没有再少",
    "不能再优惠",
    "没法再优惠",
    "没有再优惠",
    "不能再便宜",
    "没有再便宜",
    "不能往下调",
    "没有再往下调",
)
MEMBERSHIP_VALUE_LIST_MARKERS = ("课程", "视频", "一对一", "指导")


def guard_reply_spec(*, spec: ReplySpec, context: PersonaContext) -> ReplySpec:
    if spec.render_mode in {"locked", "silent"}:
        return spec
    answer = (
        spec.persona_copy
        if spec.composition_mode == "anchor_plus_persona"
        else spec.suggested_copy
    ).strip()
    fallback_reason = None
    if not answer:
        fallback_reason = "empty_persona_reply"
    elif violation := persona_extension_violation(spec, answer):
        fallback_reason = violation
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
    if spec.composition_mode == "anchor_plus_persona":
        return spec.model_copy(
            update={
                "persona_copy": "",
                "metadata": _guard_metadata(spec, "failed", fallback_reason),
            }
        )
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
    punctuation_count = sum(answer.count(mark) for mark in ("?", "？"))
    if punctuation_count:
        return punctuation_count
    return 1 if QUESTION_LANGUAGE_PATTERN.search(answer.strip()) else 0


def persona_extension_violation(spec: ReplySpec, answer: str) -> str | None:
    question_count = _question_count(answer)
    if question_count > 1:
        return "multiple_questions"
    if spec.question_slot is not None and question_count == 0:
        return "missing_required_question"
    if spec.question_slot is None and question_count:
        return "unexpected_question"
    if (
        spec.composition_mode == "anchor_plus_persona"
        and any(pattern.search(answer) for pattern in UNVERIFIED_PERSONA_FACT_PATTERNS)
    ):
        return "unverified_fact_claim"
    if _invalid_product_extension(spec, answer):
        return "invalid_product_extension"
    if _disallowed_product_card_action(spec, answer):
        return "unexpected_product_card_action"
    if violation := _membership_followup_objection_violation(spec, answer):
        return violation
    return None


def _membership_followup_objection_violation(
    spec: ReplySpec,
    answer: str,
) -> str | None:
    tool_state = spec.verified_facts.get("tool_state")
    if not isinstance(tool_state, dict):
        return None
    if (
        tool_state.get("membership_question_kind") != "objection"
        or tool_state.get("membership_objection_round") != "followup"
    ):
        return None
    if tool_state.get("additional_discount_status") == "unavailable" and not any(
        marker in answer for marker in NO_FURTHER_DISCOUNT_MARKERS
    ):
        return "missing_discount_answer"
    if any(marker in answer for marker in MEMBERSHIP_VALUE_LIST_MARKERS):
        return "repeated_membership_value"
    return None


def _invalid_product_extension(spec: ReplySpec, answer: str) -> bool:
    if (
        spec.composition_mode != "anchor_plus_persona"
        or spec.question_slot is not None
    ):
        return False
    tool_state = spec.verified_facts.get("tool_state")
    if not isinstance(tool_state, dict) or tool_state.get("commerce_type") != "product":
        return False
    compact = "".join(answer.split())
    return len(compact) > 40 or not any(
        marker in compact for marker in PRODUCT_EXTENSION_ACTION_MARKERS
    )


def _disallowed_product_card_action(spec: ReplySpec, answer: str) -> bool:
    tool_state = spec.verified_facts.get("tool_state")
    return (
        isinstance(tool_state, dict)
        and tool_state.get("commerce_type") == "product"
        and tool_state.get("send_purchase_card") is False
        and any(marker in answer for marker in PRODUCT_CARD_ACTION_MARKERS)
    )


def finalize_reply_spec(spec: ReplySpec) -> FinalReply:
    outbound_messages = list(spec.outbound_messages)
    answer = spec.suggested_copy
    answer_segments = list(spec.answer_segments)
    if (
        spec.composition_mode == "anchor_plus_persona"
        and spec.persona_copy.strip()
    ):
        anchor = spec.suggested_copy.strip()
        persona = spec.persona_copy.strip()
        answer_segments = [part for part in (anchor, persona) if part]
        answer = "\n\n".join(answer_segments)
        if outbound_messages:
            outbound_messages.append(OutboundMessage(type="text", content=persona))
    elif spec.render_mode == "persona" and outbound_messages:
        outbound_messages = _replace_first_text(outbound_messages, spec.suggested_copy)
    answer_segments = _separate_follow_up_segments(
        answer_segments or ([answer] if answer.strip() else [])
    )
    outbound_messages = _separate_follow_up_messages(outbound_messages)
    if not outbound_messages and answer_segments:
        outbound_messages = [
            OutboundMessage(type="text", content=segment)
            for segment in answer_segments
        ]
    metadata = dict(spec.metadata)
    metadata.pop("persona_original_copy", None)
    if spec.question_slot and _question_count(answer) == 1:
        metadata["emitted_question_slot"] = spec.question_slot
    else:
        metadata.pop("emitted_question_slot", None)
    return FinalReply(
        answer=answer,
        answer_segments=answer_segments,
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


def _separate_follow_up_segments(segments: list[str]) -> list[str]:
    result: list[str] = []
    for segment in segments:
        for part in _split_follow_up_question(str(segment)):
            result.extend(split_customer_messages(part))
    return [item for item in result if item]


def _separate_follow_up_messages(
    messages: list[OutboundMessage],
) -> list[OutboundMessage]:
    result: list[OutboundMessage] = []
    for message in messages:
        if message.type != "text":
            result.append(message)
            continue
        parts = [
            chunk
            for part in _split_follow_up_question(message.content)
            for chunk in split_customer_messages(part)
        ]
        result.extend(
            OutboundMessage(
                type="text",
                content=part,
                material_id=message.material_id,
            )
            for part in parts
        )
    return result


def _split_follow_up_question(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    match = re.search(r"([^。！!\n]*[？?])\s*$", value)
    if match is None:
        match = TRAILING_QUESTION_PATTERN.search(value)
    if match is None:
        return [value]
    question = match.group(1).strip()
    prefix = value[: match.start(1)].strip()
    if not prefix:
        return [value]
    if question[-1:] not in {"?", "？"}:
        question += "？"
    return [prefix, question]


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
