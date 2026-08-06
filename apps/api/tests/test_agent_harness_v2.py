import json

import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.agent import AgentTurnDecision
from app.domains.decisioning.services import agent_runtime, agent_tools
from app.domains.decisioning.services.agent_prompt import (
    build_system_prompt,
    build_tool_result_payload,
    build_turn_payload,
)
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


def _decision(
    *,
    tools=None,
    final=None,
    judgment="判断",
    purpose="推进关系",
    purchase_signal="none",
):
    return {
        "data": {
            "commercial_judgment": judgment,
            "relationship_purpose": purpose,
            "customer_signal": "none",
            "purchase_signal": purchase_signal,
            "tool_calls": tools or [],
            "final_response": final,
        },
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }


def test_agent_decision_rejects_tool_calls_with_final_response():
    with pytest.raises(
        ValueError,
        match="tool calls and final response are mutually exclusive",
    ):
        AgentTurnDecision.model_validate(
            _decision(
                tools=[
                    {
                        "call_id": "service-card",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "两三天浇一次偏勤，我再把适合您的服务放下面。",
                        }
                    ],
                    "need_human": False,
                },
            )["data"]
        )


def test_tool_result_payload_marks_draft_and_prepared_items_as_unsent():
    payload = json.loads(
        build_tool_result_payload(
            [
                {
                    "call_id": "service-card",
                    "tool": "product.send_card",
                    "status": "prepared",
                }
            ]
        )
    )

    assert payload["delivery_state"] == {
        "customer_visible_messages_sent_in_tool_round": False,
        "prepared_items_sent": False,
        "instruction": (
            "工具轮不会向客户发送文字或卡片，上轮如有回复草稿也未发送。"
            "请继续调用必要工具，或在最终回复中重新完整承接并回答客户当前问题；"
            "要发送已准备卡片，必须在 final_response.messages 中引用对应 call_id。"
        ),
    }


def test_turn_payload_foregrounds_short_reply_and_previous_assistant_question():
    payload = json.loads(
        build_turn_payload(
            customer_message="是的",
            customer_workspace={
                "recent_turns": [
                    {"role": "customer", "content": "你们什么服务？"},
                    {
                        "role": "assistant",
                        "content": "每个品种都有对应的单品养护教程。",
                    },
                    {
                        "role": "assistant",
                        "content": "实际养护中还有师傅一对一指导。",
                    },
                    {
                        "role": "assistant",
                        "content": "您觉得这种有人带着走的方式，是不是更踏实？",
                    },
                    {"role": "customer", "content": "是的"},
                ]
            },
            event_context={},
            tool_results=[],
        )
    )

    assert payload["turn_focus"]["current_customer_message"] == "是的"
    assert payload["turn_focus"]["previous_assistant_messages"] == [
        "每个品种都有对应的单品养护教程。",
        "实际养护中还有师傅一对一指导。",
        "您觉得这种有人带着走的方式，是不是更踏实？",
    ]
    assert "推进下一步" in payload["turn_focus"]["instruction"]
    assert payload["full_conversation"]["turns"] == [
        {"role": "customer", "content": "你们什么服务？"},
        {"role": "assistant", "content": "每个品种都有对应的单品养护教程。"},
        {"role": "assistant", "content": "实际养护中还有师傅一对一指导。"},
        {
            "role": "assistant",
            "content": "您觉得这种有人带着走的方式，是不是更踏实？",
        },
    ]
    assert payload["full_conversation"]["truncated_oldest"] is False
    assert "recent_turns" not in payload["customer_workspace"]


def test_turn_payload_keeps_latest_full_conversation_within_char_budget():
    payload = json.loads(
        build_turn_payload(
            customer_message="是的",
            customer_workspace={
                "recent_turns": [
                    {"role": "customer", "content": "早" * 12_000},
                    {"role": "assistant", "content": "近" * 12_000},
                    {"role": "customer", "content": "是的"},
                ]
            },
            event_context={},
            tool_results=[],
        )
    )

    conversation = payload["full_conversation"]
    assert conversation["content_chars"] == 20_000
    assert conversation["truncated_oldest"] is True
    assert conversation["turns"][0]["content"].startswith("…")
    assert conversation["turns"][-1] == {
        "role": "assistant",
        "content": "近" * 12_000,
    }
    assert all(turn["content"] != "是的" for turn in conversation["turns"])
    assert "跨会话稳定事实" in payload["context_priority"]
    assert "客户更新的明确表述" in payload["context_priority"]


