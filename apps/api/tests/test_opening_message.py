from datetime import datetime, timedelta, timezone

import pytest


def test_first_inbound_message_is_the_only_opening_trigger(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.infrastructure.database.models import EyunInboundMessageModel
    from app.integrations.eyun.services import message_risk_control_service as risk_control

    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'opening.db').as_posix()}")
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()

    with risk_control._get_session() as session:
        session.add(
            EyunInboundMessageModel(
                provider_message_id="first",
                batch_key="wid:customer",
                content="第一条",
                payload_json="{}",
                created_at=risk_control.utcnow(),
            )
        )
        session.commit()
        assert risk_control.is_first_eyun_inbound_message(session, "wid:customer") is True

        session.add(
            EyunInboundMessageModel(
                provider_message_id="second",
                batch_key="wid:customer",
                content="第二条",
                payload_json="{}",
                created_at=risk_control.utcnow(),
            )
        )
        session.commit()
        assert risk_control.is_first_eyun_inbound_message(session, "wid:customer") is False

    get_settings.cache_clear()
    risk_control._sessionmakers.clear()


@pytest.mark.asyncio
async def test_first_inbound_message_skips_debounce_delay(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.integrations.eyun.services import message_risk_control_service as risk_control

    now = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'opening-delay.db').as_posix()}")
    monkeypatch.setattr(risk_control, "utcnow", lambda: now)
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()

    batch = await risk_control.enqueue_eyun_inbound(
        {
            "messageType": "60001",
            "wcId": "sales",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "想学养兰",
                "newMsgId": 1,
            },
        }
    )

    assert batch["due_at"] == now
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()


@pytest.mark.asyncio
async def test_new_friend_opening_uses_service_copy_and_dedicated_queue(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.integrations.eyun.services import message_risk_control_service as risk_control

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    get_settings.cache_clear()
    queued = []
    recorded = []
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    due_slots = [now]

    async def fake_record(*args, **kwargs):
        del args, kwargs

    async def fake_ensure(**kwargs):
        recorded.append(kwargs)
        return {"id": len(queued) + 100, **kwargs}

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    async def fake_reserve(*, w_id, message_count):
        assert w_id == "wid"
        assert message_count == 1
        return due_slots

    monkeypatch.setattr(risk_control, "_record_opening_memories", fake_record)
    monkeypatch.setattr(risk_control, "ensure_outbound_conversation_message", fake_ensure)
    monkeypatch.setattr(risk_control, "enqueue_eyun_outbound", fake_enqueue)
    monkeypatch.setattr(risk_control, "_reserve_opening_delivery_slots", fake_reserve)

    await risk_control._send_opening_for_new_friend(
        {
            "batch_key": "wid:customer",
            "w_id": "wid",
            "target_wc_id": "customer",
            "from_user": "customer",
            "from_group": None,
            "created_at": now,
        }
    )

    assert [item["content"] for item in queued] == [risk_control.SERVICE_OPENING]
    assert [item["due_at"] for item in queued] == due_slots
    assert [item["route"] for item in recorded] == ["opening"]
    assert queued[0]["depends_on_outbound_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("service_enabled,disable_before_send", [(True, False), (False, False), (True, True)])
async def test_new_friend_service_opening_respects_service_switch_with_first_order_off(
    monkeypatch, tmp_path, service_enabled, disable_before_send
):
    from app.core.config import get_settings
    from app.domains.conversations.services.conversation_service import get_conversation_detail
    from app.domains.handoff.services.handoff_notification_service import update_sop_enabled
    from app.integrations.eyun.services import eyun_callback_service as callback
    from app.integrations.eyun.services import message_risk_control_service as risk_control

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    get_settings.cache_clear()
    update_sop_enabled("first_order", False)
    update_sop_enabled("service", service_enabled)
    sent = []

    async def contact(**kwargs):
        return {"nickname": "新兰友"}

    async def send(**kwargs):
        sent.append(kwargs)
        return {"code": "1000", "data": {"newMsgId": 2001}}

    monkeypatch.setattr(callback, "get_eyun_contact_snapshot", contact)
    monkeypatch.setattr(callback, "schedule_eyun_contact_refresh", lambda **kwargs: None)
    monkeypatch.setattr(callback, "send_eyun_text", send)
    monkeypatch.setattr(risk_control, "random_reply_delay_seconds", lambda: 0)
    payload = {
        "messageType": "60999",
        "wcId": "wxid_bot",
        "data": {
            "wId": "wid", "fromUser": "wxid_customer", "toUser": "wxid_bot",
            "content": "你已添加了新兰友，以上是打招呼的消息。",
            "newMsgId": 1001, "self": False,
        },
    }
    await callback.handle_eyun_callback(payload)
    assert await risk_control.process_due_eyun_inbound_batches() == 1
    if disable_before_send:
        update_sop_enabled("service", False)
    should_send = service_enabled and not disable_before_send
    assert await risk_control.process_due_eyun_outbound_messages() == int(should_send)
    detail = await get_conversation_detail("wechat:wxid_customer:wxid_bot")
    openings = [message for message in detail["messages"] if message["sender_type"] == "ai"]
    assert len(openings) == int(service_enabled)
    if service_enabled:
        assert openings[0]["content"] == risk_control.SERVICE_OPENING
        assert openings[0]["metadata"]["sop_scope"] == "service"
        assert openings[0]["delivery_status"] == ("accepted" if should_send else "cancelled")

    if should_send:
        await callback.handle_eyun_callback({**payload, "data": {**payload["data"], "newMsgId": 1002}})
        later = risk_control.utcnow() + timedelta(seconds=300)
        monkeypatch.setattr(risk_control, "utcnow", lambda: later)
        assert await risk_control.process_due_eyun_inbound_batches() == 1
        assert await risk_control.process_due_eyun_outbound_messages() == 0
    assert len(sent) == int(should_send)
