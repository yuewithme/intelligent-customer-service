import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.infrastructure.database.models import EyunContactModel
from app.domains.handoff.schemas.handoff_notification import HandoffNotificationSettingsUpdateRequest
from app.domains.conversations.services.conversation_service import (
    AI_WAITING,
    HANDOFF_PENDING,
    claim_conversation,
    force_handoff,
    record_customer_message,
    resolve_conversation,
)
from app.domains.handoff.services.handoff_notification_service import (
    enqueue_handoff_notification,
    get_handoff_notification_settings,
    is_sop_node_handoff_enabled,
    update_handoff_notification_settings,
)
from app.domains.handoff.services import handoff_notification_service
from app.domains.orchestration.services.workbench_service import (
    get_capability_workbench,
    update_capability_workbench_sop_node,
)
from app.domains.sales.services.contact_sync_service import (
    _session as get_contact_session,
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
    with get_contact_session() as session:
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
            global_handoff_enabled=True,
            recipient_contact_ids=[contact_id],
            message_text="请及时接待这位客户。",
            sop_node_handoff={"first_order.value_building": True},
        )
    )

    assert saved["recipient_contact_ids"] == [contact_id]
    assert saved["global_handoff_enabled"] is True
    assert saved["sop_node_handoff"]["first_order.value_building"] is True
    assert saved["sop_node_handoff"]["service.need_discovery"] is False
    assert [group["sop_scope"] for group in saved["sop_node_groups"]] == [
        "first_order",
        "service",
        "seeding",
    ]
    assert saved["recipients"][0]["remark_name"] == "小李"
    assert get_handoff_notification_settings()["message_text"] == "请及时接待这位客户。"
    assert is_sop_node_handoff_enabled(
        "first_order", "first_order.value_building"
    )
    assert not is_sop_node_handoff_enabled(
        "service", "first_order.value_building"
    )


def test_workbench_exposes_three_sops_and_parallel_branches(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)

    workbench = get_capability_workbench()

    packages = workbench["experience_packages"]
    assert [package["package_id"] for package in packages] == [
        "orchid.first_order",
        "orchid.service",
        "orchid.seeding",
    ]
    steps = [step for package in packages for step in package["steps"]]
    business_steps = [step for step in steps if step.get("node_id")]
    assert len(business_steps) == 14
    assert all(step["handoff_enabled"] is False for step in business_steps)
    assert [package["enabled"] for package in packages] == [True, True, False]
    for package, reply_node in zip(packages[1:], ("need_discovery", "product_interest")):
        edges = package["transitions"]
        assert {edge["to_step"] for edge in edges if edge["from_step"] == "entry"} == {"daily_touch", reply_node}
    assert {edge["to_step"] for edge in packages[1]["transitions"] if edge["from_step"] == "daily_touch"} == {"story", "knowledge", "topic"}
    assert workbench["read_only"] is False


def test_sop_switches_persist_independently(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    service = handoff_notification_service
    service.update_sop_enabled("service", False)
    service.update_sop_enabled("seeding", True)
    service._sessionmakers.clear()
    assert service.get_sop_settings() == {"first_order": True, "service": False, "seeding": True}
    packages = get_capability_workbench()["experience_packages"]
    assert [package["enabled"] for package in packages] == [True, False, True]


def test_workbench_updates_shared_sop_handoff_setting(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)

    updated = update_capability_workbench_sop_node(
        node_id="service.repurchase_discovery",
        handoff_enabled=True,
    )

    assert updated["handoff_enabled"] is True
    assert is_sop_node_handoff_enabled(
        "service", "service.repurchase_discovery"
    )
    workbench = get_capability_workbench()
    service_package = next(
        package
        for package in workbench["experience_packages"]
        if package["package_id"] == "orchid.service"
    )
    repurchase = next(
        step
        for step in service_package["steps"]
        if step["node_id"] == "service.repurchase_discovery"
    )
    assert repurchase["handoff_enabled"] is True


def test_existing_handoff_settings_table_adds_global_switch_column(
    monkeypatch, tmp_path
):
    _settings(monkeypatch, tmp_path)
    database_url = get_settings().database_url
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE handoff_notification_settings ("
                "id INTEGER PRIMARY KEY, "
                "recipient_contact_ids_json TEXT NOT NULL, "
                "message_text TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        )

    settings = get_handoff_notification_settings()

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("handoff_notification_settings")
    }
    assert "global_handoff_enabled" in columns
    assert "sop_node_handoff_json" in columns
    assert "sop_enabled_json" in columns
    assert settings["global_handoff_enabled"] is False
    assert not any(settings["sop_node_handoff"].values())


@pytest.mark.asyncio
async def test_global_handoff_reopens_resolved_conversation(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    conversation = await record_customer_message(
        channel="wechat",
        user_id="returning-customer",
        session_id="default",
        content="上次问题已处理",
        message_id="resolved-1",
        status=AI_WAITING,
    )
    conversation_id = conversation["conversation_id"]
    await force_handoff(conversation_id, operator_id="system", reason="人工跟进")
    await claim_conversation(conversation_id, operator_id="operator-1")
    await resolve_conversation(conversation_id, operator_id="operator-1", reason=None)

    reopened = await record_customer_message(
        channel="wechat",
        user_id="returning-customer",
        session_id="default",
        content="我又有一个问题",
        message_id="resolved-2",
        status=HANDOFF_PENDING,
        route="global_handoff",
        primary_intent="global_handoff",
        handoff_reason="global_handoff",
    )

    assert reopened["status"] == HANDOFF_PENDING
    assert reopened["handoff_reason"] == "global_handoff"


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


@pytest.mark.asyncio
async def test_video_access_reminder_notifies_operator_without_handoff_wording(
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

    result = await enqueue_handoff_notification(
        customer_wc_id="customer-wxid",
        nickname="兰友小王",
        wechat_id="orchid_wang",
        handoff_reason="video_access_review",
        trigger_message="[订单截图已核验] 视频看不了",
        source_reference="test-video-access",
        notification_kind="reminder",
    )

    assert result["queued"] == 1
    assert "转人工" not in queued[0]["content"]
    assert "待处理用户昵称：兰友小王" in queued[0]["content"]
    assert "处理事项：核实抖音购买截图并处理视频课程权限" in queued[0]["content"]
    assert "触发客户消息：[订单截图已核验] 视频看不了" in queued[0]["content"]
