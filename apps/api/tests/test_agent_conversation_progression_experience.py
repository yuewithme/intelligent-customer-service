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
    prompt = build_system_prompt(sop_scope="first_order")

    assert "选对＋有人教" in prompt
    assert "也为后续推荐适配品种建立信任" in prompt
    assert "客户收到的是直播间图文版的电子档" in prompt
    assert "链接应在 48 小时内及时查看" in prompt
    assert "图文资料只是陪伴指导的一步" in prompt
    assert "不重讲刚说过的资料、服务或客户背景" in prompt
    assert "不是按关键词触发的硬路由" in prompt
    assert "先按 12.3 判断它是在确认紧邻提议" in prompt
    assert "会员课程和长期服务不能脱离已核实权益扩大承诺" in prompt
    assert "以客户当前明确表达的意思为最高优先级" in prompt
    assert "不能为了完成自己的流程强行拉回" in prompt


def test_progression_capability_connects_beginner_material_and_next_action():
    progression = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.conversation_progression"
    )

    assert "把对话看成一条连续的关系进度" in progression.instructions
    assert "不能要求客户继续上一话题" in progression.instructions
    assert "选对＋有人教" in progression.instructions
    assert "48 小时内及时查看" in progression.instructions
    assert "图文资料只是陪伴指导的一步" in progression.instructions
    assert "已确认的内容不再复述" in progression.instructions
    assert "不是按短语触发的固定路由" in progression.instructions
    assert "上下文只帮助理解客户当前意思" in progression.instructions
    assert "原提问和销售动作退到背景" in progression.instructions


def test_merged_experience_keeps_material_and_short_reply_meaning_consistent():
    capabilities = {item.name: item.instructions for item in agent_tools.CAPABILITIES}

    material = capabilities["experience.material_value"]
    assert "图文版电子档" in material
    assert "链接 48 小时内有效" in material
    assert "资料只是陪伴指导的其中一步" in material

    objection = capabilities["experience.objection"]
    assert "必须放回紧邻上下文" in objection
    assert "不能仅凭短语决定" in objection

    companion = capabilities["experience.companion_service_fit"]
    assert "会员百节视频教学" in companion
    assert "师傅一对一实时指导" in companion
    assert "按真实权益" in companion


def test_new_product_interest_restarts_discovery_without_returning_to_old_flow():
    prompt = build_system_prompt(sop_scope="first_order")
    capabilities = {item.name: item.instructions for item in agent_tools.CAPABILITIES}

    assert "这已经是新的当前主线" in prompt
    assert "不在同一轮又自动回到旧的订单截图" in prompt
    assert "从花色、香味、预算、地区环境和经验中" in prompt
    assert "易养和信心风险当作核心选品条件" in prompt

    direction = capabilities["experience.product_direction_weighting"]
    assert "新出现的兰花购买需求会成为当前主线" in direction
    assert "不当成必填表" in direction
    assert "不重问原问题" in direction


def test_beginner_product_value_and_companion_close_use_verified_facts():
    capabilities = {item.name: item.instructions for item in agent_tools.CAPABILITIES}

    pain = capabilities["experience.pain_to_service"]
    assert "如果客户已在选兰花" in pain
    assert "人群上说清新手为什么需要降低试错" in pain
    assert "不把所有在售兰花都说成自然放养或皮实好养" in pain

    close = capabilities["experience.trial_and_close"]
    assert "把花养好养开花的目标" in close
    assert "一顿快餐的成本" in close
    assert "不从历史案例继承 39.9 元、原价 199 元或‘终身有效’" in close
    assert "不只是一份资料" in close
    assert "才能把赠品具体说成高价值国兰、带花苞" in close


@pytest.mark.asyncio
async def test_capability_search_retrieves_product_restart_and_stronger_close():
    context = AgentExecutionContext(
        message=_message("我是新手，想选好养的兰花"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    context.message.metadata = {"sop_scope": "first_order"}

    product_result = await agent_tools.execute_agent_tool(
        call_id="cap-product",
        name="capability.search",
        arguments={"query": "适合新手的兰花，挖花色香味和预算"},
        context=context,
    )
    product_names = {
        item["name"] for item in product_result.data["capabilities"]
    }
    assert "experience.product_direction_weighting" in product_names

    close_result = await agent_tools.execute_agent_tool(
        call_id="cap-close",
        name="capability.search",
        arguments={"query": "付费体验价和赠品怎么促单"},
        context=context,
    )
    close_names = {item["name"] for item in close_result.data["capabilities"]}
    assert "experience.trial_and_close" in close_names


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
