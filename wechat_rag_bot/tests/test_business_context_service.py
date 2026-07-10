import pytest

from app.schemas.event import NormalizedMessage


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
async def test_business_snapshot_builds_grounded_facts_without_extra_facts():
    from app.services.business_context_service import build_business_context

    message = _message(
        {"business_snapshot": "《东方红荷》2—3苗26.8元，带花苞发货。"}
    )
    context = await build_business_context(message)

    assert context.available is True
    assert "东方红荷" in context.snapshot
    assert context.tool_state == {}
