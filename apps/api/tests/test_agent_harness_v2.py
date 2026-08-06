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
async def test_customer_tag_records_evidence_backed_catalog_tag(monkeypatch):
    from app.domains.customers.services import user_profile_service

    recorded = {}

    async def fake_add_ai_customer_tag(user_id, tag, *, reason, trace_id=None):
        recorded.update(
            user_id=user_id,
            tag=tag,
            reason=reason,
            trace_id=trace_id,
        )
        return {"customer_tags": ["1-10盆", "建兰"]}

    monkeypatch.setattr(
        user_profile_service,
        "add_ai_customer_tag",
        fake_add_ai_customer_tag,
    )
    context = AgentExecutionContext(
        message=_message("我现在养了8盆，主要是建兰"),
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {}},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="tag-1",
        name="customer.tag",
        arguments={"tag": "1-10盆", "evidence": "养了8盆"},
        context=context,
    )

    assert result.status == "recorded"
    assert result.data["persisted"] is True
    assert context.workspace["profile"]["customer_tags"] == ["1-10盆", "建兰"]
    assert recorded == {
        "user_id": "customer-1",
        "tag": "1-10盆",
        "reason": "agent_customer_evidence",
        "trace_id": "trace-agent-v2",
    }


@pytest.mark.asyncio
async def test_brand_service_facts_returns_verified_two_part_delivery():
    context = AgentExecutionContext(
        message=_message("我反复养不好，也一直没人教"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="brand-1",
        name="brand.service_facts",
        arguments={},
        context=context,
    )

    assert result.status == "found"
    assert [item["name"] for item in result.data["delivery"]] == [
        "对应品种的单品养护教程",
        "养兰师傅一对一实操指导",
    ]
    assert "有些商家" in result.data["comparison_boundary"]
    assert "所有同行" in result.data["comparison_boundary"]


def test_paid_order_detection_excludes_unpaid_and_closed_orders():
    assert agent_tools._has_verified_paid_order(
        {
            "ok": True,
            "data": {
                "status": "found",
                "orders": [{"order_no": "E1", "status": "WAIT_SELLER_SEND_GOODS"}],
            },
        }
    )
    assert not agent_tools._has_verified_paid_order(
        {
            "ok": True,
            "data": {
                "status": "found",
                "orders": [
                    {"order_no": "E2", "status": "WAIT_BUYER_PAY"},
                    {"order_no": "E3", "status": "TRADE_CLOSED"},
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_verified_paid_order_records_wechat_purchase_tag(monkeypatch):
    from app.domains.customers.services import user_profile_service

    recorded = {}

    async def fake_add_verified_customer_tag(user_id, tag, *, reason, trace_id=None):
        recorded.update(
            user_id=user_id,
            tag=tag,
            reason=reason,
            trace_id=trace_id,
        )
        return {"customer_tags": ["L2 白银期", "微信已购"]}

    monkeypatch.setattr(
        user_profile_service,
        "add_verified_customer_tag",
        fake_add_verified_customer_tag,
    )
    context = AgentExecutionContext(
        message=_message("帮我查一下订单"),
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {"customer_tags": ["L2 白银期"]}},
    )

    await agent_tools._record_verified_wechat_purchase(
        context,
        {
            "ok": True,
            "data": {
                "status": "found",
                "order": {"order_no": "E1", "status": "TRADE_SUCCESS"},
            },
        },
    )

    assert recorded["tag"] == "微信已购"
    assert recorded["reason"] == "youzan_order_tool_verified_paid_order"
    assert context.workspace["profile"]["customer_tags"] == [
        "L2 白银期",
        "微信已购",
    ]


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
async def test_agent_uses_pot_count_and_pain_to_bridge_to_guidance_gap(monkeypatch):
    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "8盆建兰反复黄叶，常见要一起排查根系、浇水和通风，光补肥往往解决不了。",
                    },
                    {
                        "type": "text",
                        "content": "您买回来以后，原来的商家有继续指导您怎么养吗？",
                    },
                ],
                "need_human": False,
            },
            judgment="客户已给出具体盆数和黄叶痛点，足以停止横向盘问并确认持续指导缺口",
            purpose="先让客户理解问题框架，再自然确认陪伴服务需求",
        )

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("我现在有8盆建兰，最近总是黄叶"),
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {"customer_tags": ["1-10盆", "建兰"]}},
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "text"]
    assert "原来的商家有继续指导" in reply.outbound_messages[1].content


