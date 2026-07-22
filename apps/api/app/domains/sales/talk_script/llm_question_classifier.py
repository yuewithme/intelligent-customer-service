import json

from app.integrations.ai.services import llm_service
from app.domains.sales.talk_script.models import ClassifierDecision


async def classify_question(
    *,
    current_message: str,
    normalized_message: str,
    recent_messages: list[str] | None,
    customer_tags: dict | None,
    candidate_questions: list[dict],
) -> ClassifierDecision:
    if llm_service.get_model_config("talk_script").provider == "mock":
        return _mock_classify(normalized_message, candidate_questions)

    raw = await llm_service.generate_json(
        _build_prompt(
            current_message=current_message,
            recent_messages=recent_messages or [],
            customer_tags=customer_tags or {},
            candidate_questions=candidate_questions,
        ),
        purpose="talk_script",
    )
    return ClassifierDecision.model_validate(raw)


def _mock_classify(
    normalized_message: str,
    candidate_questions: list[dict],
) -> ClassifierDecision:
    best: tuple[float, dict] | None = None
    for item in candidate_questions:
        score = 0.0
        for field in (
            "standard_question",
            "core_intent",
            "positive_examples",
            "required_conditions",
        ):
            value = str(item.get(field) or "")
            if value and value in normalized_message:
                score = max(score, 0.9)
            elif value:
                score = max(
                    score,
                    len(set(normalized_message) & set(value)) / max(len(set(value)), 1),
                )
        if best is None or score > best[0]:
            best = (score, item)
    if best is None or best[0] <= 0:
        return ClassifierDecision(matched=False, reason="no_candidate_match")
    return ClassifierDecision(
        matched=True,
        question_id=best[1]["question_id"],
        confidence=min(max(best[0], 0.0), 0.95),
        reason="mock_candidate_text_score",
    )


def _build_prompt(
    *,
    current_message: str,
    recent_messages: list[str],
    customer_tags: dict,
    candidate_questions: list[dict],
) -> str:
    return f"""你是兰花私域客服系统中的“标准问题分类器”。

你的任务不是回答用户，也不是生成话术。
你的唯一任务是：根据用户当前消息、最近上下文、客户标签、候选标准问题，判断用户问题最匹配哪个 question_id。

你必须遵守：
1. 只能从候选 question_id 中选择。
2. 如果没有合适候选，输出 matched=false。
3. 如果用户问题信息不足，输出 need_slot_filling=true。
4. 只有用户明确要求人工、退款、退货、赔付、补发、投诉、举报或订单纠纷时，才输出 need_human=true。
5. 黑斑、黄叶、烂根、腐苗、快死了、不开花、不会养，如果只是咨询养护或需要判断原因，不要输出 need_human=true；信息不足时输出 need_slot_filling=true，让后续大模型自然追问。
6. 不能自由生成客服回复。
7. 必须参考 positive_examples 和 negative_examples。
8. 如果候选都不合适，必须返回 matched=false，不要硬选。

当前用户消息：
{current_message}

最近上下文：
{json.dumps(recent_messages, ensure_ascii=False)}

客户标签：
{json.dumps(customer_tags, ensure_ascii=False)}

候选标准问题：
{json.dumps(candidate_questions, ensure_ascii=False)}

请只输出 JSON，不要输出其他内容：
{{
  "matched": true,
  "question_id": "候选中的 question_id 或 null",
  "confidence": 0.0,
  "need_slot_filling": false,
  "need_human": false,
  "reason": "简短说明判断依据"
}}
"""
