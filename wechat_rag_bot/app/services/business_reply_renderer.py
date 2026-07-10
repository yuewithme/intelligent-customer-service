import json

from app.schemas.reply import FinalReply
from app.schemas.reply_plan import BusinessFacts
from app.services.llm_service import generate_answer


INTERNAL_KEYS = {
    "payment_status",
    "order_lookup",
    "activity_status",
    "shipping_date_change_executed",
    "course_status",
}


def build_business_prompt(*, question: str, facts: BusinessFacts) -> str:
    payload = json.dumps(facts.model_dump(), ensure_ascii=False, sort_keys=True)
    return f"""你是兰花客服。只根据给定事实回答当前问题。
规则：
1. 不得增加价格、库存、权益、执行结果或时间承诺。
2. false、unknown、unverified、not_found、failed 表示事项未完成或未确认。
3. 不得向客户输出英文内部字段名。
4. 已付款、已执行和已开通必须有明确的肯定事实才能确认。
5. 用简洁自然的中文说明现状和下一步。

【业务事实】
{payload}

【客户当前问题】
{question.strip()}
"""


def _contains_internal_key(answer: str, facts: BusinessFacts) -> bool:
    keys = INTERNAL_KEYS | set(facts.tool_state)
    return any(key in answer for key in keys)


async def render_business_reply(
    message,
    facts: BusinessFacts | None = None,
) -> FinalReply:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    tool_state = metadata.get("tool_state")
    facts = facts or BusinessFacts(
        snapshot=str(metadata.get("business_snapshot") or "").strip(),
        tool_state=dict(tool_state) if isinstance(tool_state, dict) else {},
    )
    result = await generate_answer(
        build_business_prompt(question=message.message, facts=facts),
        purpose="business",
    )
    answer = str(result.get("answer") or "").strip()
    if not answer or _contains_internal_key(answer, facts):
        answer = (
            "当前业务状态需要进一步核验，"
            "我会根据查询或操作结果再向您确认。"
        )
    return FinalReply(
        answer=answer,
        reply_type="template",
        route="template_reply",
        usage=dict(result.get("usage") or {}),
        metadata={"business_facts_used": True},
    )