@pytest.mark.asyncio
async def test_agent_recommends_companion_service_after_guidance_gap(monkeypatch):
    service = {
        "item_id": "service-1",
        "title": "陪伴养兰服务",
        "price_cent": 9900,
        "stock": 20,
        "status": "online",
        "h5_url": "https://shop.example.com/companion",
        "knowledge": {
            "product_name": "陪伴养兰服务",
            "highlighted_features": ["单品知识", "实时指导"],
        },
    }
    monkeypatch.setattr(agent_tools, "search_catalog_products", lambda query, limit=3: [service])
    monkeypatch.setattr(agent_tools, "get_catalog_product", lambda item_id: service)
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "service-search",
                        "name": "product.search",
                        "arguments": {"query": "陪伴养兰服务", "limit": 2},
                    }
                ],
                judgment="盆数、黄叶痛点和无人指导已经明确，适合主动匹配陪伴养兰服务",
                purpose="从问题解释进入可落地的持续指导方案",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-card",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                judgment="真实服务已查询且与客户反复黄叶和缺少指导匹配",
                purpose="说明具体服务价值并给出购买入口",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "您之前主要靠自己摸索，问题才容易反复。我们会按具体品种告诉您关键养法，遇到黄叶这类情况还能结合实际情况继续指导，能少走很多弯路。",
                        },
                        {"type": "prepared", "ref": "service-card"},
                    ],
                    "need_human": False,
                },
                judgment="客户的服务缺口已确认，无需继续盘问",
                purpose="推荐真实陪伴服务并提供购买下一步",
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("以前都是自己摸索，卖家也没教过"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "profile": {
                "customer_tags": ["1-10盆", "L2 白银期", "建兰"],
                "pain_points": ["建兰反复黄叶"],
            },
            "recent_turns": [
                {"role": "customer", "content": "家里有8盆建兰，最近总是黄叶"},
            ],
        },
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    assert "具体品种" in reply.outbound_messages[0].content
    assert json.loads(reply.outbound_messages[1].content)["title"] == "陪伴养兰服务"


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


def test_style_flags_do_not_become_hard_violations():
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

    assert agent_runtime._guard_violations(decision, context) == []
    assert set(agent_runtime._quality_flags(decision)) == {
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
    assert agent_runtime._quality_flags(decision) == []


def test_natural_compound_question_is_not_counted_by_question_words():
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "您以前有没有专门学过养兰，还是主要凭自己的感觉摸索呀？",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._quality_flags(decision) == []


@pytest.mark.asyncio
async def test_runtime_sends_quality_flagged_reply_without_retry(monkeypatch):
    calls = 0

    async def fake_generate(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "您说的“老叶发黄”我明白。\n1. 是底部老叶吗？\n2. 最近有没有施肥？",
                    }
                ],
                "need_human": False,
            }
        )

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("主要是老叶发黄，这种怎么处理"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert calls == 1
    assert reply.route == "agent"
    assert "老叶发黄" in reply.answer
    assert set(reply.metadata["agent_runtime"]["quality_flags"]) == {
        "too_many_customer_questions",
        "non_conversational_list_style",
        "unnecessary_customer_quotes",
    }


@pytest.mark.asyncio
async def test_tool_rounds_append_only_new_results(monkeypatch):
    snapshots = []
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "discover-1",
                        "name": "capability.search",
                        "arguments": {"query": "养兰服务"},
                    }
                ]
            ),
            _decision(
                final={
                    "messages": [{"type": "text", "content": "我先按您现在的情况帮您理清。"}],
                    "need_human": False,
                }
            ),
        ]
    )

    async def fake_generate(messages, **kwargs):
        del kwargs
        snapshots.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    await agent_runtime.run_sales_agent(
        message=_message("养不好怎么办"),
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {"customer_tags": ["1-10盆"]}},
    )

    first_user_messages = [m for m in snapshots[0] if m["role"] == "user"]
    second_user_messages = [m for m in snapshots[1] if m["role"] == "user"]
    assert len(first_user_messages) == 1
    assert len(second_user_messages) == 2
    assert "customer_workspace" in second_user_messages[0]["content"]
    assert "new_tool_results" in second_user_messages[1]["content"]
    assert "customer_workspace" not in second_user_messages[1]["content"]


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
                        "content": "为了后面给您更贴合的养护建议和资料，我先了解一下，您家里现在大概养了多少盆，主要都是什么品种呀？",
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
        "opening_identity_missing",
        "opening_needs_question_invalid",
        "opening_profile_question_invalid",
    }
    assert agent_runtime._quality_flags(invalid) == [
        "too_many_customer_questions"
    ]

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
    assert set(agent_runtime._guard_violations(pushy, context)) == {
        "opening_profile_question_invalid",
        "opening_sales_push_question",
    }


