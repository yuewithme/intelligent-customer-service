import pytest

from app.config import get_settings
from app.db.models import EyunContactModel
from app.schemas.handoff_notification import HandoffNotificationSettingsUpdateRequest
from app.services.conversation_service import (
    AI_WAITING,
    HANDOFF_PENDING,
    record_customer_message,
)
from app.services.handoff_notification_service import (
    get_handoff_notification_settings,
    update_handoff_notification_settings,
)
from app.services.unpurchased_sop_service import (
    _get_session as get_sop_session,
    sync_eyun_contacts,
)


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    )
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("EYUN_WID", "wid-1")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()


async def _create_contact(monkeypatch, tmp_path) -> int:
    _settings(monkeypatch, tmp_path)
    await sync_eyun_contacts(friend_ids=["recipient-wxid"])
    with get_sop_session() as session:
        contact = session.query(EyunContactModel).filter_by(wc_id="recipient-wxid").one()
        contact.display_name = "值班客服"
        contact.remark_name = "小李"
        contact.wechat_id = "xiaoli_service"
        session.commit()
        return contact.id


@pytest.mark.asyncio
async def test_admin_can_save_handoff_notification_settings(monkeypatch, tmp_path):
    contact_id = await _create_contact(monkeypatch, tmp_path)
    saved = update_handoff_notification_settings(
        HandoffNotificationSettingsUpdateRequest(
            recipient_contact_ids=[contact_id],
            message_text="请及时接待这位客户。",
        )
    )

    assert saved["recipient_contact_ids"] == [contact_id]
    assert saved["recipients"][0]["remark_name"] == "小李"
    assert get_handoff_notification_settings()["message_text"] == "请及时接待这位客户。"


@pytest.mark.asyncio
async def test_handoff_transition_queues_notification_once_with_customer_identity(
    monkeypatch, tmp_path
):
    contact_id = await _create_contact(monkeypatch, tmp_path)
    update_handoff_notification_settings(
        HandoffNotificationSettingsUpdateRequest(
            recipient_contact_ids=[contact_id],
            message_text="请及时接待这位客户。",
        )
    )
    queued: list[dict] = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    monkeypatch.setattr(
        "app.services.message_risk_control_service.enqueue_wechat_outbound",
        fake_enqueue,
    )

    await record_customer_message(
        channel="wechat",
        user_id="customer-wxid",
        session_id="default",
        content="你好",
        message_id="msg-1",
        status=AI_WAITING,
        metadata={"nickname": "兰友小王", "alias_name": "orchid_wang"},
    )
    await record_customer_message(
        channel="wechat",
        user_id="customer-wxid",
        session_id="default",
        content="请转人工",
        message_id="msg-2",
        status=HANDOFF_PENDING,
        metadata={"nickname": "兰友小王", "alias_name": "orchid_wang"},
    )
    await record_customer_message(
        channel="wechat",
        user_id="customer-wxid",
        session_id="default",
        content="在吗",
        message_id="msg-3",
        status=HANDOFF_PENDING,
        metadata={"nickname": "兰友小王", "alias_name": "orchid_wang"},
    )

    assert len(queued) == 1
    assert queued[0]["w_id"] == "wid-1"
    assert queued[0]["wc_id"] == "recipient-wxid"
    assert queued[0]["content"] == (
        "请及时接待这位客户。\n\n"
        "转人工用户昵称：兰友小王\n"
        "转人工用户微信号：orchid_wang"
    )
