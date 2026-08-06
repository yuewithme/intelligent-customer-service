import json

import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.agent import AgentTurnDecision
from app.domains.decisioning.services import agent_runtime, agent_tools
from app.domains.decisioning.services.agent_prompt import build_system_prompt
from app.domains.decisioning.services.agent_tools import AgentExecutionContext


def _message(text: str = "我想看看建兰") -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace-agent-v2",
        channel="api",
        user_id="customer-1",
        session_id="session-1",
        message=text,
        kb_id="kb_default",
        metadata={},
    )


def _decision(*, tools=None, final=None, judgment="判断", purpose="推进关系"):
    return {
        "data": {
            "commercial_judgment": judgment,
            "relationship_purpose": purpose,
            "customer_signal": "none",
            "tool_calls": tools or [],
            "final_response": final,
        },
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_agent_discovers_capability_then_replies(monkeypatch):
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "discover-1",
                        "name": "capability.search",
                        "arguments": {"query": "客户问养护问题，需要专业知识"},
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {"type": "text", "content": "您先别急着施肥，我先帮您把黄叶原因判断清楚。"}
                    ],
                    "need_human": False,
                }
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("叶子发黄要不要施肥"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert reply.route == "agent"
    assert reply.answer.startswith("您先别急着施肥")
    trace = reply.metadata["agent_runtime"]["tool_trace"]
    assert trace[0]["tool"] == "capability.search"
    assert any(
        item["name"] == "knowledge.search"
        for item in trace[0]["data"]["capabilities"]
    )


@pytest.mark.asyncio
async def test_agent_splits_explanation_and_follow_up_into_message_units(monkeypatch):
    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": (
                            "先别急，根系反复出问题通常和浇水、植料或通风有关。\n\n"
                            "您现在用的是普通泥土，还是颗粒植料？"
                        ),
                    }
                ],
                "need_human": False,
            }
        )

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("盆土一直湿漉漉的，兰花也总是烂根"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "text"]
    assert [item.content for item in reply.outbound_messages] == [
        "先别急，根系反复出问题通常和浇水、植料或通风有关。",
        "您现在用的是普通泥土，还是颗粒植料？",
    ]
    assert reply.answer_segments == [item.content for item in reply.outbound_messages]


@pytest.mark.asyncio
async def test_agent_can_prepare_and_place_verified_product_card(monkeypatch):
    product = {
        "item_id": "item-39",
        "title": "建兰红君荷",
        "price_cent": 3900,
        "stock": 8,
        "status": "onsale",
        "h5_url": "https://h5.youzan.com/goods/item-39",
        "image_url": "https://cdn.example.com/item-39.jpg",
        "knowledge": {"product_name": "红君荷", "category": "建兰"},
    }
    monkeypatch.setattr(agent_tools, "search_catalog_products", lambda query, limit: [product])
    monkeypatch.setattr(agent_tools, "get_catalog_product", lambda item_id: product if item_id == "item-39" else None)
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "search-1",
                        "name": "product.search",
                        "arguments": {"query": "建兰红君荷", "limit": 3},
                    }
                ]
            ),
            _decision(
                tools=[
                    {
                        "call_id": "card-1",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:item-39"},
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {"type": "text", "content": "这款现在是39元，我把真实商品卡片放下面，您可以直接看详情。"},
                        {"type": "prepared", "ref": "card-1"},
                    ],
                    "need_human": False,
                },
                judgment="客户已明确选择，直接推进成交",
                purpose="核实后给购买入口",
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("就要红君荷，链接发我"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    card = json.loads(reply.outbound_messages[1].content)
    assert card["url"] == product["h5_url"]
    assert "39元" in reply.answer


@pytest.mark.asyncio
async def test_agent_holds_material_while_match_facts_are_unknown(monkeypatch):
    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "这份资料会讲新手入门和常见养护问题，我先看看哪部分对您最有用。您家里已经养着兰花，还是准备从第一盆开始？",
                    }
                ],
                "need_human": False,
            },
            judgment="客户索要资料但养兰经验和需求未知，还不足以判断资料释放和商品匹配",
            purpose="借资料兴趣先了解客户的养兰起点",
        )

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("你们直播间说有资料的，我要资料"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text"]
    assert "哪部分对您最有用" in reply.answer
    assert reply.answer.endswith("？")


@pytest.mark.asyncio
async def test_agent_releases_earned_material_then_proactively_recommends(monkeypatch):
    async def material_not_recently_sent(*args, **kwargs):
        del args, kwargs
        return False

    product = {
        "item_id": "item-beginner",
        "title": "新手入门建兰",
        "price_cent": 16800,
        "stock": 6,
        "status": "onsale",
        "h5_url": "https://h5.youzan.com/goods/item-beginner",
        "image_url": "https://cdn.example.com/item-beginner.jpg",
        "knowledge": {
            "product_name": "新手入门建兰",
            "category": "建兰",
            "suitable_for": "新手、通风阳台",
        },
    }
    monkeypatch.setattr(
        agent_tools,
        "search_catalog_products",
        lambda query, limit: [product],
    )
    monkeypatch.setattr(
        agent_tools,
        "get_catalog_product",
        lambda item_id: product if item_id == "item-beginner" else None,
    )

    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "material-search-1",
                        "name": "material.search",
                        "arguments": {"query": "新手养兰资料", "limit": 3},
                    },
                    {
                        "call_id": "product-search-1",
                        "name": "product.search",
                        "arguments": {
                            "query": "南向通风阳台新手第一盆好养兰花",
                            "limit": 3,
                        },
                    },
                ]
            ),
            _decision(
                tools=[
                    {
                        "call_id": "material-send-1",
                        "name": "material.send",
                        "arguments": {
                            "material_ref": "material:orchid-companion"
                        },
                    },
                    {
                        "call_id": "product-card-1",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:item-beginner"},
                    },
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "您是刚入门，又是南向通风阳台，这份资料里的新手养护和浇水部分正好适合您先看。",
                        },
                        {"type": "prepared", "ref": "material-send-1"},
                        {
                            "type": "text",
                            "content": "按您想从第一盆好养的开始，我更建议这款适合新手和通风阳台的建兰，我把真实商品卡片也放下面。",
                        },
                        {"type": "prepared", "ref": "product-card-1"},
                    ],
                    "need_human": False,
                },
                judgment="已知客户是新手、南向通风阳台、想选第一盆好养兰花，足以匹配入门资料并主动推荐新手建兰",
                purpose="用已经收集的信息完成价值释放并转入合适商品推荐",
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    monkeypatch.setattr(
        agent_tools, "_material_recently_sent", material_not_recently_sent
    )
    reply = await agent_runtime.run_sales_agent(
        message=_message("我就是想先学习，你把那份资料发我看看"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {"role": "user", "content": "我还没养过，想从第一盆好养的开始。"},
                {"role": "user", "content": "家里是南向阳台，通风还可以。"},
            ]
        },
    )

    assert [item.type for item in reply.outbound_messages] == [
        "text",
        "link_card",
        "text",
        "link_card",
    ]
    material_card = json.loads(reply.outbound_messages[1].content)
    product_card = json.loads(reply.outbound_messages[3].content)
    assert material_card["title"] == "萧岚苑陪伴养兰资料"
    assert product_card["title"] == "新手入门建兰"
    assert "您是刚入门" in reply.outbound_messages[0].content
    assert "更建议这款" in reply.outbound_messages[2].content