def test_harness_collects_match_facts_before_product_and_material_release():
    prompt = build_system_prompt()
    assert "新客户开场优先了解当前盆数和主要品种" in prompt
    assert "能否用客户已经说过的事实" in prompt
    assert "也可以主动查询并推荐合适商品" in prompt
    assert "先用一句客户收益说明回答后能得到什么" in prompt
    assert "新建一个 text 消息" in prompt
    assert "客户索要资料是兴趣信号，不是自动发送指令" in prompt
    assert "发送后必须有主动的下一步" in prompt
    assert "新客户开场优先了解当前盆数和主要品种" in prompt
    assert "L1-L6 客户等级、盆数和品种标签" in prompt
    assert "两个标签可以同时存在" in prompt
    assert "这只是一次专业答疑，不要擅自升级成诊断会诊" in prompt
    assert "同一个技术细节最多追问一轮" in prompt
    assert "未知但不阻塞" in prompt
    assert "不把“等待客户继续反馈该细节”写进 next_action" in prompt
    assert "顺序不能跳过" in prompt
    assert "调用 brand.service_facts 核实" in prompt
    assert "先挑几盆" in prompt
    assert "市面上有些商家卖完后缺少持续养护承接" in prompt

    experience = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.relationship_before_product"
    )
    assert "只收集会影响匹配的客户事实" in experience.instructions
    assert "应自然转入真实商品查询和主动推荐" in experience.instructions

    discovery = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.need_discovery"
    )
    assert "足以支撑销售匹配的客户事实" in discovery.instructions
    assert "主动推品" in discovery.instructions
    assert "问题不是每轮默认动作" in discovery.instructions
    assert "普通概念或养护知识答疑不是诊断会诊" in discovery.instructions
    assert "不再索图" in discovery.instructions

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

    leveling = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.customer_leveling"
    )
    assert "L1-L6 是可修正的客户理解" in leveling.instructions
    assert "单个关键词不能决定等级" in leveling.instructions

    service_fit = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.companion_service_fit"
    )
    assert "盆数是前期分层入口" in service_fit.instructions
    assert "已有具体盆数和痛点通常足以" in service_fit.instructions
    assert "单品知识" in service_fit.instructions
    assert "实时指导" in service_fit.instructions
    assert "优先经验而非硬阈值" in service_fit.instructions
    assert "固定价值顺序" in service_fit.instructions
    assert "对应品种的单品养护教程" in service_fit.instructions
    assert "不让客户在挑苗和发资料之间选择流程" in service_fit.instructions

    service_facts = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "brand.service_facts"
    )
    assert "已核实买后服务事实" in service_facts.instructions
    assert "一对一指导" in service_facts.instructions


def test_service_value_guard_blocks_offer_before_full_value_sequence():
    context = AgentExecutionContext(
        message=_message("养在阳台，但是这里也有自然风吹"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {
                    "role": "user",
                    "content": "我养了十几盆，总是反复养不好，买完也没人指导",
                },
                {
                    "role": "assistant",
                    "content": "您之前养在室内还是有自然风的地方？",
                },
            ]
        },
    )
    premature = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "我给您挑适合杭州的壮苗，带着您把浇水节奏找准。您想先挑两盆试试，还是我先发一份养护要点？",
                    }
                ],
                "need_human": False,
                "next_action": "根据客户选择调用product.search或material.send",
            }
        )["data"]
    )
    complete = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "反复养不好，常见是一开始苗没选对，买回去又缺少一对一指导。市面上有些商家卖完后缺少承接，我们更看重您买回去能不能养好。",
                    },
                    {
                        "type": "text",
                        "content": "每个结缘品种有对应的单品养护教程；实操不懂时，再由养兰师傅结合环境一对一指导。接下来我按杭州阳台环境给您选苗。",
                    },
                ],
                "need_human": False,
                "next_action": "查询适合杭州阳台和当前经验的建兰",
            }
        )["data"]
    )

    assert agent_runtime._service_value_sequence_violations(
        premature, context
    ) == ["premature_offer_before_service_value"]
    assert agent_runtime._service_value_sequence_violations(complete, context) == []


