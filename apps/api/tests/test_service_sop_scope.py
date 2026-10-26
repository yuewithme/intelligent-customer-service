import json
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
    assert "立即转人工，不需要购买意向或客户确认" in prompt
    assert "不因此转人工" not in prompt


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
@pytest.mark.parametrize("scope,tool,value,text", [
    ("service", "service.record", "春兰耐看", "我比较喜欢春兰，耐看"),
    ("service", "service.record", "耐看的老种", "我更喜欢耐看的老种"),
    ("service", "service.record", "没有特别偏好", "没什么特别偏好，都行"),
    ("service", "customer.tag", "清香", "我喜欢清香的"),
    ("first_order", "customer.tag", "春兰", "我喜欢春兰"),
    ("general", "customer.tag", "荷瓣", "我喜欢荷瓣"),
    ("seeding", "customer.tag", "春兰", "我喜欢春兰"),
    ("general", "human.handoff", "有年代感的老种", "我更喜欢有年代感的老种"),
])
async def test_one_current_product_preference_immediately_enters_handoff(monkeypatch, tmp_path, scope, tool, value, text):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message, _decision
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import agent_runtime
    from app.domains.conversations.services import conversation_service, service_handoff_service
    from app.domains.customers.services.user_profile_service import get_profile_bundle

    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_runtime, "get_saved_flow", lambda scope: None)
    monkeypatch.setattr(agent_runtime, "is_sop_node_handoff_enabled", lambda scope, node: False)
    if tool == "service.record":
        arguments = {"facts": [{"field": "preference", "value": value, "evidence": text}]}
    elif tool == "customer.tag":
        arguments = {"tag": value, "evidence": text}
    else:
        arguments = {"reason": "product_preference_received", "summary": text}
    calls = []
    async def generate(*args, **kwargs):
        calls.append(1)
        assert len(calls) == 1, "喜好已经收到，不应继续生成追问或收尾"
        decision = _decision(sop_node="general.reply", tools=[
            {"call_id": "preference", "name": tool, "arguments": arguments},
            {"call_id": "unneeded", "name": "knowledge.search", "arguments": {"query": "继续介绍品种"}},
        ])
        decision["data"]["service_handoff"] = {"invitation_reason": "closing"}
        return decision
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    message = _message(text, sop_scope=scope)
    message.metadata.update(sop_scopes=[scope], suppress_handoff_notification=True)
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id=message.user_id), workspace={"profile": {}})
    assert len(calls) == 1 and reply.need_human and reply.route == "human"
    assert reply.metadata["handoff"]["reason"] == "product_preference_received"
    assert reply.metadata["handoff"]["summary"] == text
    assert reply.metadata["agent_runtime"]["purchase_signal"] == "none"
    trace = reply.metadata["agent_runtime"]["tool_trace"]
    assert all(item["call_id"] != "unneeded" for item in trace)
    assert service_handoff_service.CLOSING_INVITATION not in reply.answer
    await conversation_service.record_ai_turn(message=message, result={**reply.model_dump(), "handoff": reply.metadata["handoff"]})
    detail = await conversation_service.get_conversation_detail(conversation_service.make_conversation_id(message.channel, message.user_id, message.session_id))
    assert detail["conversation"]["status"] == conversation_service.HANDOFF_PENDING
    if tool == "service.record":
        profile = (await get_profile_bundle(message.user_id))["profile"]
        assert profile["service_facts"]["preference"]["value"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments,text", [
    ("service.record", {"facts": [{"field": "existing_varieties", "value": "春兰", "evidence": "有一盆春兰"}]}, "我有一盆春兰"),
    ("service.record", {"facts": [{"field": "environment", "value": "阳台", "evidence": "放在阳台养"}]}, "我放在阳台养"),
    ("service.record", {"facts": [{"field": "preference", "value": "春兰", "evidence": "我喜欢春兰"}]}, "春兰该怎么浇水"),
    ("customer.tag", {"tag": "1-9盆", "evidence": "三盆"}, "我有三盆"),
    ("customer.tag", {"tag": "春兰", "evidence": "我喜欢春兰"}, "春兰该怎么浇水"),
])
async def test_ordinary_facts_or_invalid_preference_evidence_do_not_handoff(monkeypatch, tmp_path, tool, arguments, text):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message, _decision
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import agent_runtime

    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_runtime, "get_saved_flow", lambda scope: None)
    monkeypatch.setattr(agent_runtime, "is_sop_node_handoff_enabled", lambda scope, node: False)
    decisions = iter([
        _decision(tools=[{"call_id": "fact", "name": tool, "arguments": arguments}]),
        _decision(final={"messages": [{"type": "text", "content": "浇水要结合植料干湿，避免一直积水。"}]}),
    ])
    async def generate(*args, **kwargs):
        return next(decisions)
    monkeypatch.setattr(agent_runtime, "generate_messages_json", generate)
    message = _message(text)
    message.metadata["evaluation_id"] = "ordinary-facts"
    reply = await agent_runtime.run_sales_agent(message=message, user_state=UserState(user_id=message.user_id), workspace={"profile": {
        "customer_tags": ["春兰"], "service_facts": {"preference": {"value": "喜欢清香", "evidence": "历史喜好"}},
    }})
    assert not reply.need_human and "handoff" not in reply.metadata
    assert len(reply.metadata["agent_runtime"]["tool_trace"]) == 1