@pytest.mark.asyncio
async def test_runtime_repairs_mixed_tool_and_reply_without_losing_customer_answer(
    monkeypatch,
):
    service = {
        "item_id": "service-1",
        "title": "陪伴养兰服务",
        "price_cent": 9900,
        "stock": 20,
        "status": "online",
        "h5_url": "https://shop.example.com/companion",
        "knowledge": {"product_name": "陪伴养兰服务"},
    }
    calls = []
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "service-card-draft",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "两三天浇一次确实偏勤，这段草稿不能被当成已经发送。",
                        }
                    ],
                    "need_human": False,
                },
                purchase_signal="direct",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-card",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                purchase_signal="direct",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": (
                                "两三天浇一次确实偏勤，春兰肉质根长期处在湿植料里很容易闷烂。"
                                "先等植料明显干一些再浇透，同时保持通风；如果您总拿不准节奏，"
                                "陪伴养兰服务可以按实际情况持续帮您调整。"
                            ),
                        },
                        {"type": "prepared", "ref": "service-card"},
                    ],
                    "need_human": False,
                },
                purchase_signal="direct",
            ),
        ]
    )

    async def fake_generate(messages, **kwargs):
        del kwargs
        calls.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return next(responses)

    monkeypatch.setattr(agent_tools, "get_catalog_product", lambda item_id: service)
    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("我不太懂，都是之前商家配的，浇水两三天一次吧"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert len(calls) == 3
    assert "工具调用与最终回复必须二选一" in calls[1][-1]["content"]
    tool_payload = json.loads(calls[2][-1]["content"])
    assert tool_payload["delivery_state"][
        "customer_visible_messages_sent_in_tool_round"
    ] is False
    assert tool_payload["delivery_state"]["prepared_items_sent"] is False
    assert "两三天浇一次确实偏勤" in reply.answer
    assert "陪伴养兰服务" in reply.answer
    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    assert json.loads(reply.outbound_messages[1].content)["title"] == "陪伴养兰服务"
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "invalid_schema",
        "tool_calls_executed",
        "accepted",
    ]


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
                ],
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
async def test_video_access_request_requires_verified_douyin_purchase(monkeypatch):
    from app.domains.handoff.services import handoff_notification_service

    async def fail_if_notified(**kwargs):
        del kwargs
        raise AssertionError("unverified customer must not notify humans")

    monkeypatch.setattr(
        handoff_notification_service,
        "enqueue_handoff_notification",
        fail_if_notified,
    )
    context = AgentExecutionContext(
        message=_message("我在抖音买过，视频看不了"),
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {"customer_tags": []}},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="video-access-1",
        name="video_access.request",
        arguments={},
        context=context,
    )

    assert result.status == "forbidden"
    assert result.data["reason"] == "verified_douyin_purchase_required"
    assert context.handoff is None


