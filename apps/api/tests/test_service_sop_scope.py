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
        message=SimpleNamespace(metadata=metadata),
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
    assert "不是要求同一轮连续发送的三段话" in instructions
    assert "不触发 product.search、商品推荐或卡片" in instructions
    assert "用 memory.record 保存" in instructions
    assert "当前问题未解决" in instructions


def test_service_prompt_separates_preference_collection_from_purchase_intent():
    prompt = build_system_prompt(sop_scope="service")

    assert "主动轻量询问一个尚未知的花色、瓣型、香味或品种鉴赏偏好" in prompt
    assert "这是长期偏好采集，不代表客户现在有购买意向" in prompt
    assert "不把服务心智、权益和偏好三段一次性灌给客户" in prompt
    assert "仅仅回答了未来鉴赏偏好" in prompt
    assert "每周上新、专属折扣、实拍或优惠推送属于动态运营事实" in prompt


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
