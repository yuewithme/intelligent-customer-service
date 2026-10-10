from types import SimpleNamespace

import pytest

from app.domains.decisioning.services.agent_prompt import build_system_prompt
from app.domains.decisioning.schemas.execution import AgentExecutionContext
from app.domains.decisioning.services.agent_tools import execute_agent_tool


def _context(*, sop_scope: str | None = None) -> AgentExecutionContext:
    metadata = {} if sop_scope is None else {"sop_scope": sop_scope}
    return AgentExecutionContext(
        message=SimpleNamespace(metadata=metadata, message=""),
        user_state=SimpleNamespace(user_id="customer-1"),
        workspace={},
    )


def test_service_prompt_prioritizes_problem_resolution_and_relationship():
    prompt = build_system_prompt(sop_scope="service")

    assert "客户当前的问题优先" in prompt
    assert "通过专业服务建立长期信任" in prompt
    assert "由人工按需精准推荐" in prompt
    assert "普通养护咨询不能直接解释成购买意向" in prompt
    assert "新客户开场优先了解当前盆数和主要品种" not in prompt
    assert "陪伴养兰服务＞兰花＞养护产品" not in prompt


def test_first_order_prompt_remains_available_but_is_not_default_runtime_scope():
    prompt = build_system_prompt(sop_scope="first_order")

    assert "新客户开场优先了解当前盆数和主要品种" in prompt
    assert "陪伴养兰服务＞兰花＞养护产品" in prompt


@pytest.mark.asyncio
async def test_service_capability_search_excludes_first_order_experience():
    result = await execute_agent_tool(
        call_id="search-service",
        name="capability.search",
        arguments={"query": "首单 陪伴养兰 试成交 逼单", "limit": 8},
        context=_context(),
    )

    names = {item["name"] for item in result.data["capabilities"]}
    assert result.data["sop_scope"] == "service"
    assert "experience.service_first_routing" not in names
    assert "experience.pain_to_service" not in names
    assert "experience.trial_and_close" not in names


@pytest.mark.asyncio
async def test_service_can_discover_post_service_repurchase_seed_experience():
    result = await execute_agent_tool(
        call_id="search-post-service-seed",
        name="capability.search",
        arguments={
            "query": "已购客户问题解决后服务收口，传达会员福利并了解花色瓣型鉴赏偏好",
            "limit": 8,
        },
        context=_context(),
    )

    capabilities = {item["name"]: item for item in result.data["capabilities"]}
    assert "experience.post_service_repurchase_seed" in capabilities
    instructions = capabilities["experience.post_service_repurchase_seed"][
        "full_instructions"
    ]
    assert "member_day.send" in instructions
    assert "永久停止例行挖需" in instructions
    assert "service.record" in instructions
    assert "human.handoff" in instructions
    assert "当前问题未解决" in instructions


def test_service_prompt_separates_preference_collection_from_purchase_intent():
    prompt = build_system_prompt(sop_scope="service")

    assert "collect_preferences" in prompt
    assert "跨会话不再主动执行例行挖需" in prompt
    assert "仅仅回答了未来鉴赏偏好不等于现在有购买意向" in prompt
    assert "member_day.send" in prompt
    assert "周四会员日" in prompt
    assert "不自行查询推荐或发送商品卡片" in prompt


@pytest.mark.asyncio
async def test_member_hook_experience_redirects_detail_questions_without_inventing():
    result = await execute_agent_tool(
        call_id="search-member-hook",
        name="capability.search",
        arguments={
            "query": "会员每周上新专属折扣，客户追问几折和哪天上新",
            "limit": 5,
        },
        context=_context(),
    )

    capabilities = {item["name"]: item for item in result.data["capabilities"]}
    instructions = capabilities["experience.member_benefit_delivery"][
        "full_instructions"
    ]
    assert "member_day.send" in instructions
    assert "不编本期品种" in instructions
    assert "已介绍内容不反复背诵" in instructions


@pytest.mark.asyncio
async def test_first_order_capability_search_can_retrieve_archived_experience():
    result = await execute_agent_tool(
        call_id="search-first-order",
        name="capability.search",
        arguments={"query": "首单 陪伴养兰 试成交 逼单", "limit": 8},
        context=_context(sop_scope="first_order"),
    )

    names = {item["name"] for item in result.data["capabilities"]}
    assert result.data["sop_scope"] == "first_order"
    assert names & {
        "experience.service_first_routing",
        "experience.pain_to_service",
        "experience.trial_and_close",
    }