@pytest.mark.asyncio
async def test_prepared_card_is_not_sent_when_agent_does_not_place_it(monkeypatch):
    product = {
        "item_id": "item-39",
        "title": "建兰红君荷",
        "price_cent": 3900,
        "stock": 8,
        "status": "onsale",
        "h5_url": "https://h5.youzan.com/goods/item-39",
        "image_url": "https://cdn.example.com/item-39.jpg",
        "knowledge": {},
    }
    monkeypatch.setattr(
        agent_tools,
        "get_catalog_product",
        lambda item_id: product if item_id == "item-39" else None,
    )
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "card-1",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:item-39"},
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {"type": "text", "content": "您这个顾虑我明白，我们先把规格确认清楚。"}
                    ],
                    "need_human": False,
                }
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("先等等，我想确认规格"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text"]


def test_verified_order_shipment_is_not_mistaken_for_card_delivery_claim():
    context = AgentExecutionContext(
        message=_message("订单到哪了"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
        tool_facts={
            "order-1": {
                "tool": "order.get",
                "status": "found",
                "data": {"status": "已发货"},
            }
        },
    )
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [{"type": "text", "content": "您的订单已发货。"}],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._guard_violations(decision, context) == []


def test_hard_guard_requires_exact_verified_stock_and_url():
    context = AgentExecutionContext(
        message=_message("还有多少，链接发我"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
        tool_facts={
            "product-1": {
                "tool": "product.get",
                "status": "found",
                "data": {
                    "product": {
                        "stock": 8,
                        "h5_url": "https://h5.youzan.com/goods/item-39",
                    }
                },
            }
        },
    )
    verified = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "库存还剩8株，链接是 https://h5.youzan.com/goods/item-39",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )
    fabricated = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "库存还剩3株，链接是 https://fake.example/item-39",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._guard_violations(verified, context) == []
    assert set(agent_runtime._guard_violations(fabricated, context)) == {
        "unverified_stock_count",
        "unverified_url",
    }


def test_style_guard_rejects_multi_question_list_and_quotes():
    context = AgentExecutionContext(
        message=_message("总是养死，怎么办"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": (
                            "我先了解一下：\n"
                            "1. 您养的是哪种兰花？\n"
                            "2. 平时放室内还是室外？\n"
                            "3. 多久浇一次水？"
                        ),
                    },
                    {"type": "text", "content": "先别按“见干见湿”硬套。"},
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert set(agent_runtime._guard_violations(decision, context)) == {
        "too_many_customer_questions",
        "non_conversational_list_style",
        "unnecessary_customer_quotes",
    }


def test_style_guard_allows_one_natural_question():
    context = AgentExecutionContext(
        message=_message("总是养死，怎么办"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "先别急，这种反复养死通常要先排查浇水和通风。您平时大概多久浇一次水？",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._guard_violations(decision, context) == []


def test_opening_guard_requires_identity_and_one_needs_question():
    message = _message("[系统新好友建立]")
    message.metadata = {"system_event": "first_contact"}
    context = AgentExecutionContext(
        message=message,
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    valid = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "您好，我是萧岚苑的小兰，我们团队平时主要做兰花，养护上拿不准都可以找我。",
                    },
                    {
                        "type": "text",
                        "content": "我先了解一下您的情况，后面给您的养护建议和资料也能更贴合。您是刚接触兰花，还是家里已经养了一些？",
                    },
                ],
                "need_human": False,
            }
        )["data"]
    )
    invalid = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {"type": "text", "content": "您好，我可以帮您。"},
                    {"type": "text", "content": "您养什么品种？平时多久浇一次？"},
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._guard_violations(valid, context) == []
    assert set(agent_runtime._guard_violations(invalid, context)) == {
        "too_many_customer_questions",
        "opening_identity_missing",
        "opening_needs_question_invalid",
    }

    pushy = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {"type": "text", "content": "您好，我是萧岚苑的小兰。"},
                    {"type": "text", "content": "您现在想先看花还是选花？"},
                ],
                "need_human": False,
            }
        )["data"]
    )
    assert agent_runtime._guard_violations(pushy, context) == [
        "opening_sales_push_question"
    ]


