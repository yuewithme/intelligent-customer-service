import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


def _message(metadata=None, text="请给我推荐一款"):
    return NormalizedMessage(
        trace_id="t1",
        channel="api",
        user_id="u1",
        session_id="s1",
        message=text,
        kb_id="kb_default",
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_business_snapshot_builds_grounded_reply_without_extra_facts():
    from app.services.business_context_service import build_business_context

    message = _message(
        {"business_snapshot": "《东方红荷》2—3苗26.8元，带花苞发货。"}
    )
    context = await build_business_context(message)
    reply = context.to_reply()

    assert reply is not None
    assert "东方红荷" in reply.answer
    assert "26.8" in reply.answer
    assert "库存充足" not in reply.answer
    assert reply.route == "template_reply"


@pytest.mark.asyncio
async def test_template_reply_uses_structured_business_context_before_search(monkeypatch):
    from app.services import template_reply_service

    async def fail_select(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(template_reply_service, "select_template", fail_select)
    message = _message(
        {
            "business_snapshot": "会员39.9元，赠送建兰《飞煌腾达》。",
            "tool_state": {"activity": "expired"},
        },
        text="会员有哪些服务？",
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent="ask_after_sale",
        confidence=0.9,
    )

    reply = await template_reply_service.build_default_template_reply(
        message, intent, UserState(user_id="u1")
    )

    assert reply is not None
    assert "39.9" in reply.answer
    assert "expired" in reply.metadata["business_context"]["tool_state"].values()
