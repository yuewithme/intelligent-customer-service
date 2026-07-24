from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings


def _configure_db(monkeypatch, tmp_path, name: str) -> None:
    from app.services import conversation_service, message_risk_control_service

    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / f'{name}.db').as_posix()}"
    )
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    get_settings.cache_clear()
    conversation_service._sessionmakers.clear()
    conversation_service._initialized_urls.clear()
    message_risk_control_service._sessionmakers.clear()
    message_risk_control_service._initialized_urls.clear()


@pytest.mark.asyncio
async def test_self_callback_records_wechat_client_message(monkeypatch, tmp_path):
    from app.services import eyun_callback_service
    from app.domains.conversations.services.conversation_service import get_conversation_detail

    _configure_db(monkeypatch, tmp_path, "self-message")

    async def empty_contact(**kwargs):
        del kwargs
        return {}

    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", empty_contact)

    await eyun_callback_service.handle_eyun_callback(
        {
            "account": "sales",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_bot",
                "toUser": "wxid_customer",
                "content": "微信客户端发出的消息",
                "newMsgId": 1001,
                "self": True,
            },
        }
    )

    detail = await get_conversation_detail("wechat:wxid_customer:default")
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["sender_type"] == "human"
    assert detail["messages"][0]["message_id"] == "1001"
    assert detail["messages"][0]["delivery_status"] == "sent"


@pytest.mark.asyncio
async def test_self_callback_reconciles_queued_ai_message(monkeypatch, tmp_path):
    from app.services import eyun_callback_service
    from app.domains.conversations.services.conversation_service import (
        ensure_outbound_conversation_message,
        get_conversation_detail,
    )

    _configure_db(monkeypatch, tmp_path, "self-reconcile")

    async def empty_contact(**kwargs):
        del kwargs
        return {}

    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", empty_contact)
    await ensure_outbound_conversation_message(
        channel="wechat",
        user_id="wxid_customer",
        session_id="default",
        content="自动回复",
    )

    await eyun_callback_service.handle_eyun_callback(
        {
            "account": "sales",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_bot",
                "toUser": "wxid_customer",
                "content": "自动回复",
                "newMsgId": 1002,
                "self": True,
            },
        }
    )

    detail = await get_conversation_detail("wechat:wxid_customer:default")
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["sender_type"] == "ai"
    assert detail["messages"][0]["message_id"] == "1002"
    assert detail["messages"][0]["delivery_status"] == "sent"


@pytest.mark.asyncio
async def test_opening_text_and_image_are_recorded(monkeypatch, tmp_path):
    from app.services import message_risk_control_service as risk_control
    from app.domains.conversations.services.conversation_service import (
        AI_WAITING,
        get_conversation_detail,
        record_customer_message,
    )

    _configure_db(monkeypatch, tmp_path, "opening-sync")
    monkeypatch.setenv("EYUN_OPENING_TEXT", "欢迎开场")
    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "https://example.com/opening.jpg")
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "0")
    monkeypatch.setenv("EYUN_INBOUND_DEBOUNCE_SECONDS", "0")
    get_settings.cache_clear()
    now = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk_control, "utcnow", lambda: now)

    async def empty_contact(**kwargs):
        del kwargs
        return {}

    async def keep_queued(**kwargs):
        return kwargs

    monkeypatch.setattr(risk_control, "get_eyun_contact_snapshot", empty_contact)
    monkeypatch.setattr(risk_control, "enqueue_eyun_outbound", keep_queued)
    await record_customer_message(
        channel="wechat",
        user_id="wxid_customer",
        session_id="default",
        content="你好",
        message_id="inbound-1",
        status=AI_WAITING,
    )
    await risk_control.enqueue_eyun_inbound(
        {
            "account": "sales",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "你好",
                "newMsgId": "inbound-1",
            },
        }
    )

    assert await risk_control.process_due_eyun_inbound_batches(limit=1) == 1
    detail = await get_conversation_detail("wechat:wxid_customer:default")
    opening = next(
        message for message in detail["messages"] if message["content"] == "欢迎开场"
    )
    image = next(
        message
        for message in detail["messages"]
        if message["metadata"].get("media", {}).get("type") == "image"
    )
    assert opening["sender_type"] == "ai"
    assert image["metadata"]["media"] == {
        "type": "image",
        "url": "https://example.com/opening.jpg",
        "fallback": False,
    }
    assert opening["delivery_status"] == "queued"
    assert image["delivery_status"] == "queued"


@pytest.mark.asyncio
async def test_admin_reply_records_eyun_message_id(monkeypatch, tmp_path):
    from app.services import eyun_callback_service, message_risk_control_service
    from app.domains.conversations.services.conversation_service import (
        HANDOFF_PENDING,
        claim_conversation,
        get_conversation_detail,
        record_customer_message,
        reply_conversation,
    )

    _configure_db(monkeypatch, tmp_path, "admin-reply-sync")

    async def send_text(**kwargs):
        assert kwargs == {
            "w_id": "wid",
            "wc_id": "wxid_customer",
            "content": "人工回复",
        }
        return {
            "code": "1000",
            "data": {
                "newMsgId": 2001,
                "createTime": 1784102400,
            },
        }

    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", send_text)
    await record_customer_message(
        channel="wechat",
        user_id="wxid_customer",
        session_id="default",
        content="需要人工",
        message_id="customer-1",
        status=HANDOFF_PENDING,
        metadata={
            "provider": "eyun",
            "w_id": "wid",
            "from_user": "wxid_customer",
        },
    )
    conversation_id = "wechat:wxid_customer:default"
    await claim_conversation(conversation_id, "operator-1")
    fixed_now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(message_risk_control_service, "utcnow", lambda: fixed_now)
    await reply_conversation(conversation_id, "operator-1", "人工回复")

    detail = await get_conversation_detail(conversation_id)
    reply = detail["messages"][-1]
    assert reply["sender_type"] == "human"
    assert reply["delivery_status"] == "queued"
    assert reply["message_id"] is None

    monkeypatch.setattr(
        message_risk_control_service,
        "utcnow",
        lambda: fixed_now + timedelta(days=1),
    )
    assert await message_risk_control_service.process_due_eyun_outbound_messages() == 1

    detail = await get_conversation_detail(conversation_id)
    reply = next(
        message for message in detail["messages"] if message["sender_type"] == "human"
    )
    assert reply["message_id"] == "2001"
    assert reply["delivery_status"] == "sent"
    assert reply["delivery_status"] == "sent"
    assert reply["metadata"]["origin"] == "admin_workbench"
