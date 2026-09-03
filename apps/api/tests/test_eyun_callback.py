import asyncio

from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def disable_background_contact_refresh(monkeypatch):
    from app.services import eyun_callback_service

    monkeypatch.setattr(
        eyun_callback_service,
        "schedule_eyun_contact_refresh",
        lambda **kwargs: None,
    )


def _reset_settings(monkeypatch, tmp_path):
    db_path = tmp_path / "chat_logs.db"
    profile_db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{profile_db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("STATE_PROVIDER", "memory")
    get_settings.cache_clear()


def test_offline_callback_schedules_login_alert(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    scheduled = []
    monkeypatch.setattr(
        eyun_callback_service,
        "schedule_eyun_offline_notification",
        scheduled.append,
    )

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "30000",
            "wcId": "wxid_bot",
            "data": {"wId": "wid", "reason": "session expired"},
        },
    )

    assert response.status_code == 200
    assert response.json()["code"] == "1000"
    assert scheduled[0]["messageType"] == "30000"


def test_private_callback_uses_external_user_id_and_persists_basic_info(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    queued = []

    profiles = []

    async def fake_ensure(user_id, **kwargs):
        profiles.append((user_id, kwargs))
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_enqueue(payload):
        queued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    async def fake_contact(**kwargs):
        return {
            "nickname": "若兰" if kwargs["wc_id"] == "wxid_bot" else "张姐"
        }

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "你好",
                "newMsgId": 101,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["user_id"] == "wxid_customer"
    assert recorded[0]["metadata"]["owner_wc_id"] == "wxid_bot"
    assert recorded[0]["metadata"]["contact_wc_id"] == "wxid_customer"
    assert recorded[0]["metadata"]["owner_display_name"] == "若兰"
    assert "_profile_user_id" not in queued[0]
    assert profiles == [
        (
            "wxid_customer",
            {
                "tenant_id": "wxid_bot",
                "channel": "wechat",
                "basic_info": {
                    "owner_wc_id": "wxid_bot",
                    "nickname": "张姐",
                },
            },
        )
    ]


def test_self_message_never_uses_recipient_as_owner(monkeypatch, tmp_path):
    from app.integrations.eyun.services.eyun_callback_service import (
        _eyun_owner_wc_id,
    )

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("EYUN_WC_ID", "")
    monkeypatch.setenv("EYUN_WID", "")
    get_settings.cache_clear()

    assert _eyun_owner_wc_id(
        {},
        {
            "wId": "wid_unknown",
            "fromUser": "wxid_owner",
            "toUser": "wxid_contact",
            "self": True,
        },
    ) == ""


@pytest.mark.parametrize(
    "content",
    [
        "你已添加了新兰友，以上是打招呼的消息。",
        (
            '<sysmsg type="NewXmlOpenIMFriReqAcceptedInWxWork">'
            "<NewXmlOpenIMFriReqAcceptedInWxWork>"
            "<username>wxid_customer</username>"
            "</NewXmlOpenIMFriReqAcceptedInWxWork></sysmsg>"
        ),
    ],
)
def test_new_friend_event_enters_opening_flow_instead_of_handoff(
    monkeypatch, tmp_path, content
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    queued = []
    tagged = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_enqueue(payload):
        queued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    async def fake_add_tag(user_id, tag, **kwargs):
        tagged.append((user_id, tag, kwargs))
        return {"user_id": user_id, "customer_tags": [tag]}

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    monkeypatch.setattr(eyun_callback_service, "add_system_customer_tag", fake_add_tag)
    monkeypatch.setattr(
        eyun_callback_service, "is_global_handoff_enabled", lambda: True
    )

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60999",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": content,
                "newMsgId": 105,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "ai_waiting"
    assert recorded[0]["route"] == "opening_trigger"
    assert recorded[0]["primary_intent"] == "opening_trigger"
    assert recorded[0]["handoff_reason"] is None
    assert queued[0]["_eyun_opening_trigger"] is True
    assert tagged == [
        (
            "wxid_customer",
            "服务中",
            {"reason": "eyun_new_friend_added", "trace_id": "105"},
        )
    ]


def test_global_handoff_routes_customer_message_without_ai_queue(
    monkeypatch, tmp_path
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    queued = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_enqueue(payload):
        queued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    monkeypatch.setattr(
        eyun_callback_service, "is_global_handoff_enabled", lambda: True
    )

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "你好",
                "newMsgId": 106,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "handoff_pending"
    assert recorded[0]["route"] == "global_handoff"
    assert recorded[0]["primary_intent"] == "global_handoff"
    assert recorded[0]["handoff_reason"] == "global_handoff"
    assert queued == []


def test_internal_workbench_title_callback_is_ignored(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail(**kwargs):
        pytest.fail(f"internal workbench title must be ignored: {kwargs}")

    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fail)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "销售工作台 - 销售 Agent",
                "newMsgId": 102,
                "self": False,
            },
        },
    )

    assert response.status_code == 200


