from types import SimpleNamespace

import pytest

from app.domains.decisioning.services.agent_prompt import build_system_prompt
from app.domains.decisioning.services.agent_tools import (
    AgentExecutionContext,
    execute_agent_tool,
)


def _context(*, sop_scope: str | None = None) -> AgentExecutionContext:
    metadata = {} if sop_scope is None else {"sop_scope": sop_scope}
    return AgentExecutionContext(
        message=SimpleNamespace(metadata=metadata, message=""),
        user_state=SimpleNamespace(user_id="customer-1"),
        workspace={},
    )


def test_service_prompt_prioritizes_problem_resolution_and_relationship():
    prompt = build_system_prompt(sop_scope="service")

    assert "先把客户当前的问题解决好" in prompt
    assert "履行已承诺的服务并维护长期关系" in prompt
    assert "复购是服务关系中的可选后续结果" in prompt
    assert "普通养护咨询直接解释成购买意向" in prompt
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
    assert "强烈建议在这次收口中完整覆盖三个方向" in instructions
    assert "通常按语义拆成两到三条短消息" in instructions
    assert "不按关键词、固定句式或消息数量机械执行" in instructions
    assert "不触发 product.search、商品推荐或卡片" in instructions
    assert "用 memory.record 保存" in instructions
    assert "当前问题未解决" in instructions


def test_service_prompt_separates_preference_collection_from_purchase_intent():
    prompt = build_system_prompt(sop_scope="service")

    assert "自然了解一个尚未知的花色、瓣型、香味或品种鉴赏偏好" in prompt
    assert "这是长期偏好采集，不代表客户现在有购买意向" in prompt
    assert "强烈建议把这次价值交付继续推进为三个关系结果" in prompt
    assert "通常按语义拆成两到三条短消息" in prompt
    assert "不机械检查固定句式或消息数量" in prompt
    assert "仅仅回答了未来鉴赏偏好" in prompt
    assert "每周会上新多款铭品供鉴赏" in prompt
    assert "有会员专属折扣活动" in prompt
    assert "可以不查工具直接表达" in prompt
    assert "客户追问上新日期、数量、具体品种、折扣、金额、条件或有效期时" in prompt
    assert "再自然转向了解一个尚未知的鉴赏偏好" in prompt


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
    assert "作为默认会员吸引点直接表达" in instructions
    assert "不编数字和细节" in instructions
    assert "把话题转到一个尚未知的花色、瓣型、香味或品种鉴赏偏好" in instructions


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