def test_specific_brand_service_claim_requires_verified_tool_facts():
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "每个结缘品种都有单品养护教程，看完不懂再由养兰师傅一对一指导。",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )
    unverified = AgentExecutionContext(
        message=_message("你们怎么教"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    verified = AgentExecutionContext(
        message=_message("你们怎么教"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
        tool_facts={
            "brand-1": {
                "tool": "brand.service_facts",
                "status": "found",
                "data": {},
            }
        },
    )

    assert "unverified_brand_service_claim" in agent_runtime._guard_violations(
        decision, unverified
    )
    assert agent_runtime._guard_violations(decision, verified) == []


@pytest.mark.asyncio
async def test_runtime_rewrites_premature_offer_into_service_value_sequence(
    monkeypatch,
):
    snapshots = []
    responses = iter(
        [
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "我给您挑适合杭州的壮苗，带着您养。您想先挑两盆试试，还是我发一份养护要点？",
                        }
                    ],
                    "need_human": False,
                    "next_action": "根据客户选择调用product.search或material.send",
                }
            ),
            _decision(
                tools=[
                    {
                        "call_id": "brand-1",
                        "name": "brand.service_facts",
                        "arguments": {},
                    }
                ],
                judgment="客户反复养不好，先核实并讲清买后服务价值",
                purpose="建立选苗加持续指导的完整价值认知",
            ),
            _decision(
                judgment="客户已有十几盆且反复养不好，服务价值已可完整塑造",
                purpose="让客户理解萧岚苑如何从理论和实操两层承接养护",
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "反复养不好，常见是一开始苗没选对，买回去又缺少一对一指导。市面上有些商家卖完后缺少承接，我们更看重您买回去能不能养好。",
                        },
                        {
                            "type": "text",
                            "content": "每个结缘品种都有对应的单品养护教程，先把上盆、浇水、施肥和防病讲清；实操有不懂，再由养兰师傅结合杭州阳台环境一对一指导。下一步我按这个条件给您选苗。",
                        },
                    ],
                    "need_human": False,
                    "next_action": "查询适合杭州阳台、已有十几盆基础客户的建兰",
                },
            ),
        ]
    )

    async def fake_generate(messages, **kwargs):
        del kwargs
        snapshots.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("养在阳台，但是这里也有自然风吹"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {
                    "role": "user",
                    "content": "我养了十几盆，总是反复养不好，买完也没人指导",
                }
            ]
        },
    )

    assert "苗没选对" in reply.answer
    assert "单品养护教程" in reply.answer
    assert "一对一指导" in reply.answer
    assert "还是我发" not in reply.answer
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "trajectory_rewrite_requested",
        "tool_calls_executed",
        "accepted",
    ]
    assert reply.metadata["agent_runtime"]["tool_trace"][0]["tool"] == (
        "brand.service_facts"
    )
    assert "不要把销售流程选择权交给客户" in snapshots[1][-1]["content"]


def test_trajectory_guard_stops_non_core_detail_after_customer_cannot_answer():
    context = AgentExecutionContext(
        message=_message("植料是什么？我不太懂，反正商家给我的我就用着了"),
        user_state=UserState(user_id="customer-1"),
        workspace={"recent_turns": []},
    )
    drilling = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "植料要透气沥水。您倒出来看，是细土还是树皮和石子呀？",
                    }
                ],
                "need_human": False,
                "next_action": "等待客户反馈植料形态",
            }
        )["data"]
    )
    high_value_discovery = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "植料就是兰花根系的生长介质，关键是透气沥水，您现在大概养了多少盆，主要是什么品种呀？",
                    }
                ],
                "need_human": False,
                "next_action": "补齐盆数和品种后判断推荐方向",
            }
        )["data"]
    )

    assert agent_runtime._sales_trajectory_violations(drilling, context) == [
        "customer_cannot_answer_non_core_followup"
    ]
    assert agent_runtime._sales_trajectory_violations(
        high_value_discovery, context
    ) == []