def test_private_non_text_callback_uses_external_user_id(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_fetch_image_url(**kwargs):
        return "https://cdn.example.com/image.jpg"

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60002",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "<msg><img /></msg>",
                "newMsgId": 102,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["user_id"] == "wxid_customer"


def test_private_system_event_is_classified_and_ignored(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail(**kwargs):
        pytest.fail(f"system event must not enter the conversation: {kwargs}")

    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fail)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "messageType": "60999",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": '<sysmsg type="gamecenter"><gamecenter /></sysmsg>',
                "newMsgId": 107,
                "self": False,
            },
        },
    )

    assert response.status_code == 200


def test_self_callback_confirms_queued_outbound_without_duplicate(
    monkeypatch, tmp_path
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    confirmed = []

    def fake_confirm(message_ids, **kwargs):
        confirmed.append((message_ids, kwargs))
        return {"status": "confirmed"}

    async def fail_metadata(*args, **kwargs):
        pytest.fail(f"confirmed callback must not create another message: {args} {kwargs}")

    monkeypatch.setattr(
        eyun_callback_service, "confirm_eyun_outbound_delivery", fake_confirm
    )
    monkeypatch.setattr(
        eyun_callback_service, "_eyun_workbench_metadata", fail_metadata
    )
    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "messageType": "60002",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "toUser": "wxid_customer",
                "fromUser": "wxid_bot",
                "content": "<msg><img /></msg>",
                "newMsgId": "provider-accepted-1",
                "self": True,
            },
        },
    )
    assert response.status_code == 200
    assert confirmed == [
        (
            ["provider-accepted-1"],
            {
                "w_id": "wid",
                "wc_id": "wxid_customer",
                "message_type": "60002",
            },
        )
    ]