@pytest.mark.asyncio
async def test_video_access_request_notifies_without_handoff(monkeypatch):
    from app.domains.handoff.services import handoff_notification_service

    notified = {}

    async def fake_notify(**kwargs):
        notified.update(kwargs)
        return {"queued": 1, "outbound_message_ids": [9], "feishu_sent": False}

    monkeypatch.setattr(
        handoff_notification_service,
        "enqueue_handoff_notification",
        fake_notify,
    )
    message = _message("[订单截图已核验] 视频看不了")
    message.metadata = {
        "wc_id": "customer-wxid",
        "nickname": "兰友小王",
        "alias_name": "orchid_wang",
    }
    context = AgentExecutionContext(
        message=message,
        user_state=UserState(user_id="customer-1"),
        workspace={"profile": {"customer_tags": ["抖音已购"]}},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="video-access-2",
        name="video_access.request",
        arguments={},
        context=context,
    )

    assert result.status == "notified"
    assert result.data["permission_status"] == "pending_human_review"
    assert result.data["ai_continues"] is True
    assert result.data["customer_reply"] == "已经联系同事处理了"
    assert context.handoff is None
    assert notified["customer_wc_id"] == "customer-wxid"
    assert notified["notification_kind"] == "reminder"
    assert notified["handoff_reason"] == "video_access_review"


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
async def test_agent_shapes_service_without_repeating_or_sending_card_before_interest(
    monkeypatch,
):
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
                        "call_id": "service-facts",
                        "name": "brand.service_facts",
                        "arguments": {},
                    }
                ],
                judgment="客户刚确认一直自己摸索，应该承接指导缺口并具体塑造服务",
                purpose="说明萧岚苑与卖完即止模式的差异",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-search",
                        "name": "product.search",
                        "arguments": {"query": "陪伴养兰", "limit": 2},
                    }
                ],
                judgment="服务事实已核实，可以查询真实服务商品供后续试成交使用",
                purpose="内部核实服务商品，不向客户发卡",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-card-too-early",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                judgment="客户缺少指导，准备直接发送服务卡片",
                purpose="尝试成交",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": (
                                "那问题可能就出在这儿了。建兰虽然喜湿，但根部更需要透气。"
                                "两三天浇一次，如果盆土还没干透就接着浇，根部长期泡在湿气里，很容易闷坏。"
                            ),
                        },
                        {
                            "type": "text",
                            "content": (
                                "其实养兰最难的就是把握这个度，每个家庭的环境、通风都不一样，"
                                "固定的天数很难套用在所有情况上。我们这边会有师傅一对一教您判断浇水。"
                            ),
                        },
                    ],
                    "need_human": False,
                },
                judgment="重新解释病因并概述服务",
                purpose="说明服务价值",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": (
                                "原来您之前一直都是自己摸索，难怪浇水这个度不容易拿准。"
                                "市面上有些商家把兰花卖出去后，后面的养护只能靠兰友自己试；"
                                "我们更看重您买回去以后能不能真正养稳。"
                            ),
                        },
                        {
                            "type": "text",
                            "content": (
                                "我们每个品种都有对应的单品养护教程，收货、上盆、浇水、施肥都会讲清楚；"
                                "实际养的时候还有不懂，师傅会结合您家里的通风和盆土一对一帮您调整。"
                                "您要是觉得这种有人跟着教的方式更省心，我再给您具体讲讲。"
                            ),
                        },
                    ],
                    "need_human": False,
                },
                judgment="客户仅确认指导缺口，先完成差异和服务落地塑造，不发卡",
                purpose="让客户理解陪伴服务并自然试探意向",
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
                {"role": "customer", "content": "一般两三天一次，具体什么时候想起来就浇"},
                {
                    "role": "assistant",
                    "content": "那问题可能就出在这儿了。建兰虽然喜湿，但根部更需要透气。两三天浇一次，如果盆土还没干透就接着浇，根部长期泡在湿气里，很容易闷坏。",
                },
                {
                    "role": "assistant",
                    "content": "其实养兰最难的就是把握这个度，每个家庭的环境、通风都不一样，固定的天数很难套用在所有情况上。我们这边会有师傅一对一教您判断浇水。",
                },
            ],
        },
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "text"]
    assert "市面上有些商家" in reply.answer
    assert "单品养护教程" in reply.answer
    assert "师傅" in reply.answer and "一对一" in reply.answer
    assert "那问题可能就出在这儿了" not in reply.answer
    trace = reply.metadata["agent_runtime"]["tool_trace"]
    assert [item["tool"] for item in trace] == [
        "brand.service_facts",
        "product.search",
    ]
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "tool_calls_executed",
        "tool_calls_executed",
        "trajectory_rewrite_requested",
        "trajectory_rewrite_requested",
        "accepted",
    ]


