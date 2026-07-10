import pytest

from app.schemas.event import NormalizedMessage


def _message(text: str, tool_state: dict) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_business",
        channel="api",
        user_id="user_business",
        session_id="session_business",
        message=text,
        kb_id="kb_default",
        metadata={"tool_state": tool_state},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "tool_state", "must_contain"),
    [
        (
            "还是按66元下单吧。",
            {"current_bundle_price": 72, "historical_price": 66},
            "72",
        ),
        ("我已经下单了，帮我开课程。", {"order_lookup": "not_found"}, "没有查询到"),
        ("我刚付过了，怎么还显示失败？", {"payment_status": "failed"}, "支付失败"),
    ],
)
async def test_business_reply_uses_customer_language(
    monkeypatch,
    text,
    tool_state,
    must_contain,
):
    from app.services import business_reply_renderer

    async def fake_generate_answer(prompt: str, purpose: str = "rag") -> dict:
        assert purpose == "business"
        if "current_bundle_price" in prompt:
            return {
                "answer": "历史价格是66元，当前有效价格是72元，请确认是否继续。",
                "usage": {},
            }
        if "order_lookup" in prompt:
            return {
                "answer": "当前没有查询到对应订单，请核对订单号或下单账号。",
                "usage": {},
            }
        return {
            "answer": "系统当前显示支付失败，请先核对是否实际扣款。",
            "usage": {},
        }

    monkeypatch.setattr(business_reply_renderer, "generate_answer", fake_generate_answer)

    reply = await business_reply_renderer.render_business_reply(
        _message(text, tool_state)
    )

    assert must_contain in reply.answer
    assert not any(key in reply.answer for key in tool_state)
    assert reply.route == "template_reply"
    assert reply.need_human is False
