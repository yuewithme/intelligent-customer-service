import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services import agent_tools
from app.domains.decisioning.services.agent_prompt import build_system_prompt
from app.domains.decisioning.services.agent_tools import AgentExecutionContext


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace-conversation-progression",
        channel="api",
        user_id="customer-1",
        session_id="session-1",
        message=text,
        kb_id="kb_default",
        metadata={},
    )


def test_prompt_teaches_progression_as_contextual_experience_not_fixed_routing():
    prompt = build_system_prompt()

    assert "选对＋有人教" in prompt
    assert "也为后续推荐适配品种建立信任" in prompt
    assert "客户收到的是直播间图文版的电子档" in prompt
    assert "链接应在 48 小时内及时查看" in prompt
    assert "图文资料只是陪伴指导的一步" in prompt
    assert "不重讲刚说过的资料、服务或客户背景" in prompt
    assert "不是按关键词触发的硬路由" in prompt


def test_progression_capability_connects_beginner_material_and_next_action():
    progression = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.conversation_progression"
    )

    assert "不把每句客户话当成新开始" in progression.instructions
    assert "选对＋有人教" in progression.instructions
    assert "48 小时内及时查看" in progression.instructions
    assert "图文资料只是陪伴指导的一步" in progression.instructions
    assert "不再复述刚说过的资料" in progression.instructions
    assert "不是按短语触发的固定路由" in progression.instructions


@pytest.mark.asyncio
async def test_material_send_returns_verified_post_send_positioning(monkeypatch):
    async def material_not_recently_sent(context, title):
        del context, title
        return False

    monkeypatch.setattr(
        agent_tools, "_material_recently_sent", material_not_recently_sent
    )
    context = AgentExecutionContext(
        message=_message("把直播间那份资料发我"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="material-1",
        name="material.send",
        arguments={"material_ref": "material:orchid-companion"},
        context=context,
    )

    assert result.status == "prepared"
    assert "直播间展示的是图文版" in result.data["post_send_facts"]["format"]
    assert "48 小时" in result.data["post_send_facts"]["access"]
    assert "其中一步" in result.data["post_send_facts"]["service_role"]
