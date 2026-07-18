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
    commerce_reply = _render_commerce_reply(facts)
    if commerce_reply is not None:
        return commerce_reply
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


def _render_commerce_reply(facts: BusinessFacts) -> FinalReply | None:
    state = facts.tool_state
    commerce_type = state.get("commerce_type")
    if commerce_type not in {"product", "order"}:
        return None

    status = state.get("status")
    if status == "unavailable":
        return _commerce_final_reply("当前有赞系统暂时无法查询，请稍后再试。", state)
    if status == "missing_product":
        return _commerce_final_reply("可以的，您想看哪一个品种或规格？", state)
    if commerce_type == "product":
        products = state.get("products") if isinstance(state.get("products"), list) else []
        if status != "found" or not products:
            answer = "暂时没有查到合适的商品，您可以再告诉我品种、颜色或规格。"
        else:
            first = products[0]
            price = first.get("price_cent") if isinstance(first, dict) else None
            price_text = f"，当前售价{price / 100:g}元" if isinstance(price, int) else ""
            card = state.get("mini_program")
            if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
                next_step = "，点击商品卡片就可以查看和下单。"
            elif first.get("h5_url"):
                next_step = f"。购买链接：{first['h5_url']}"
            else:
                next_step = "。如果需要下单，我再帮您确认购买入口。"
            answer = f"给您找到这款“{first.get('title') or '商品'}”{price_text}{next_step}"
        return _commerce_final_reply(answer, state)

    if status == "missing_mobile":
        return _commerce_final_reply("可以的，请把下单手机号发给我，我帮您查询一下。", state)
    orders = state.get("orders") if isinstance(state.get("orders"), list) else []
    if status != "found" or not orders:
        answer = "暂时没有查到近期订单，请核对下单手机号，或点击订单卡片自行查看。"
        return _commerce_final_reply(answer, state)

    lines = ["查到您近期的订单："]
    for index, order in enumerate(orders, start=1):
        if not isinstance(order, dict):
            continue
        detail = f"{index}. {order.get('item_summary') or '商品'}，{order.get('status_text') or '状态待确认'}"
        if order.get("express_company"):
            detail += f"，{order['express_company']}"
        if order.get("tracking_no_masked"):
            detail += f"，单号{order['tracking_no_masked']}"
        lines.append(detail)
    card = state.get("mini_program")
    if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
        lines.append("详细信息可以点击订单卡片查看。")
    else:
        lines.append("以上为有赞最新查询结果。")
    return _commerce_final_reply("\n".join(lines), state)


def _commerce_final_reply(answer: str, state: dict) -> FinalReply:
    outbound_messages = [{"type": "text", "content": answer}]
    card = state.get("mini_program")
    if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
        outbound_messages.append(
            {
                "type": "mini_program",
                "content": json.dumps(card, ensure_ascii=False),
            }
        )
    return FinalReply(
        answer=answer,
        outbound_messages=outbound_messages,
        reply_type="template",
        route="template_reply",
        metadata={"business_facts_used": True},
    )