@pytest.mark.asyncio
async def test_agent_sends_companion_service_card_after_customer_interest(
    monkeypatch,
):
    service = {
        "item_id": "service-1",
        "title": "陪伴养兰服务",
        "price_cent": 3990,
        "stock": 20,
        "status": "online",
        "h5_url": "https://shop.example.com/companion",
        "knowledge": {"product_name": "陪伴养兰服务"},
    }
    monkeypatch.setattr(
        agent_tools,
        "search_catalog_products",
        lambda query, limit=3: [service],
    )
    monkeypatch.setattr(agent_tools, "get_catalog_product", lambda item_id: service)
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "service-search",
                        "name": "product.search",
                        "arguments": {"query": "陪伴养兰", "limit": 2},
                    }
                ],
                judgment="客户明确想进一步了解陪伴服务，查询真实服务商品",
                purpose="核实服务商品后进入试成交",
                purchase_signal="interest",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-card",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                judgment="客户主动询问服务收费，已有清晰正向意向",
                purpose="发送真实服务入口试成交",
                purchase_signal="interest",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "可以，我把陪伴养兰服务的真实卡片放下面，您先看看具体内容。",
                        },
                        {"type": "prepared", "ref": "service-card"},
                    ],
                    "need_human": False,
                },
                judgment="客户已主动询问服务，卡片用于试成交",
                purpose="让客户查看服务并承接购买问题",
                purchase_signal="interest",
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("这种有人带着养的服务我想了解一下，怎么收费？"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    assert json.loads(reply.outbound_messages[1].content)["title"] == "陪伴养兰服务"
    assert reply.metadata["agent_runtime"]["purchase_signal"] == "interest"


@pytest.mark.asyncio
async def test_agent_advances_short_service_acceptance_to_trial_close(monkeypatch):
    service = {
        "item_id": "service-1",
        "title": "陪伴养兰服务",
        "price_cent": 3990,
        "stock": 20,
        "status": "online",
        "h5_url": "https://shop.example.com/companion",
        "knowledge": {"product_name": "陪伴养兰服务"},
    }
    monkeypatch.setattr(
        agent_tools,
        "search_catalog_products",
        lambda query, limit=3: [service],
    )
    monkeypatch.setattr(agent_tools, "get_catalog_product", lambda item_id: service)
    responses = iter(
        [
            _decision(
                tools=[
                    {
                        "call_id": "service-facts",
                        "name": "brand.service_facts",
                        "arguments": {},
                    }
                ],
                judgment="客户询问服务，先核实服务事实",
                purpose="准备说明服务",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": (
                                "主要是两块实在的支撑。一是每个品种都有对应的单品养护教程，"
                                "从收货、上盆到浇水施肥都会写清楚。"
                            ),
                        },
                        {
                            "type": "text",
                            "content": (
                                "二是真人指导，遇到拿不准的地方，"
                                "师傅会结合您家的环境给一对一建议。"
                            ),
                        },
                    ],
                    "need_human": False,
                },
                judgment="客户还在询问服务内容",
                purpose="再次介绍服务",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-search",
                        "name": "product.search",
                        "arguments": {"query": "陪伴养兰", "limit": 2},
                    }
                ],
                judgment="是的是对上一轮服务价值的认可，已形成兴趣",
                purpose="核实真实服务后试成交",
                purchase_signal="interest",
            ),
            _decision(
                tools=[
                    {
                        "call_id": "service-card",
                        "name": "product.send_card",
                        "arguments": {"product_ref": "product:service-1"},
                    }
                ],
                judgment="客户认可陪伴服务，可以进入试成交",
                purpose="发送真实服务卡片",
                purchase_signal="interest",
            ),
            _decision(
                final={
                    "messages": [
                        {
                            "type": "text",
                            "content": "那您可以先看看这个陪伴养兰服务，我把真实卡片放在下面。",
                        },
                        {"type": "prepared", "ref": "service-card"},
                    ],
                    "need_human": False,
                },
                judgment="客户已认可服务价值",
                purpose="试成交并承接后续问题",
                purchase_signal="interest",
            ),
        ]
    )

    async def fake_generate(*args, **kwargs):
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(agent_runtime, "generate_messages_json", fake_generate)
    reply = await agent_runtime.run_sales_agent(
        message=_message("是的"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {"role": "customer", "content": "你们什么服务？"},
                {
                    "role": "assistant",
                    "content": (
                        "主要是两块实在的支撑。一是每个品种都有对应的单品养护教程，"
                        "从收货、上盆到浇水施肥都会写清楚。"
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "二是真人指导，遇到拿不准的地方，"
                        "师傅会结合您家的环境给一对一建议。"
                    ),
                },
                {
                    "role": "assistant",
                    "content": "您觉得这种有人带着走的方式，是不是比自己摸索更踏实一些？",
                },
                {"role": "customer", "content": "是的"},
            ]
        },
    )

    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    assert "两块实在的支撑" not in reply.answer
    assert json.loads(reply.outbound_messages[1].content)["title"] == "陪伴养兰服务"
    assert reply.metadata["agent_runtime"]["purchase_signal"] == "interest"
    assert [item["tool"] for item in reply.metadata["agent_runtime"]["tool_trace"]] == [
        "brand.service_facts",
        "product.search",
        "product.send_card",
    ]
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == [
        "tool_calls_executed",
        "trajectory_rewrite_requested",
        "tool_calls_executed",
        "tool_calls_executed",
        "accepted",
    ]
    assert attempts[1]["trajectory_violations"] == [
        "repeats_recent_assistant_content",
        "missed_affirmed_service_trial_close",
    ]


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
                ],
                purchase_signal="direct",
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
                purchase_signal="direct",
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
                            "content": "按您想从第一盆好养的开始，我更建议适合新手和通风阳台的建兰。您如果想进一步看看具体品种，我再按这个条件给您介绍。",
                        },
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
    ]
    material_card = json.loads(reply.outbound_messages[1].content)
    assert material_card["title"] == "萧岚苑陪伴养兰资料"
    assert "您是刚入门" in reply.outbound_messages[0].content
    assert "更建议适合新手" in reply.outbound_messages[2].content


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
                ],
                purchase_signal="interest",
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