def test_harness_collects_match_facts_before_product_and_material_release():
    prompt = build_system_prompt()
    assert "新客户前段的主要任务是逐步理解这个人" in prompt
    assert "能否用客户已经说过的事实" in prompt
    assert "也可以主动查询并推荐合适商品" in prompt
    assert "先用一句客户收益说明回答后能得到什么" in prompt
    assert "新建一个 text 消息" in prompt
    assert "客户索要资料是兴趣信号，不是自动发送指令" in prompt
    assert "发送后必须有主动的下一步" in prompt

    experience = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.relationship_before_product"
    )
    assert "每轮收集一项会影响匹配的客户事实" in experience.instructions
    assert "应自然转入真实商品查询和主动推荐" in experience.instructions

    discovery = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.need_discovery"
    )
    assert "足以支撑销售匹配的客户事实" in discovery.instructions
    assert "转入主动推品" in discovery.instructions

    material = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.material_value"
    )
    assert "不是客户一索要就自动发送" in material.instructions
    assert "本轮不发资料" in material.instructions
    assert "转入有依据的推品" in material.instructions

    objection = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.objection"
    )
    assert "是礼貌性软收口，不是明确拒绝" in objection.instructions
    assert "换一个更容易回答的角度" in objection.instructions


@pytest.mark.asyncio
async def test_opening_does_not_execute_agent_tools(monkeypatch):
    message = _message("[系统新好友建立]")
    message.metadata = {"system_event": "first_contact"}
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "search-1",
                        "name": "product.search",
                        "arguments": {"query": "兰花"},
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "您好，我是萧岚苑的小兰，我们团队平时主要做兰花，养护上拿不准都可以找我。",
                        },
                        {
                            "type": "text",
                            "content": "我先了解一下您的情况，后面给您的养护建议和资料也能更贴合。您是刚接触兰花，还是家里已经养了一些？",
                        },
                    ],
                    "need_human": False,
                }
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    async def fail_if_tool_executes(*args, **kwargs):
        del args, kwargs
        raise AssertionError("opening must not execute tools")

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    monkeypatch.setattr(agent_runtime, "execute_agent_tool", fail_if_tool_executes)
    reply = await agent_runtime.run_sales_agent(
        message=message,
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "image", "text"]
    assert reply.answer.startswith("您好，我是萧岚苑的小兰")


@pytest.mark.asyncio
async def test_hard_guard_blocks_ungrounded_price(monkeypatch):
    unsafe = _decision(
        final={
            "messages": [{"type": "text", "content": "这款现在99元，库存充足。"}],
            "need_human": False,
        }
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return unsafe

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("多少钱"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert reply.reply_type == "sales_agent_fallback"
    assert "99元" not in reply.answer
    assert "库存充足" not in reply.answer


@pytest.mark.asyncio
async def test_discount_application_uses_human_handoff(monkeypatch):
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "handoff-1",
                        "name": "human.handoff",
                        "arguments": {
                            "reason": "discount_application",
                            "summary": "客户已有购买兴趣，希望争取福利；当前没有真实活动结果。",
                        },
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [
                        {"type": "text", "content": "这个价格已经比较实在了，我再帮您问问，看还能不能争取一点福利。"}
                    ],
                    "need_human": True,
                    "handoff_reason": "discount_application",
                }
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("还能优惠点吗"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert reply.need_human is True
    assert reply.metadata["handoff"]["reason"] == "discount_application"
    assert "申请成功" not in reply.answer