def test_private_emoji_is_recorded_as_reaction_without_handoff(
    monkeypatch, tmp_path
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    queued = []
    recovered = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_enqueue(payload):
        queued.append(payload)

    async def fake_recover(**kwargs):
        recovered.append(kwargs)
        return True

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    monkeypatch.setattr(
        eyun_callback_service, "recover_automatic_handoff", fake_recover
    )

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "messageType": "60006",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": '<msg><emoji md5="abc" len="123" /></msg>',
                "newMsgId": 108,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "ai_active"
    assert recorded[0]["route"] == "inbound_emoji"
    assert recorded[0]["primary_intent"] == "emoji"
    assert recorded[0]["handoff_reason"] is None
    assert recorded[0]["metadata"]["inbound_classification"]["disposition"] == "reaction"
    assert queued == []
    assert recovered == [
        {
            "channel": "wechat",
            "user_id": "wxid_customer",
            "session_id": "wxid_bot",
        }
    ]


def test_private_app_card_is_normalized_for_agent(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    queued = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_enqueue(payload):
        queued.append(payload)

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "messageType": "60999",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": (
                    "<msg><appmsg><title>七仙女</title><type>109</type>"
                    "<url>https://example.com/product</url></appmsg></msg>"
                ),
                "newMsgId": 109,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "ai_waiting"
    assert recorded[0]["route"] == "inbound_app_card"
    assert recorded[0]["handoff_reason"] is None
    assert queued[0]["messageType"] == "60001"
    assert queued[0]["_eyun_original_message_type"] == "60999"
    assert queued[0]["data"]["content"] == (
        "[应用卡片] 标题：七仙女；链接：https://example.com/product"
    )


def test_private_image_callback_enters_recognition_batch(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    enqueued = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_fetch_image_url(**kwargs):
        return "https://cdn.example.com/image.jpg"

    async def fake_enqueue_inbound(payload):
        enqueued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue_inbound)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60002",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "<msg><img /></msg>",
                "newMsgId": 102,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "ai_waiting"
    assert recorded[0]["route"] == "inbound_image"
    assert recorded[0]["handoff_reason"] is None
    assert len(enqueued) == 1
    assert enqueued[0]["messageType"] == "60002"
    assert enqueued[0]["data"]["_image_url"] == "https://cdn.example.com/image.jpg"


def test_image_like_private_other_callback_is_normalized_for_recognition(
    monkeypatch, tmp_path
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    recorded = []
    enqueued = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    async def fake_contact(**kwargs):
        return {}

    async def fake_fetch_image_url(**kwargs):
        return "https://cdn.example.com/nonstandard-image.jpg"

    async def fake_enqueue_inbound(payload):
        enqueued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(
        eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url
    )
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue_inbound)

    response = TestClient(app).post(
        "/wechat/callback",
        json={
            "account": "sales_a",
            "messageType": "60999",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "<msg><img md5=\"abc\" /></msg>",
                "newMsgId": 104,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert recorded[0]["status"] == "ai_waiting"
    assert recorded[0]["route"] == "inbound_image"
    assert recorded[0]["metadata"]["original_message_type"] == "60999"
    assert recorded[0]["metadata"]["image_detection"] == "data.content.img"
    assert len(enqueued) == 1
    assert enqueued[0]["messageType"] == "60002"
    assert enqueued[0]["_eyun_original_message_type"] == "60999"
    assert (
        enqueued[0]["data"]["_image_url"]
        == "https://cdn.example.com/nonstandard-image.jpg"
    )


def test_private_image_callback_never_sends_immediate_fallback(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    enqueued = []

    async def fake_ensure(user_id, **kwargs):
        return {"user_id": user_id}

    async def fake_record(**kwargs):
        return None

    async def fake_contact(**kwargs):
        return {}

    async def fake_fetch_image_url(**kwargs):
        return "https://cdn.example.com/image.jpg"

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid:wxid_customer"}

    monkeypatch.setattr(
        eyun_callback_service, "ensure_user_profile", fake_ensure, raising=False
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fake_record)
    monkeypatch.setattr(eyun_callback_service, "get_eyun_contact_snapshot", fake_contact)
    monkeypatch.setattr(eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)

    client = TestClient(app)
    for message_id in (102, 103):
        response = client.post(
            "/wechat/callback",
            json={
                "account": "sales_a",
                "messageType": "60002",
                "wcId": "wxid_bot",
                "data": {
                    "wId": "wid",
                    "fromUser": "wxid_customer",
                    "toUser": "wxid_bot",
                    "content": "<msg><img /></msg>",
                    "newMsgId": message_id,
                    "self": False,
                },
            },
        )
        assert response.status_code == 200

    assert [item["data"]["newMsgId"] for item in enqueued] == [102, 103]


def test_eyun_callback_accepts_json_payload(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_enqueue(payload):
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    client = TestClient(app)

    response = client.post(
        "/eyun/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_test",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_test",
                "content": "hello",
                "newMsgId": 123,
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}


def test_wechat_callback_accepts_eyun_test_payload():
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "00000",
            "wcId": "wxid_test",
            "data": {},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}


def test_wechat_callback_enqueues_eyun_text_payload(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    enqueued = []

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "hello",
                "msgId": 456,
                "newMsgId": 123,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}
    assert len(enqueued) == 1
    assert enqueued[0]["data"]["content"] == "hello"


def test_wechat_callback_records_private_messages_under_same_wcid(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    enqueued = []

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)

    async def fake_fetch_image_url(**kwargs):
        assert kwargs["w_id"] == "wid_test"
        assert kwargs["msg_id"] == "456"
        assert kwargs["image_type"] == 1
        return "https://cdn.example.com/original.jpg"

    monkeypatch.setattr(eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url)
    client = TestClient(app)

    text_response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "hello",
                "msgId": 455,
                "newMsgId": 122,
                "self": False,
            },
        },
    )
    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60002",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "<?xml version=\"1.0\"?><msg><img md5=\"abc\" /></msg>",
                "img": "/9j/4AAQSkZJRgABAQAASABIAAD/",
                "msgId": 456,
                "newMsgId": 123,
                "self": False,
            },
        },
    )

    assert text_response.status_code == 200
    assert response.status_code == 200
    assert len(enqueued) == 2
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "wechat:wxid_customer:wxid_bot"
    assert item["tenant_id"] == "wxid_bot"
    assert item["owner_wc_id"] == "wxid_bot"
    assert item["status"] == "ai_waiting"
    assert item["last_message"] == "[图片]"

    detail = client.get(f"/api/v1/admin/conversations/{item['conversation_id']}")
    messages = detail.json()["data"]["messages"]
    assert [message["content"] for message in messages] == [
        "hello",
        "[图片]",
    ]
    assert [message["sender_type"] for message in messages] == ["customer", "customer"]
    assert messages[1]["metadata"]["message_type"] == "60002"
    assert messages[1]["metadata"]["image_thumb_base64"]
    assert messages[1]["metadata"]["media"] == {
        "type": "image",
        "url": "https://cdn.example.com/original.jpg",
        "thumb_base64": "/9j/4AAQSkZJRgABAQAASABIAAD/",
        "fallback": False,
    }


