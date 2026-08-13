from datetime import datetime, timezone

import pytest


def test_first_inbound_message_is_the_only_opening_trigger(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.infrastructure.database.models import EyunInboundMessageModel
    from app.services import message_risk_control_service as risk_control

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
    from app.services import message_risk_control_service as risk_control

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
async def test_new_friend_opening_uses_service_copy_and_dedicated_queue(monkeypatch):
    from app.services import message_risk_control_service as risk_control

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
