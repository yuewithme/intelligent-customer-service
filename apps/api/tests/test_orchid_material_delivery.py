import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import (
    ConversationMessageModel,
    EyunInboundBatchModel,
)


@pytest.fixture(autouse=True)
def delivery_db(monkeypatch, tmp_path):
    from app.services import message_risk_control_service

    db_path = tmp_path / "orchid-material-delivery.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    yield
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()


async def _async_value(value):
    return value


def test_outbound_messages_keep_fixed_link_card_payload():
    from app.integrations.eyun.services.message_risk_control_service import _outbound_messages
    from app.domains.catalog.services.orchid_material_service import orchid_material_chat_result

    result = orchid_material_chat_result("发资料")

    assert result is not None
    messages = _outbound_messages(result)
    assert [message["type"] for message in messages] == [
        "link_card",
        "text",
        "image",
    ]
    assert json.loads(messages[0]["content"])["url"].endswith(
        "noteAlias=0Ja8r3cajo"
    )
    assert messages[1]["content"].startswith("直播间展示的是图文版资料")
    assert messages[1]["content"].count("\n") == 1
    assert messages[2]["content"] == (
        "http://150.158.52.233/static/orchid-material/"
        "companion-service-video-links.png"
    )


@pytest.mark.asyncio
async def test_chat_core_discovers_need_on_first_request_and_delivers_on_second(
    monkeypatch,
):
    from app.domains.conversations.schemas.chat import ChatRequest
    from app.domains.conversations.services.chat_orchestrator import handle_chat
    from app.domains.conversations.services import state_service
    from app.integrations.ai.services import llm_service

    async def fail_generate(*args, **kwargs):
        del args, kwargs
        pytest.fail("固定资料链路不应调用 LLM")

    monkeypatch.setattr(llm_service, "generate_answer", fail_generate)
    user_id = "material_core_two_step"
    state_service._state_store.pop(user_id, None)

    first = await handle_chat(
        ChatRequest(
            channel="api",
            user_id=user_id,
            session_id="material-core-session",
            message="老师，怎样领取养兰资料？",
            kb_id="kb_default",
        )
    )
    second = await handle_chat(
        ChatRequest(
            channel="api",
            user_id=user_id,
            session_id="material-core-session",
            message="那把养兰资料发我吧",
            kb_id="kb_default",
        )
    )

    assert first["intent"]["primary_goal"] == "request_material"
    assert first["intent"]["slots"]["material_request_phase"] == "discovery"
    assert first["route"] == "orchid_material_discovery"
    assert first["outbound_messages"]
    assert {item["type"] for item in first["outbound_messages"]} == {"text"}
    assert "黄叶" in first["answer"]
    assert "烂根" in first["answer"]

    assert second["intent"]["primary_goal"] == "request_material"
    assert second["intent"]["slots"]["material_request_phase"] == "delivery"
    assert second["route"] == "orchid_material_delivery"
    second_message_types = [item["type"] for item in second["outbound_messages"]]
    assert second_message_types[0] == "link_card"
    assert "text" in second_message_types
    assert second_message_types[-1] == "image"


@pytest.mark.parametrize(
    "content",
    [
        "为什么你们的视频打不开？",
        "资料里面的课程看不了",
        "发的视频点不开",
        "视频无法播放是什么原因",
    ],
)
def test_material_video_issue_matcher(content):
    from app.domains.catalog.services.orchid_material_service import (
        orchid_material_video_issue_context,
    )

    result = orchid_material_video_issue_context(content)

    assert result is not None
    assert "视频或课程无法打开" in result["business_snapshot"]
    assert "抖音购买" in result["business_snapshot"]
    assert "订单截图" in result["business_snapshot"]
    assert (
        result["tool_state"]["material_video_access_action"]
        == "confirm_douyin_purchase"
    )
    assert "answer" not in result


def test_material_video_issue_requires_video_or_course_problem():
    from app.domains.catalog.services.orchid_material_service import (
        orchid_material_video_issue_context,
    )

    assert orchid_material_video_issue_context("资料在哪里") is None
    assert orchid_material_video_issue_context("视频很好看") is None


