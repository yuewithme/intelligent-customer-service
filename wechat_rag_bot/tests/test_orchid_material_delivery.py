import json
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db.models import EyunInboundBatchModel


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
    from app.services.message_risk_control_service import _outbound_messages
    from app.services.orchid_material_service import orchid_material_chat_result

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
    assert messages[2]["content"].endswith("companion-service-video-links.png")


@pytest.mark.asyncio
async def test_process_batch_sends_fixed_material_without_calling_ai(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        _process_inbound_batch,
    )

    now = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
    queued = []

    async def fail_handle_chat(request):
        del request
        pytest.fail("固定资料关键词不应调用 AI")

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), **kwargs}

    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_outbound_spacing_seconds",
        lambda: 3,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.handle_chat", fail_handle_chat
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: True,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.enqueue_eyun_outbound",
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
            content="麻烦发一下养兰资料",
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

    assert [row.get("message_type", "text") for row in queued] == [
        "link_card",
        "text",
        "image",
    ]
    assert json.loads(queued[0]["content"])["url"].endswith(
        "noteAlias=0Ja8r3cajo"
    )
    assert queued[1]["content"].startswith("直播间展示的是图文版资料")
    assert queued[2]["content"].endswith("companion-service-video-links.png")
