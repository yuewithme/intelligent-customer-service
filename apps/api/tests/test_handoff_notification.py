import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import EyunContactModel
from app.domains.handoff.schemas.handoff_notification import HandoffNotificationSettingsUpdateRequest
from app.domains.conversations.services.conversation_service import (
    AI_WAITING,
    HANDOFF_PENDING,
    force_handoff,
    record_customer_message,
)
from app.domains.handoff.services.handoff_notification_service import (
    enqueue_handoff_notification,
    get_handoff_notification_settings,
    update_handoff_notification_settings,
)
from app.domains.handoff.services import handoff_notification_service
from app.domains.sales.services.unpurchased_sop_service import (
    _get_session as get_sop_session,
    sync_eyun_contacts,
)


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.infrastructure.database').as_posix()}")
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    )
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("EYUN_WID", "wid-1")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.delenv("FEISHU_HANDOFF_WEBHOOK_URL", raising=False)
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
        "app.integrations.eyun.services.message_risk_control_service.enqueue_wechat_outbound",
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
        handoff_reason="human_request",
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
        "转人工用户微信号：orchid_wang\n"
        "转人工原因：客户明确要求人工客服\n"
        "触发客户消息：请转人工"
    )


@pytest.mark.asyncio
async def test_unsupported_message_notification_contains_reason_and_message(
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
        "app.integrations.eyun.services.message_risk_control_service.enqueue_wechat_outbound",
        fake_enqueue,
    )

    await record_customer_message(
        channel="wechat",
        user_id="video-customer",
        session_id="default",
        content="[视频]",
        message_id="video-message-1",
        status=HANDOFF_PENDING,
        handoff_reason="unsupported_message_type",
        metadata={"nickname": "企业微信用户（51451）"},
    )

    assert len(queued) == 1
    assert "转人工原因：非文本消息需人工处理" in queued[0]["content"]
    assert "触发客户消息：[视频]" in queued[0]["content"]


@pytest.mark.asyncio
async def test_forced_handoff_sends_operator_reason_and_trigger_message(
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
        "app.integrations.eyun.services.message_risk_control_service.enqueue_wechat_outbound",
        fake_enqueue,
    )
    conversation = await record_customer_message(
        channel="wechat",
        user_id="customer-wxid",
        session_id="default",
        content="商品收到后有破损",
        message_id="msg-force-1",
        status=AI_WAITING,
        metadata={"nickname": "兰友小王", "alias_name": "orchid_wang"},
    )

    await force_handoff(
        conversation["conversation_id"],
        operator_id="operator-1",
        reason="请人工核实破损补发",
    )

    assert len(queued) == 1
    assert "转人工原因：请人工核实破损补发" in queued[0]["content"]
    assert "触发客户消息：商品收到后有破损" in queued[0]["content"]


@pytest.mark.asyncio
async def test_feishu_webhook_receives_same_handoff_notification(
    monkeypatch, tmp_path
):
    contact_id = await _create_contact(monkeypatch, tmp_path)
    update_handoff_notification_settings(
        HandoffNotificationSettingsUpdateRequest(
            recipient_contact_ids=[contact_id],
            message_text="请及时接待这位客户。",
        )
    )
    monkeypatch.setenv(
        "FEISHU_HANDOFF_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook",
    )
    get_settings.cache_clear()
    queued: list[dict] = []
    posted: list[dict] = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"StatusCode": 0, "StatusMessage": "success"}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 10

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            posted.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_wechat_outbound",
        fake_enqueue,
    )
    monkeypatch.setattr(handoff_notification_service.httpx, "AsyncClient", FakeClient)

    result = await enqueue_handoff_notification(
        customer_wc_id="customer-wxid",
        nickname="兰友小王",
        wechat_id="orchid_wang",
        handoff_reason="human_request",
        trigger_message="请转人工",
        source_reference="test-feishu",
    )

    assert result["feishu_sent"] is True
    assert len(queued) == 1
    assert posted == [
        {
            "url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook",
            "json": {
                "msg_type": "text",
                "content": {"text": queued[0]["content"]},
            },
        }
    ]
