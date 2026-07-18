from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services.vision_service import (
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
async def test_image_and_followup_text_share_sixty_second_window(monkeypatch):
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

    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(seconds=30))
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

    assert image_batch["due_at"] == now + timedelta(seconds=60)
    assert merged["due_at"] == now + timedelta(seconds=90)
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
    content, image_count, recognized_count, verified_orders = (
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
    assert "初步判断：疑似介壳虫" in content
    assert content.endswith("这个怎么处理？")


@pytest.mark.asyncio
async def test_verified_store_order_adds_purchase_tag_and_enters_chat(monkeypatch):
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
    assert profile["customer_tags"] == ["浙江省", "抖音已购"]
    assert "已验证店铺订单截图" in chat_requests[0].message
    assert "***************9059" in chat_requests[0].message
    assert chat_requests[0].metadata["verified_order_count"] == 1
    assert queued[0]["content"] == "订单已经看到了亲"


@pytest.mark.asyncio
async def test_unrecognized_image_uses_configured_fallback(monkeypatch):
    from app.services import message_risk_control_service as service

    queued = []
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

    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(
        service, "ensure_outbound_conversation_message", fake_conversation_message
    )
    monkeypatch.setattr(service, "enqueue_eyun_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "utcnow", lambda: now)

    assert await service.process_due_eyun_inbound_batches(limit=1) == 1
    assert queued[0]["content"] == "亲能否描述一下图片或重拍一下图片"