def test_wechat_callback_ignores_group_messages(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail_enqueue(payload):
        raise AssertionError("group messages should not enter the AI queue")

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)

    async def fail_contact_snapshot(**kwargs):
        raise AssertionError("group messages should not query contact details")

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fail_contact_snapshot
    )
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "80001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "fromGroup": "12345@chatroom",
                "toUser": "wxid_bot",
                "content": "group hello",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 0


@pytest.mark.parametrize(
    "data",
    [
        {
            "fromUser": "wxid_sender",
            "fromGroup": "12345@chatroom",
            "toUser": "wxid_bot",
        },
        {
            "fromUser": "12345@chatroom",
            "fromGroup": "",
            "toUser": "wxid_bot",
        },
    ],
)
def test_mislabeled_private_callback_is_blocked_before_processing(
    monkeypatch, tmp_path, data
):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail_processing(*args, **kwargs):
        raise AssertionError("group messages must be blocked before processing")

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fail_processing
    )
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", fail_processing)
    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_processing)

    payload = {
        "account": "test_account",
        "messageType": "60001",
        "wcId": "wxid_bot",
        "data": {
            "wId": "wid_test",
            "content": "mislabeled group hello",
            "msgId": 791,
            "newMsgId": 792,
            "self": False,
            **data,
        },
    }

    assert eyun_callback_service.should_process_eyun_payload(payload) is False
    assert asyncio.run(eyun_callback_service.handle_eyun_callback(payload)) == {
        "code": "1000",
        "message": "success",
        "data": None,
    }