def test_material_video_issue_context_requires_a_sent_material():
    from app.domains.conversations.services.conversation_service import make_conversation_id
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        _has_sent_orchid_material,
    )

    now = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    with _get_session() as session:
        message = ConversationMessageModel(
            conversation_id=make_conversation_id("wechat", "context-user", None),
            delivery_status="queued",
            sender_type="ai",
            sender_id="ai",
            content="[链接卡片] 萧岚苑陪伴养兰资料",
            route="orchid_material_delivery",
            metadata_json="{}",
            created_at=now - timedelta(minutes=1),
        )
        session.add(message)
        session.commit()

        assert not _has_sent_orchid_material(
            session,
            user_id="context-user",
            session_id=None,
            before=now,
        )

        message.delivery_status = "sent"
        session.commit()
        assert _has_sent_orchid_material(
            session,
            user_id="context-user",
            session_id=None,
            before=now,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "message", "expected_route", "expected_text"),
    [
        (
            "confirm_douyin_purchase",
            "资料里的视频打不开",
            "orchid_material_purchase_check",
            "请问您是在抖音购买的吗？",
        ),
        (
            "request_order_screenshot",
            "是的，我在抖音买的",
            "orchid_material_order_screenshot_request",
            "请把抖音购买的订单截图发给我",
        ),
    ],
)
async def test_chat_core_runs_fixed_material_video_access_workflow(
    action,
    message,
    expected_route,
    expected_text,
):
    from app.domains.conversations.schemas.chat import ChatRequest
    from app.domains.conversations.services.chat_orchestrator import handle_chat
    from app.domains.conversations.services import state_service

    user_id = f"material-video-workflow-{action}"
    state_service._state_store.pop(user_id, None)

    result = await handle_chat(
        ChatRequest(
            channel="api",
            user_id=user_id,
            session_id="material-video-session",
            message=message,
            kb_id="kb_default",
            metadata={
                "business_snapshot": "资料视频权限核实流程。",
                "tool_state": {
                    "resource_access_issue": "video_unavailable",
                    "material_video_access_action": action,
                },
            },
        )
    )

    assert result["route"] == expected_route
    assert expected_text in result["answer"]
    assert result["intent"]["slots"]["material_video_access_action"] == action
    assert result["outbound_messages"]
    assert {item["type"] for item in result["outbound_messages"]} == {"text"}


