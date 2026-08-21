from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.integrations.ai.services.vision_service import (
    OrchidHealthAnalysis,
    OrderScreenshotAnalysis,
    VisionAnalysis,
)


@pytest.fixture(autouse=True)
def image_flow_db(monkeypatch, tmp_path):
    from app.services import message_risk_control_service, tag_catalog, user_profile_service

    chat_db_path = tmp_path / "image-flow-chat.db"
    profile_db_path = tmp_path / "image-flow-profile.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{chat_db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{profile_db_path.as_posix()}")
    monkeypatch.setenv("VISION_ENABLED", "false")
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    user_profile_service._sessionmakers.clear()
    tag_catalog.clear_cache()
    yield
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    user_profile_service._sessionmakers.clear()
    tag_catalog.clear_cache()


@pytest.mark.asyncio
async def test_image_and_followup_text_share_dynamic_window(monkeypatch):
    from app.services import message_risk_control_service as service

    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    image_batch = await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 1,
                "_image_url": "https://cdn.example.com/image.jpg",
            },
        }
    )

    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(seconds=3))
    merged = await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "这个怎么处理？",
                "newMsgId": 2,
            },
        }
    )

    assert image_batch["due_at"] == now + timedelta(seconds=5)
    assert merged["due_at"] == now + timedelta(seconds=8)
    assert merged["content"] == "[图片]\n这个怎么处理？"


@pytest.mark.asyncio
async def test_prepare_content_combines_orchid_health_result_and_text(monkeypatch):
    from app.services import message_risk_control_service as service
    from app.services import vision_service

    async def fake_analyze(image_source):
        assert image_source == "https://cdn.example.com/image.jpg"
        return VisionAnalysis(
            category="orchid_health",
            summary="叶片有白色虫体",
            orchid_health=OrchidHealthAnalysis(
                visible_symptoms=["叶片附着白色蜡质虫体"],
                primary_diagnosis="疑似介壳虫",
                alternative_diagnosis="粉蚧",
                isolation_needed=True,
                safe_actions=["先隔离并人工清除可见虫体"],
                clarifying_questions=["叶背和叶腋是否也有虫体？"],
            ),
            confidence=0.9,
        )

    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze)
    content, image_count, recognized_count, verified_orders, wrong_store_orders = (
        await service._prepare_inbound_content(
            "[图片]\n这个怎么处理？",
            [
                {
                    "messageType": "60002",
                    "data": {"_image_url": "https://cdn.example.com/image.jpg"},
                },
                {"messageType": "60001", "data": {"content": "这个怎么处理？"}},
            ],
        )
    )

    assert image_count == 1
    assert recognized_count == 1
    assert verified_orders == 0
    assert wrong_store_orders == 0
    assert "初步判断：疑似介壳虫" in content
    assert content.endswith("这个怎么处理？")


@pytest.mark.asyncio
async def test_verified_store_order_does_not_add_disabled_purchase_tag(monkeypatch):
    from app.services import message_risk_control_service as service
    from app.services import user_profile_service, vision_service

    queued = []
    chat_requests = []
    now = datetime(2026, 7, 18, 12, 5, tzinfo=timezone.utc)
    await user_profile_service.patch_user_profile(
        "customer", {"customer_tags": ["浙江省"]}
    )
    monkeypatch.setattr(service, "utcnow", lambda: now - timedelta(seconds=120))
    await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 1,
                "_image_url": "https://cdn.example.com/order.jpg",
            },
        }
    )

    async def fake_analyze(image_source):
        return VisionAnalysis(
            category="order",
            summary="订单截图",
            order=OrderScreenshotAnalysis(
                is_order_screenshot=True,
                store_name="萧兰苑",
                platform="淘宝/天猫",
                page_type="待收货",
                product="春兰裸苗",
                amount="¥32.80",
                order_number="6954542914016319059",
                status="已发货",
            ),
            confidence=0.95,
        )

    async def fake_contact(**kwargs):
        return {}

    async def fake_handle_chat(request):
        chat_requests.append(request)
        return {"answer": "订单已经看到了亲"}

    async def fake_conversation_message(**kwargs):
        return {"id": 7}

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze)
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(
        service, "ensure_outbound_conversation_message", fake_conversation_message
    )
    monkeypatch.setattr(service, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)

    assert await service.process_due_eyun_inbound_batches(limit=1) == 1

    profile = (await user_profile_service.get_profile_bundle("customer"))["profile"]
    assert profile["customer_tags"] == ["浙江省"]
    assert "支持店铺订单截图" in chat_requests[0].message
    assert "已验证抖音已付款" not in chat_requests[0].message
    assert "***************9059" in chat_requests[0].message
    assert chat_requests[0].metadata["verified_order_count"] == 0
    assert [item["content"] for item in queued] == ["订单已经看到了亲"]


