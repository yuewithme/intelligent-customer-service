from types import SimpleNamespace

import pytest

from app.domains.decisioning.services.agent_prompt import build_system_prompt
from app.domains.decisioning.schemas.agent import AgentTurnDecision
from app.domains.decisioning.services import agent_runtime
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
    assert "不是要求同一轮连续发送的三段话" in instructions
    assert "绝不能在同一轮同时出现持续咨询、会员钩子和偏好提问三项" in instructions
    assert "不触发 product.search、商品推荐或卡片" in instructions
    assert "用 memory.record 保存" in instructions
    assert "当前问题未解决" in instructions


def test_service_prompt_separates_preference_collection_from_purchase_intent():
    prompt = build_system_prompt(sop_scope="service")

    assert "主动轻量询问一个尚未知的花色、瓣型、香味或品种鉴赏偏好" in prompt
    assert "这是长期偏好采集，不代表客户现在有购买意向" in prompt
    assert "单轮最多组合两个相邻动作，绝不能三项同轮" in prompt
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


def test_runtime_allows_general_member_hooks_but_blocks_invented_details():
    general = AgentTurnDecision.model_validate(
        {
            "commercial_judgment": "问题已解决，可以传达会员吸引点",
            "relationship_purpose": "为后续鉴赏分享留下关系触点",
            "customer_signal": "none",
            "purchase_signal": "none",
            "tool_calls": [],
            "final_response": {
                "messages": [
                    {
                        "type": "text",
                        "content": "咱们每周都会上新多款铭品供兰友鉴赏，也会有会员专属折扣活动，后面有实拍图和优惠我再给您分享。",
                    }
                ],
                "need_human": False,
            },
        }
    )
    invented = AgentTurnDecision.model_validate(
        {
            "commercial_judgment": "客户追问具体活动",
            "relationship_purpose": "回答活动",
            "customer_signal": "none",
            "purchase_signal": "none",
            "tool_calls": [],
            "final_response": {
                "messages": [
                    {
                        "type": "text",
                        "content": "我们每周三上新10款，会员统一8折。",
                    }
                ],
                "need_human": False,
            },
        }
    )
    context = _context()

    assert agent_runtime._guard_violations(general, context) == []
    assert "unverified_member_promotion_detail" in agent_runtime._guard_violations(
        invented, context
    )


def test_runtime_blocks_three_post_service_seed_actions_in_one_turn():
    overloaded = AgentTurnDecision.model_validate(
        {
            "commercial_judgment": "问题解决后经营长期关系",
            "relationship_purpose": "留下咨询心智、会员钩子和偏好",
            "customer_signal": "none",
            "purchase_signal": "none",
            "tool_calls": [],
            "final_response": {
                "messages": [
                    {
                        "type": "text",
                        "content": "后面有变化随时发图给我。咱们每周都会上新，也有会员专属折扣。您更偏爱哪种香味？",
                    }
                ],
                "need_human": False,
            },
        }
    )

    violations = agent_runtime._guard_violations(overloaded, _context())
    assert "overloaded_post_service_seed" in violations
    instruction = agent_runtime._hard_rewrite_instruction(violations)
    assert "必须真正删掉一项" in instruction


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