def test_video_access_notification_claim_requires_notified_tool_fact():
    decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "已经联系同事处理了，处理期间我继续帮您看养护问题。",
                    }
                ],
                "need_human": False,
            }
        )["data"]
    )
    unverified = AgentExecutionContext(
        message=_message("视频看不了"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
    )
    verified = AgentExecutionContext(
        message=_message("视频看不了"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
        tool_facts={
            "video-access-1": {
                "tool": "video_access.request",
                "status": "notified",
                "data": {"permission_status": "pending_human_review"},
            }
        },
    )

    assert agent_runtime._guard_violations(decision, unverified) == [
        "unverified_video_access_notification"
    ]
    assert agent_runtime._guard_violations(decision, verified) == []

    wrong_wording = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {"type": "text", "content": "已经提交权限处理了。"}
                ],
                "need_human": False,
            }
        )["data"]
    )
    assert agent_runtime._guard_violations(wrong_wording, verified) == [
        "incorrect_video_access_wording"
    ]

    unrelated_decision = AgentTurnDecision.model_validate(
        _decision(
            final={
                "messages": [
                    {"type": "text", "content": "我已经提醒同事核对优惠了。"}
                ],
                "need_human": True,
            }
        )["data"]
    )
    unrelated = AgentExecutionContext(
        message=_message("我想申请一下优惠"),
        user_state=UserState(user_id="customer-1"),
        workspace={},
        handoff={"status": "pending", "reason": "discount"},
    )
    assert agent_runtime._guard_violations(unrelated_decision, unrelated) == []


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
    assert "也可以主动查询并口头推荐合适商品" in prompt
    assert "先用一句客户收益说明回答后能得到什么" in prompt
    assert "新建一个 text 消息" in prompt
    assert "客户索要资料是兴趣信号，不是自动发送指令" in prompt
    assert "发送后必须有主动的下一步" in prompt
    assert "资料一旦已经发出，就从后续对话主线退到背景" in prompt
    assert "不要用“资料里面有、您先对照看看”代替回答" in prompt
    assert "先直接解决客户的新问题，再判断是否推品" in prompt
    assert "新客户开场优先了解当前盆数和主要品种" in prompt
    assert "L1-L6 客户等级、盆数和品种标签" in prompt
    assert "两个标签可以同时存在" in prompt
    assert "这只是一次专业答疑，不要擅自升级成诊断会诊" in prompt
    assert "同一个技术细节最多追问一轮" in prompt
    assert "未知但不阻塞" in prompt
    assert "不把“等待客户继续反馈该细节”写进 next_action" in prompt
    assert "推品经验和价值完整性目标，不是固定话术、固定顺序或逐项打卡" in prompt
    assert "自主决定表达顺序、消息轮次和是否需要同行对比" in prompt
    assert "顺序不能跳过" not in prompt
    assert "调用 brand.service_facts 核实" in prompt
    assert "自然询问客户是否想进一步了解这种陪伴方式" in prompt
    assert "市面上有些商家卖完后缺少持续养护承接" in prompt
    assert "几盆、十来盆、不多、几十盆" in prompt
    assert "否则不追问精确数字" in prompt
    assert "不需要精确到 3 盆还是 5 盆" in prompt
    assert "先问是否在抖音购买" in prompt
    assert "保持 AI 回复，不调用 human.handoff" in prompt
    assert "客户口径只说“已经联系同事处理了”" in prompt
    assert "不要求固定字段、精确盆数或完整画像" in prompt
    assert "推品方向采用明确的默认权重，但不做运行时固定路由" in prompt
    assert "都是养兰服务需求信号，不能据此推导客户想再买一盆兰花" in prompt
    assert "只要客户没有明确的兰花购买需求，推品方向就落在陪伴养兰服务" in prompt
    assert "仅仅提到自己现有的建兰等品种不算购买需求" in prompt
    assert "商品卡片是客户已有清晰正向意向后的试成交动作" in prompt
    assert "客户只是承认养不好、没人指导、一直自己摸索" in prompt
    assert "只有 purchase_signal 为 interest 或 direct 时才调用 product.send_card" in prompt
    assert "短回复必须结合紧邻的上一轮理解" in prompt
    assert "purchase_signal 应从 none 进入 interest" in prompt
    assert "是否形成意向由 Agent 理解完整语义，不用关键词硬匹配" in prompt

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
    assert "资料发出后就退到背景" in material.instructions
    assert "先像没有资料捷径一样直接解决" in material.instructions
    assert "不能用‘资料里有’代替回答" in material.instructions

    material_send = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "material.send"
    )
    assert "同一资料已经发过时" in material_send.instructions
    assert "先直接解决新问题并判断是否推品" in material_send.instructions
    assert "明确要求重发、找不到或上次未成功" in material_send.instructions

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
    assert "已有大致盆数和痛点通常足以" in service_fit.instructions
    assert "单品养护教程" in service_fit.instructions
    assert "师傅一对一实操指导" in service_fit.instructions
    assert "不是固定话术或固定轮次" in service_fit.instructions
    assert "以三个结果检查价值是否足够" in service_fit.instructions
    assert "表达顺序、轮次和取舍由 Agent 根据上下文决定" in service_fit.instructions
    assert "按固定价值顺序" not in service_fit.instructions
    assert "对应品种的单品养护教程" in service_fit.instructions
    assert "客户确认没人教、一直自己摸索后" in service_fit.instructions
    assert "只有客户给出清晰正向意向后才发送商品卡片试成交" in service_fit.instructions
    assert "几盆、十来盆、不多、几十盆" in service_fit.instructions
    assert "不再追问精确数字" in service_fit.instructions

    customer_tag = next(
        item for item in agent_tools.CAPABILITIES if item.name == "customer.tag"
    )
    assert "不要为了把标签精确到某个区间重新追问" in customer_tag.instructions

    video_access = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.video_access"
    )
    assert "先问客户是否在抖音购买" in video_access.instructions
    assert "不调用 human.handoff" in video_access.instructions
    assert "不停止 AI 回复" in video_access.instructions
    assert "只对客户说‘已经联系同事处理了’" in video_access.instructions

    video_access_tool = next(
        item for item in agent_tools.CAPABILITIES if item.name == "video_access.request"
    )
    assert "工作区已有‘抖音已购’标签" in video_access_tool.instructions
    assert "不创建人工接管" in video_access_tool.instructions
    assert "客户口径只说‘已经联系同事处理了’" in video_access_tool.instructions

    product_search = next(
        item for item in agent_tools.CAPABILITIES if item.name == "product.search"
    )
    assert "不要求固定字段、精确盆数或完整画像" in product_search.instructions
    assert "推品方向统一为陪伴养兰服务" in product_search.instructions
    assert "养护痛点不能被解释成需要换一盆好养兰花" in product_search.instructions
    assert "不是运行时关键词硬拦截" in product_search.instructions

    direction_weighting = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "experience.product_direction_weighting"
    )
    assert "推品统一落在陪伴养兰服务" in direction_weighting.instructions
    assert "都不能推导出客户想买新兰花" in direction_weighting.instructions
    assert "query‘陪伴养兰’" in direction_weighting.instructions
    assert "商品卡片属于试成交" in direction_weighting.instructions
    assert "客户明确表示想进一步了解" in direction_weighting.instructions
    assert "短肯定回复要与上一条 Agent 问话配对理解" in direction_weighting.instructions
    assert "应推进真实服务卡片试成交" in direction_weighting.instructions
    assert "不是固定话术或固定轮次" in direction_weighting.instructions

    product_send = next(
        item for item in agent_tools.CAPABILITIES if item.name == "product.send_card"
    )
    assert "商品卡片是试成交动作" in product_send.instructions
    assert "一直自己摸索或信息已经收集充分" in product_send.instructions
    assert "不等于可以发卡" in product_send.instructions

    service_facts = next(
        item
        for item in agent_tools.CAPABILITIES
        if item.name == "brand.service_facts"
    )
    assert "已核实买后服务事实" in service_facts.instructions
    assert "一对一指导" in service_facts.instructions


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
async def test_runtime_does_not_enforce_service_sales_experience_as_flow(
    monkeypatch,
):
    async def fake_generate(messages, **kwargs):
        del messages, kwargs
        return _decision(
            final={
                "messages": [
                    {
                        "type": "text",
                        "content": "自然风是加分项。您反复养不稳，下一步更重要的是按杭州阳台环境把种苗适配好，买后养护也有人承接。我直接按这个条件给您筛两款。",
                    }
                ],
                "need_human": False,
                "next_action": "查询适配杭州阳台的建兰",
            }
        )

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

    assert "我直接按这个条件给您筛两款" in reply.answer
    attempts = reply.metadata["agent_runtime"]["attempt_trace"]
    assert [attempt["outcome"] for attempt in attempts] == ["accepted"]
    assert attempts[0]["trajectory_violations"] == []


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


def test_short_affirmative_inherits_service_value_not_unrelated_question():
    accepted_service = AgentExecutionContext(
        message=_message("是的"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "您觉得这种陪伴服务是不是更踏实？",
                },
                {"role": "customer", "content": "是的"},
            ]
        },
    )
    confirmed_watering = AgentExecutionContext(
        message=_message("是的"),
        user_state=UserState(user_id="customer-1"),
        workspace={
            "recent_turns": [
                {"role": "assistant", "content": "您是不是两三天浇一次水？"},
                {"role": "customer", "content": "是的"},
            ]
        },
    )

    assert agent_runtime._affirmed_service_trial_close(accepted_service) is True
    assert agent_runtime._affirmed_service_trial_close(confirmed_watering) is False


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