@pytest.mark.asyncio
async def test_chat_core_infers_material_video_workflow_from_conversation_history():
    from app.domains.conversations.schemas.chat import ChatRequest
    from app.domains.conversations.services.chat_orchestrator import handle_chat
    from app.domains.conversations.services.conversation_service import make_conversation_id
    from app.domains.conversations.services import state_service
    from app.integrations.eyun.services.message_risk_control_service import _get_session

    user_id = "material-video-core-history"
    session_id = "material-video-history-session"
    state_service._state_store.pop(user_id, None)
    with _get_session() as session:
        session.add(
            ConversationMessageModel(
                conversation_id=make_conversation_id("wechat", user_id, session_id),
                delivery_status="sent",
                sender_type="ai",
                sender_id="ai",
                content="[链接卡片] 萧岚苑陪伴养兰资料",
                route="orchid_material_delivery",
                metadata_json="{}",
                created_at=datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

    first = await handle_chat(
        ChatRequest(
            channel="wechat",
            user_id=user_id,
            session_id=session_id,
            message="视频看不了",
            kb_id="kb_default",
        )
    )
    second = await handle_chat(
        ChatRequest(
            channel="wechat",
            user_id=user_id,
            session_id=session_id,
            message="是的，我在抖音买的",
            kb_id="kb_default",
        )
    )

    assert first["route"] == "orchid_material_purchase_check"
    assert first["need_human"] is False
    assert first["next_action"] != "human_handoff"
    assert "是在抖音购买的吗" in first["answer"]
    assert "订单截图" not in first["answer"]

    assert second["route"] == "orchid_material_order_screenshot_request"
    assert second["need_human"] is False
    assert second["next_action"] != "human_handoff"
    assert "订单截图" in second["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "麻烦发一下养兰资料",
        "养护教程发一下",
        "直播间说有师傅教，有视频资料免费领取",
    ],
)
async def test_process_batch_sends_opening_then_core_material_reply(
    monkeypatch, content
):
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        _process_inbound_batch,
    )
    from app.domains.catalog.services.orchid_material_service import (
        orchid_material_chat_result,
    )

    monkeypatch.setenv("INTENT_OBSERVATION_ENABLED", "true")
    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "")
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "0")
    get_settings.cache_clear()
    now = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    queued = []
    handled = []

    async def fake_handle_chat(request):
        handled.append(request.message)
        return orchid_material_chat_result(request.message)

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), **kwargs}

    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_outbound_spacing_seconds",
        lambda: 3,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.handle_chat", fake_handle_chat
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: True,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )

    with _get_session() as session:
        batch = EyunInboundBatchModel(
            batch_key="wid:material-user",
            w_id="wid",
            wc_id="bot",
            target_wc_id="material-user",
            from_user="material-user",
            from_group=None,
            account="acct",
            message_type="60001",
            content=content,
            message_count=1,
            status="processing",
            due_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.commit()
        batch_id = batch.id

    await _process_inbound_batch(batch_id)

    assert handled == [content]
    message_types = [row.get("message_type", "text") for row in queued]
    assert message_types[:2] == ["text", "text"]
    assert "link_card" in message_types[2:]
    assert message_types[-1] == "image"
    assert queued[0]["content"].startswith("兰友您好！欢迎来到萧岚苑")
    assert "家里目前养了多少盆兰花" in queued[1]["content"]
    card = next(row for row in queued if row.get("message_type") == "link_card")
    assert json.loads(card["content"])["url"].endswith(
        "noteAlias=0Ja8r3cajo"
    )
    assert any(
        row["content"].startswith("直播间展示的是图文版资料")
        for row in queued[2:]
    )
    assert queued[-1]["content"].endswith("companion-service-video-links.png")


@pytest.mark.asyncio
async def test_new_friend_opening_precedes_later_material_request(monkeypatch):
    from app.domains.catalog.services.orchid_material_service import (
        orchid_material_chat_result,
    )
    from app.integrations.eyun.services import (
        message_risk_control_service as risk_control,
    )

    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "")
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "0")
    get_settings.cache_clear()
    clock = [datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)]
    queued = []
    handled = []

    async def fake_handle_chat(request):
        handled.append(request.message)
        return orchid_material_chat_result(request.message)

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), **kwargs}

    async def ignore_memories(*args, **kwargs):
        return None

    monkeypatch.setattr(risk_control, "utcnow", lambda: clock[0])
    monkeypatch.setattr(risk_control, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(risk_control, "random_outbound_spacing_seconds", lambda: 3)
    monkeypatch.setattr(risk_control, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(
        risk_control,
        "get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(risk_control, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(risk_control, "_record_opening_memories", ignore_memories)
    monkeypatch.setattr(risk_control, "_record_first_inbound_memories", ignore_memories)

    opening_batch = await risk_control.enqueue_eyun_inbound(
        {
            "messageType": "60999",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_new_customer",
                "toUser": "wxid_bot",
                "content": "你已添加了新兰友，以上是打招呼的消息。",
                "newMsgId": 7001,
            },
        }
    )
    await risk_control._process_inbound_batch(opening_batch["id"])

    clock[0] += timedelta(seconds=1)
    material_batch = await risk_control.enqueue_eyun_inbound(
        {
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_new_customer",
                "toUser": "wxid_bot",
                "content": "要资料",
                "newMsgId": 7002,
            },
        }
    )
    await risk_control._process_inbound_batch(material_batch["id"])

    assert handled == ["要资料"]
    message_types = [row.get("message_type", "text") for row in queued]
    assert message_types[:2] == ["text", "text"]
    assert "link_card" in message_types[2:]
    assert message_types[-1] == "image"
    assert queued[0]["content"] == get_settings().eyun_opening_text
    assert "具体养了哪些品种" in queued[1]["content"]
    assert queued[2]["source_batch_key"] == "wid:wxid_new_customer"


@pytest.mark.asyncio
async def test_video_issue_after_material_delivery_first_confirms_douyin_purchase(
    monkeypatch,
):
    from app.domains.conversations.services.conversation_service import make_conversation_id
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        _process_inbound_batch,
    )

    now = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    queued = []

    captured = {}

    async def fake_handle_chat(request):
        captured["request"] = request
        return {
            "answer": "资料里的视频打不开确实影响使用。请问您是在抖音购买的吗？",
            "route": "orchid_material_purchase_check",
        }

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), **kwargs}

    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.handle_chat",
        fake_handle_chat,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: False,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )

    with _get_session() as session:
        session.add(
            ConversationMessageModel(
                conversation_id=make_conversation_id("wechat", "video-user", None),
                delivery_status="sent",
                sender_type="ai",
                sender_id="ai",
                content="[链接卡片] 萧岚苑陪伴养兰资料",
                route="orchid_material_delivery",
                metadata_json="{}",
                created_at=now - timedelta(minutes=1),
            )
        )
        batch = EyunInboundBatchModel(
            batch_key="wid:video-user",
            w_id="wid",
            wc_id="bot",
            target_wc_id="video-user",
            from_user="video-user",
            from_group=None,
            account="acct",
            message_type="60001",
            content="为什么你们的视频打不开？",
            message_count=1,
            status="processing",
            due_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.commit()
        batch_id = batch.id

    await _process_inbound_batch(batch_id)

    assert len(queued) == 1
    assert queued[0]["content"].startswith("资料里的视频打不开确实影响使用")
    request = captured["request"]
    assert "视频或课程无法打开" in request.metadata["business_snapshot"]
    assert request.metadata["tool_state"]["viewing_entitlement"] == "unverified"
    assert (
        request.metadata["tool_state"]["material_video_access_action"]
        == "confirm_douyin_purchase"
    )


@pytest.mark.asyncio
async def test_douyin_confirmation_after_purchase_check_requests_order_screenshot(
    monkeypatch,
):
    from app.domains.conversations.services.conversation_service import make_conversation_id
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        _process_inbound_batch,
    )

    now = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    queued = []
    captured = {}

    async def fake_handle_chat(request):
        captured["request"] = request
        return {
            "answer": (
                "好的，请把抖音购买的订单截图发给我，"
                "我先帮您核实购买记录和资料观看权限。"
            ),
            "route": "orchid_material_order_screenshot_request",
        }

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), **kwargs}

    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
        lambda: now,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.handle_chat",
        fake_handle_chat,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: False,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )

    conversation_id = make_conversation_id("wechat", "video-user", None)
    with _get_session() as session:
        session.add_all(
            [
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    delivery_status="sent",
                    sender_type="ai",
                    sender_id="ai",
                    content="[链接卡片] 萧岚苑陪伴养兰资料",
                    route="orchid_material_delivery",
                    metadata_json="{}",
                    created_at=now - timedelta(minutes=2),
                ),
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    delivery_status="sent",
                    sender_type="ai",
                    sender_id="ai",
                    content="请问您是在抖音购买的吗？",
                    route="orchid_material_purchase_check",
                    metadata_json="{}",
                    created_at=now - timedelta(minutes=1),
                ),
            ]
        )
        batch = EyunInboundBatchModel(
            batch_key="wid:video-user",
            w_id="wid",
            wc_id="bot",
            target_wc_id="video-user",
            from_user="video-user",
            from_group=None,
            account="acct",
            message_type="60001",
            content="是的，我在抖音买的",
            message_count=1,
            status="processing",
            due_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.commit()
        batch_id = batch.id

    await _process_inbound_batch(batch_id)

    assert len(queued) == 1
    assert "订单截图" in queued[0]["content"]
    request = captured["request"]
    assert request.metadata["tool_state"]["purchase_channel"] == "douyin_confirmed"
    assert (
        request.metadata["tool_state"]["material_video_access_action"]
        == "request_order_screenshot"
    )