@pytest.mark.asyncio
async def test_tutorial_intro_and_member_card_use_real_delivery_state(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message
    from app.domains.decisioning.schemas.execution import AgentExecutionContext
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import agent_tools
    from app.domains.conversations.services.conversation_service import record_ai_turn, get_service_delivery_state, get_conversation_detail, make_conversation_id
    from app.core.config import get_settings
    _configure(monkeypatch, tmp_path)
    card = {
        "display_name": "萧岚苑", "app_id": "wx0123456789abcdef", "user_name": "gh_example@app",
        "icon_url": "https://example.com/icon.png", "page_path": "pages/home/index",
        "thumb_url": "https://example.com/member.png", "title": "萧岚苑会员日",
    }
    monkeypatch.setenv("SERVICE_MEMBER_DAY_MINI_PROGRAM_CARD", json.dumps(card, ensure_ascii=False))
    get_settings.cache_clear()
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
    assert {"tutorial", "introduction", "environment_request"} <= set(state["delivered_steps"])
    assert any(step.startswith("tutorial:") for step in state["delivered_steps"])
    workspace["service_sop"].update(state)
    assert (await execute_agent_tool(call_id="intro", name="service.introduce", arguments={}, context=context)).status == "forbidden"
    result = await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    assert result.status == "prepared"
    assert [item.type for item in context.prepared["member"]] == ["text", "mini_program"]
    assert json.loads(context.prepared["member"][-1].content) == card
    await record_ai_turn(message=message, result={"outbound_messages": [m.model_dump() for m in context.prepared["member"]], "route": "agent"})
    workspace["service_sop"].update(get_service_delivery_state(message.user_id))
    detail = await get_conversation_detail(make_conversation_id(message.channel, message.user_id, message.session_id))
    assert detail["messages"][-1]["metadata"]["mini_program"] == card
    assert detail["messages"][-1]["content"] == "[小程序] 萧岚苑会员日"
    assert (await execute_agent_tool(call_id="member-again", name="member_day.send", arguments={}, context=context)).status == "forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "shortlink"])
async def test_member_card_missing_parameters_does_not_send_text_or_mark_prepared(monkeypatch, invalid):
    from app.core.config import get_settings
    card = {} if invalid == "missing" else {
        "display_name": "萧岚苑", "app_id": "wx0123456789abcdef", "user_name": "gh_example@app",
        "icon_url": "https://example.com/icon.png", "page_path": "#小程序://萧岚苑/cEdtXk4B8yCjmTy",
        "thumb_url": "https://example.com/member.png", "title": "萧岚苑会员日",
    }
    monkeypatch.setenv("SERVICE_MEMBER_DAY_MINI_PROGRAM_CARD", json.dumps(card, ensure_ascii=False))
    get_settings.cache_clear()
    context = _context(sop_scope="service")
    context.workspace["service_sop"] = {}
    result = await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    assert result.status == "temporarily_unavailable"
    assert result.data["reason"] == "member_day_mini_program_card_not_configured"
    assert not context.prepared
    assert not context.workspace["service_sop"].get("member_day_prepared")


@pytest.mark.asyncio
async def test_member_card_defaults_to_verified_official_card(monkeypatch):
    from app.core.config import get_settings
    from app.domains.decisioning.services.service_sop import MEMBER_DAY_MINI_PROGRAM_CARD

    monkeypatch.delenv("SERVICE_MEMBER_DAY_MINI_PROGRAM_CARD", raising=False)
    get_settings.cache_clear()
    context = _context(sop_scope="service")
    context.workspace["service_sop"] = {}
    result = await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    assert result.status == "prepared"
    card = json.loads(context.prepared["member"][-1].content)
    assert card == MEMBER_DAY_MINI_PROGRAM_CARD
    assert card["app_id"] == "wxdb64bd6b936d0fe9"
    assert "%E5%91%A8%E5%9B%9B%E4%BC%9A%E5%91%98%E6%97%A5" in card["page_path"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("completed,remaining", [("member_day_intro", "member_day"), ("member_day", "member_day_intro")])
async def test_member_delivery_only_prepares_missing_part(completed, remaining):
    context = _context(sop_scope="service")
    context.workspace["service_sop"] = {"delivered_steps": [completed]}
    result = await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    assert result.status == "prepared"
    assert [item.service_step for item in context.prepared["member"]] == [remaining]


@pytest.mark.asyncio
async def test_repeated_prepared_reference_only_expands_once(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message, _decision
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.schemas.agent import AgentTurnDecision
    from app.domains.decisioning.services.agent_runtime import _finalize_reply

    _configure(monkeypatch, tmp_path)
    message = _message("好的，谢谢")
    context = AgentExecutionContext(message=message, user_state=UserState(user_id=message.user_id), workspace={})
    await execute_agent_tool(call_id="member", name="member_day.send", arguments={}, context=context)
    decision = AgentTurnDecision.model_validate(_decision(final={"messages": [
        {"type": "prepared", "ref": "member"}, {"type": "prepared", "ref": "member"},
    ]})["data"])
    reply = await _finalize_reply(decision=decision, context=context, usage={}, tool_results=[], quality_flags=[], attempt_trace=[])
    assert [item.type for item in reply.outbound_messages] == ["text", "mini_program"]


def test_saved_service_facts_reach_workspace_and_prevent_repeated_environment_request(monkeypatch, tmp_path):
    from test_service_material_touch_service import _configure
    from test_agent_harness_v2 import _message
    from app.domains.customers.schemas.state import UserState
    from app.domains.conversations.services.chat_orchestrator import _customer_workspace
    from app.domains.decisioning.services.service_sop import build_service_context
    from app.domains.decisioning.services.agent_tools import _service_intro_messages

    _configure(monkeypatch, tmp_path)
    message = _message()
    user_state = UserState(user_id=message.user_id)
    facts = {"province": {"value": "浙江"}, "environment": {"value": "阳台"}, "preference": {"value": "喜欢香花"}}
    workspace = _customer_workspace(message=message, user_state=user_state, profile_bundle={"profile": {"service_facts": facts}})
    workspace["service_sop"] = build_service_context(workspace["profile"])
    context = AgentExecutionContext(message=message, user_state=user_state, workspace=workspace)
    assert workspace["service_sop"]["facts"] == facts
    assert not workspace["service_sop"]["collect_preferences"]
    assert [item.service_step for item in _service_intro_messages(context)] == ["introduction"]