@pytest.mark.asyncio
async def test_any_preference_persists_and_stops_future_collection(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message
    from app.domains.decisioning.schemas.execution import AgentExecutionContext
    from app.domains.customers.schemas.state import UserState
    from app.domains.customers.services.user_profile_service import get_profile_bundle
    from app.domains.decisioning.services.service_sop import build_service_context
    _configure(monkeypatch, tmp_path)
    message = _message("我养了两年，喜欢有香味的，最近长了新芽。")
    message.metadata["sop_scope"] = "service"
    context = AgentExecutionContext(message=message, user_state=UserState(user_id=message.user_id), workspace={"profile": {}})
    result = await execute_agent_tool(call_id="facts", name="service.record", arguments={"facts": [
        {"field": "growing_years", "value": "两年", "evidence": "养了两年"},
        {"field": "preference", "value": "喜欢有香味的", "evidence": "喜欢有香味的"},
        {"field": "care_status", "value": "最近长了新芽", "evidence": "最近长了新芽"},
    ]}, context=context)
    assert result.status == "recorded"
    profile = (await get_profile_bundle(message.user_id))["profile"]
    assert profile["service_facts"]["growing_years"]["value"] == "两年"
    assert profile["service_facts"]["care_status"]["value"] == "最近长了新芽"
    assert not build_service_context(profile)["collect_preferences"]
    assert not build_service_context({"customer_tags": ["建兰"]})["collect_preferences"]
    assert not build_service_context({"customer_tags": ["荷瓣"]})["collect_preferences"]
    assert profile["customer_tags"] == []


@pytest.mark.asyncio
async def test_tutorial_intro_and_member_link_use_real_delivery_state(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message
    from app.domains.decisioning.schemas.execution import AgentExecutionContext
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import agent_tools
    from app.domains.conversations.services.conversation_service import record_ai_turn, get_service_delivery_state
    from app.domains.decisioning.services.service_sop import MEMBER_DAY_LINK
    _configure(monkeypatch, tmp_path)
    message = _message("这是我的宋梅订单截图")
    message.metadata.update(sop_scope="service", provider_delivery_mode="simulated")
    workspace = {"profile": {"customer_tags": ["服务中", "浙江省"]}, "service_sop": {"verified_purchase": False}}
    context = AgentExecutionContext(message=message, user_state=UserState(user_id=message.user_id), workspace=workspace)
    monkeypatch.setattr(agent_tools, "get_care_manual", lambda _: {"available": True, "title": "宋梅养护", "note_url": "https://example.com/manual"})
    result = await execute_agent_tool(call_id="manual", name="material.send", arguments={"material_ref": "material:care-manual:1"}, context=context)
    assert result.status == "prepared"
    messages = context.prepared["manual"]
    assert [m.type for m in messages] == ["link_card", "text", "text"]
    assert "什么省份" not in messages[-1].content
    assert get_service_delivery_state(message.user_id)["delivered_steps"] == []
    await record_ai_turn(message=message, result={"outbound_messages": [m.model_dump() for m in messages], "route": "agent"})
    state = get_service_delivery_state(message.user_id)
    assert set(state["delivered_steps"]) == {"tutorial", "introduction", "environment_request"}
    workspace["service_sop"].update(state)
    assert (await execute_agent_tool(call_id="intro", name="service.introduce", arguments={}, context=context)).status == "forbidden"
    result = await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    assert result.status == "prepared"
    assert context.prepared["member"][-1].content == MEMBER_DAY_LINK
    await record_ai_turn(message=message, result={"outbound_messages": [m.model_dump() for m in context.prepared["member"]], "route": "agent"})
    workspace["service_sop"].update(get_service_delivery_state(message.user_id))
    assert (await execute_agent_tool(call_id="member-again", name="member_day.send", arguments={}, context=context)).status == "forbidden"


@pytest.mark.asyncio
async def test_service_purchase_signal_hands_off_before_product_tools(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message, _decision
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import agent_runtime
    _configure(monkeypatch, tmp_path)
    async def decision(*args, **kwargs):
        return _decision(purchase_signal="interest", sop_node="service.repurchase_discovery", tools=[
            {"call_id": "product", "name": "product.search", "arguments": {"query": "荷瓣"}}])
    monkeypatch.setattr(agent_runtime, "generate_messages_json", decision)
    message = _message("帮我推荐一盆荷瓣吧")
    message.metadata.update(sop_scope="service", sop_scopes=["service"])
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id=message.user_id), workspace={"profile": {"customer_tags": ["服务中"]}})
    assert reply.need_human and reply.route == "human"
    assert [item["tool"] for item in reply.metadata["agent_runtime"]["tool_trace"]] == ["human.handoff"]