def test_eyun_non_image_messages_expose_media_metadata(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    client = TestClient(app)
    payloads = [
        (
            "60003",
            '<msg><videomsg cdnvideourl="https://cdn.example.com/video.mp4" /></msg>',
            {},
            "video",
            "https://cdn.example.com/video.mp4",
        ),
        (
            "60004",
            "<msg><voicemsg /></msg>",
            {"bufId": "voice-buffer", "length": 3227, "voiceLength": 1800},
            "audio",
            None,
        ),
        (
            "60006",
            "<msg><emoji /></msg>",
            {
                "url": "https://cdn.example.com/emoji.gif",
                "md5": "emoji-md5",
                "length": 1234,
            },
            "emoji",
            "https://cdn.example.com/emoji.gif",
        ),
        (
            "60007",
            "<msg><appmsg><title>Example</title>"
            "<url>https://example.com/article</url></appmsg></msg>",
            {},
            "link",
            "https://example.com/article",
        ),
    ]

    for index, (message_type, content, extra, expected_type, expected_url) in enumerate(
        payloads
    ):
        response = client.post(
            "/wechat/callback",
            json={
                "account": "test_account",
                "messageType": message_type,
                "wcId": "wxid_bot",
                "data": {
                    "wId": "wid_test",
                    "fromUser": f"wxid_customer_{index}",
                    "toUser": "wxid_bot",
                    "content": content,
                    "msgId": 500 + index,
                    "newMsgId": 600 + index,
                    "self": False,
                    **extra,
                },
            },
        )
        assert response.status_code == 200

        conversations = client.get("/api/v1/admin/conversations").json()["data"]
        expected_label = {
            "60003": "[视频]",
            "60004": "[语音]",
            "60006": "[表情]",
            "60007": "[链接]",
        }[message_type]
        conversation = next(
            item for item in conversations["items"] if item["last_message"] == expected_label
        )
        detail = client.get(
            f"/api/v1/admin/conversations/{conversation['conversation_id']}"
        ).json()["data"]
        media = detail["messages"][0]["metadata"]["media"]
        assert media["type"] == expected_type
        if expected_type in {"video", "audio"}:
            assert media["job_key"]
        if expected_type == "video":
            assert media["original_url"] == expected_url
            assert "url" not in media
            assert media["fallback"] is True
            assert media["resolve_status"] == "pending"
        elif expected_type == "audio":
            assert "url" not in media
            assert media["buf_id"] == "voice-buffer"
            assert media["length"] == "3227"
            assert media["voice_length"] == "1800"
            assert media["resolve_status"] == "pending"
        else:
            assert media["url"] == expected_url


def test_official_voice_callback_is_downloaded_once_and_becomes_playable(
    monkeypatch, tmp_path
):
    from app.services import eyun_callback_service
    from app.integrations.ai.services import speech_recognition_service
    from app.integrations.ai.services.speech_recognition_service import SpeechTranscript
    from app.integrations.eyun.services.eyun_inbound_media_service import (
        process_due_eyun_media_jobs,
    )
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    calls = []
    queued = []

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fake_download_voice(**kwargs):
        calls.append(kwargs)
        return "/static/media/voice.wav"

    async def fake_transcribe(path):
        assert path.name == "voice.wav"
        return SpeechTranscript(
            text="我的兰花根腐烂了怎么办",
            language="zh",
            emotion="neutral",
            duration_seconds=2,
        )

    async def fake_enqueue(payload):
        queued.append(payload)
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(
        eyun_callback_service, "download_eyun_voice", fake_download_voice
    )
    monkeypatch.setattr(
        speech_recognition_service, "transcribe_audio_file", fake_transcribe
    )
    monkeypatch.setattr(
        message_risk_control_service, "enqueue_eyun_inbound", fake_enqueue
    )
    client = TestClient(app)
    payload = {
        "account": "test_account",
        "messageType": "60004",
        "wcId": "wxid_bot",
        "data": {
            "wId": "wid_test",
            "fromUser": "wxid_customer",
            "toUser": "wxid_bot",
            "content": (
                '<msg><voicemsg bufid="289139440622895483" length="3227" '
                'voicelength="1800" /></msg>'
            ),
            "msgId": 1114311129,
            "newMsgId": 7208315917323315345,
            "self": False,
        },
    }

    assert client.post("/wechat/callback", json=payload).status_code == 200
    assert client.post("/wechat/callback", json=payload).status_code == 200
    assert asyncio.run(process_due_eyun_media_jobs()) == 1

    assert calls == [
        {
            "w_id": "wid_test",
            "msg_id": "1114311129",
            "from_user": "wxid_customer",
            "buf_id": "289139440622895483",
            "length": 3227,
        }
    ]
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_customer:wxid_bot"
    ).json()["data"]
    media = detail["messages"][0]["metadata"]["media"]
    assert media["url"] == "/static/media/voice.wav"
    assert media["resolve_status"] == "succeeded"
    assert media["recognition"] == {
        "status": "succeeded",
        "text": "我的兰花根腐烂了怎么办",
        "language": "zh",
        "emotion": "neutral",
        "duration_seconds": 2,
    }
    assert queued[0]["messageType"] == "60001"
    assert queued[0]["_eyun_original_message_type"] == "60004"
    assert queued[0]["data"]["content"] == (
        "[客户语音转写] 我的兰花根腐烂了怎么办"
    )
