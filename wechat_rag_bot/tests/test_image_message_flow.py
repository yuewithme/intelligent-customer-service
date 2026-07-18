from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services.vision_service import VisionAnalysis


@pytest.fixture(autouse=True)
def image_flow_db(monkeypatch, tmp_path):
    from app.services import message_risk_control_service

    db_path = tmp_path / "image-flow.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("VISION_ENABLED", "false")
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    yield
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()


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
                "content": "这个怎么养？",
                "newMsgId": 2,
            },
        }
    )

    assert image_batch["due_at"] == now + timedelta(seconds=60)
    assert merged["due_at"] == now + timedelta(seconds=90)
    assert merged["content"] == "[图片]\n这个怎么养？"


@pytest.mark.asyncio
async def test_prepare_content_combines_image_facts_and_text(monkeypatch):
    from app.services import message_risk_control_service as service
    from app.services import vision_service

    async def fake_analyze(image_source):
        assert image_source == "https://cdn.example.com/image.jpg"
        return VisionAnalysis(
            image_type="product",
            summary="一盆兰花",
            visible_facts=["叶片发黄"],
            confidence=0.9,
        )

    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze)
    content, image_count, recognized_count = await service._prepare_inbound_content(
        "[图片]\n这个怎么养？",
        [
            {
                "messageType": "60002",
                "data": {"_image_url": "https://cdn.example.com/image.jpg"},
            },
            {"messageType": "60001", "data": {"content": "这个怎么养？"}},
        ],
    )

    assert image_count == 1
    assert recognized_count == 1
    assert "内容概述：一盆兰花" in content
    assert content.endswith("这个怎么养？")


@pytest.mark.asyncio
async def test_unrecognized_image_uses_configured_fallback(monkeypatch):
    from app.services import message_risk_control_service as service

    queued = []
    now = datetime(2026, 7, 18, 12, 5, tzinfo=timezone.utc)
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