@pytest.mark.asyncio
async def test_unrecognized_image_continues_as_soft_agent_context(monkeypatch):
    from app.services import message_risk_control_service as service

    queued = []
    chat_requests = []
    now = datetime(2026, 7, 18, 12, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now - timedelta(seconds=120))
    await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 1,
                "_image_url": "https://cdn.example.com/image.jpg",
            },
        }
    )

    async def fake_contact(**kwargs):
        return {}

    async def fake_conversation_message(**kwargs):
        return {"id": 7}

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    async def fake_handle_chat(request):
        chat_requests.append(request)
        return {
            "answer": "看着更像建兰，一年开两次说明这盆在您那里长得挺好。"
        }

    async def fail_if_handoff(*args, **kwargs):
        raise AssertionError("图片证据不足不应转人工")

    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(service, "force_handoff", fail_if_handoff)
    monkeypatch.setattr(
        service, "ensure_outbound_conversation_message", fake_conversation_message
    )
    monkeypatch.setattr(service, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)

    assert await service.process_due_eyun_inbound_batches(limit=1) == 1
    assert len(chat_requests) == 1
    assert chat_requests[0].message == "[客户发送了一张图片]"
    assert "不是转人工条件" in chat_requests[0].metadata["vision_description"]
    assert [item["content"] for item in queued] == [
        "看着更像建兰，一年开两次说明这盆在您那里长得挺好。"
    ]


@pytest.mark.asyncio
async def test_order_from_other_store_is_passed_to_agent(monkeypatch):
    from app.services import message_risk_control_service as service
    from app.services import vision_service

    queued = []
    now = datetime(2026, 7, 18, 12, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now - timedelta(seconds=120))
    await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 1,
                "_image_url": "https://cdn.example.com/other-order.jpg",
            },
        }
    )

    async def fake_analyze(image_source):
        raise vision_service.UnsupportedStoreOrderError("其他兰花店")

    async def fake_contact(**kwargs):
        return {}

    async def fake_conversation_message(**kwargs):
        return {"id": 7}

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    captured = {}

    async def fake_handle_chat(request):
        captured["request"] = request
        return {"answer": "这张截图不是萧岚苑订单，我先按您的问题继续帮您看。"}

    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze)
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(
        service, "ensure_outbound_conversation_message", fake_conversation_message
    )
    monkeypatch.setattr(service, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)

    assert await service.process_due_eyun_inbound_batches(limit=1) == 1
    assert captured["request"].metadata["attachment_error"] == "图片识别确认这不是萧岚苑的订单截图"
    assert queued[0]["content"] == "这张截图不是萧岚苑订单，我先按您的问题继续帮您看。"


@pytest.mark.asyncio
async def test_repeated_image_failure_still_continues_with_agent(monkeypatch):
    from app.services import message_risk_control_service as service
    from app.services import vision_service

    queued = []
    chat_requests = []
    recognition_attempts = 0
    now = datetime(2026, 7, 18, 12, 20, tzinfo=timezone.utc)

    async def fake_analyze(image_source):
        nonlocal recognition_attempts
        recognition_attempts += 1
        if recognition_attempts == 1:
            raise vision_service.UnsupportedStoreOrderError("其他兰花店")
        raise vision_service.VisionRecognitionError("unsupported image category")

    async def fake_contact(**kwargs):
        return {}

    async def fake_conversation_message(**kwargs):
        return {"id": 7}

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    async def fake_handle_chat(request):
        chat_requests.append(request)
        return {"answer": "我先结合您说的情况帮您看。"}

    async def fail_if_handoff(*args, **kwargs):
        raise AssertionError("重复图片识别失败也不应硬转人工")

    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze)
    monkeypatch.setattr(service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(service, "force_handoff", fail_if_handoff)
    monkeypatch.setattr(
        service, "ensure_outbound_conversation_message", fake_conversation_message
    )
    monkeypatch.setattr(service, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)

    monkeypatch.setattr(service, "utcnow", lambda: now - timedelta(seconds=120))
    await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 1,
                "_image_url": "https://cdn.example.com/first.jpg",
            },
        }
    )
    monkeypatch.setattr(service, "utcnow", lambda: now)
    assert await service.process_due_eyun_inbound_batches(limit=1) == 1
    assert len(queued) == 1
    assert "attachment_error" in chat_requests[0].metadata

    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(seconds=1))
    await service.enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60002",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "<msg><img /></msg>",
                "newMsgId": 2,
                "_image_url": "https://cdn.example.com/second.jpg",
            },
        }
    )
    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(seconds=62))
    assert await service.process_due_eyun_inbound_batches(limit=1) == 1

    assert recognition_attempts == 2
    assert len(queued) == 2
    assert len(chat_requests) == 2
    assert "不是转人工条件" in chat_requests[1].metadata["vision_description"]