def test_trajectory_guard_catches_repeated_topic_in_visible_reply_and_next_action():
    context = AgentExecutionContext(
        message=_message("小石子吧"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {
                    "role": "user",
                    "content": "植料是什么？我不太懂，反正商家给我的我就用着了",
                },
                {
                    "role": "assistant",
                    "content": "您之前用的植料，是细土还是树皮和小石子呀？",
                },
                {"role": "user", "content": "是植料吧，这个有什么讲究吗"},
            ]
        },
    )
    asks_for_photo = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "小石子如果颗粒较大，至少沥水会好一些。您拍张盆面照片，或者拨开看看下面是大颗粒还是细粉末状的？",
                    }
                ],
                "need_human": False,
                "next_action": "等待客户反馈植料颗粒形态",
            }
        )["data"]
    )
    waits_without_visible_question = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {"type": "text", "content": "小石子颗粒大、沥水快，方向上没问题。"}
                ],
                "need_human": False,
                "next_action": "等待客户继续反馈盆面植料颗粒形态",
            }
        )["data"]
    )

    assert "repeated_non_core_topic_followup" in (
        agent_runtime._sales_trajectory_violations(asks_for_photo, context)
    )
    assert "repeated_non_core_topic_followup" in (
        agent_runtime._sales_trajectory_violations(
            waits_without_visible_question, context
        )
    )


def test_trajectory_guard_allows_core_diagnostic_question_for_active_damage():
    context = AgentExecutionContext(
        message=_message("兰花已经烂根发臭了，植料我也不懂"),
        user_state=UserState(user_id="customer-1"),
        workspace={"recent_turns": []},
    )
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "先停水并隔离。现在植料是一直湿黏，还是还能正常沥水？",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )

    assert agent_runtime._sales_trajectory_violations(decision, context) == []


@pytest.mark.asyncio
async def test_runtime_rewrites_stalled_detail_toward_recommendation_readiness(
    monkeypatch,
):
    calls = []
    responses = iter(
        [
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "好的植料要透气沥水。您之前用的是细土还是树皮和小石子呀？",
                        }
                    ],
                    "need_human": False,
                    "next_action": "等待客户反馈植料形态",
                }
            ),
            _decision(
                judgment="植料细节不阻塞推荐，转而补齐客户层级与品种信息",
                purpose="用专业回答建立信任并推进推荐准备",
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "植料主要看透气和沥水，商家原配的先不用反复折腾。您现在大概养了多少盆，主要是什么品种呀？",
                        }
                    ],
                    "need_human": False,
                    "next_action": "补齐盆数和品种后判断推荐方向",
                },
            ),
        ]
    )

    async def fake_generate(messages, **kwargs):
        del kwargs
        calls.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("植料是什么？我不太懂，反正商家给我的我就用着了"),
        user_state=UserState(user_id="customer-1"),
        workspace={"recent_turns": []},
    )

    assert len(calls) == 2
    assert "多少盆" in reply.answer
    assert "细土还是" not in reply.answer
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "trajectory_rewrite_requested",
        "accepted",
    ]
    assert "偏离了推进成交的目标" in calls[1][-1]["content"]


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
                            "content": "为了后面给您更贴合的养护建议和资料，我先了解一下，您家里现在大概养了多少盆，主要都是什么品种呀？",
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

    assert reply.reply_type == "human"
    assert reply.route == "human"
    assert reply.outbound_messages == []
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert len(attempts) == 2
    assert attempts[0]["outcome"] == "hard_rewrite_requested"
    assert attempts[1]["outcome"] == "hard_blocked"


@pytest.mark.asyncio
async def test_hard_guard_rewrites_once_and_keeps_safe_agent_reply(monkeypatch):
    responses = iter(
        [
            _decision(
                final={
                    "messages": [{"type": "text", "content": "这款现在99元。"}],
                    "need_human": False,
                }
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "具体价格我先按真实商品帮您查清楚，再给您准确答复。",
                        }
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
        message=_message("多少钱"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert reply.route == "agent"
    assert "真实商品" in reply.answer
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "hard_rewrite_requested",
        "accepted",
    ]
    assert reply.metadata["agent_runtime"]["result"] == "sent"


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
