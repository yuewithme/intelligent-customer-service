import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db.models import (
    ConversationMessageModel,
    ConversationModel,
    EyunInboundBatchModel,
    EyunInboundMessageModel,
    EyunOutboundMessageModel,
    EyunSendRateModel,
)


@pytest.fixture(autouse=True)
def risk_control_db(monkeypatch, tmp_path):
    from app.services import message_risk_control_service

    db_path = tmp_path / "risk_control.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    yield
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()


def test_risk_control_defaults():
    settings = get_settings()

    assert settings.eyun_inbound_debounce_seconds == 60
    assert settings.eyun_send_max_per_minute == 30
    assert settings.eyun_send_min_interval_seconds == 2.1
    assert settings.eyun_send_max_interval_seconds == 3.0
    assert settings.eyun_reply_jitter_min_seconds == 2
    assert settings.eyun_reply_jitter_max_seconds == 12


def test_risk_control_models_have_table_names():
    from app.db import models

    assert EyunInboundBatchModel.__tablename__ == "eyun_inbound_batches"
    assert EyunInboundMessageModel.__tablename__ == "eyun_inbound_messages"
    assert EyunOutboundMessageModel.__tablename__ == "eyun_outbound_messages"
    assert EyunSendRateModel.__tablename__ == "eyun_send_rates"
    image_prompt_rate_model = getattr(models, "EyunImagePromptRateModel", None)
    assert image_prompt_rate_model is not None
    assert image_prompt_rate_model.__tablename__ == "eyun_image_prompt_rates"


def test_build_eyun_batch_key_prefers_group():
    from app.services.message_risk_control_service import build_eyun_batch_key

    key = build_eyun_batch_key(
        w_id="wid", target_wc_id="group_a", from_user="user_a"
    )

    assert key == "wid:group_a"


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_merges_same_user_window(monkeypatch):
    from app.services.message_risk_control_service import enqueue_eyun_inbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    first = await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "user",
                "content": "first",
                "newMsgId": 1,
            },
        }
    )

    monkeypatch.setattr(
        "app.services.message_risk_control_service.utcnow",
        lambda: now + timedelta(seconds=30),
    )
    second = await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "user",
                "content": "second",
                "newMsgId": 2,
            },
        }
    )

    assert first["batch_key"] == second["batch_key"]
    assert second["content"] == "first\nsecond"
    assert second["message_count"] == 2
    assert second["due_at"] == now + timedelta(seconds=90)


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_dedupes_provider_message_id(monkeypatch):
    from app.services.message_risk_control_service import enqueue_eyun_inbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    payload = {
        "account": "acct",
        "messageType": "60001",
        "wcId": "bot",
        "data": {
            "wId": "wid",
            "fromUser": "user",
            "content": "first",
            "newMsgId": 1,
        },
    }

    first = await enqueue_eyun_inbound(payload)
    second = await enqueue_eyun_inbound(payload | {"data": payload["data"] | {"content": "retry"}})

    assert first["batch_key"] == second["batch_key"]
    assert second["content"] == "first"
    assert second["message_count"] == 1


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_reopens_processed_conversation(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        enqueue_eyun_inbound,
    )

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    first = await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "user",
                "content": "old",
                "newMsgId": 1,
            },
        }
    )
    with _get_session() as session:
        batch = session.get(EyunInboundBatchModel, first["id"])
        batch.status = "processed"
        session.commit()

    second = await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "user",
                "content": "new",
                "newMsgId": 2,
            },
        }
    )

    assert second["id"] == first["id"]
    assert second["status"] == "pending"
    assert second["content"] == "new"
    assert second["message_count"] == 1


@pytest.mark.asyncio
async def test_process_due_batch_calls_ai_once_for_merged_content(monkeypatch):
    from app.services.message_risk_control_service import (
        enqueue_eyun_inbound,
        process_due_eyun_inbound_batches,
    )

    calls = []
    outbound = []
    now = datetime(2026, 7, 3, 12, 2, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    async def fake_handle_chat(request):
        calls.append(request)
        return {"answer": "merged reply"}

    async def fake_enqueue_outbound(
        *,
        w_id,
        wc_id,
        content,
        source_batch_key,
        conversation_message_id=None,
        due_at=None,
    ):
        outbound.append(
            {
                "w_id": w_id,
                "wc_id": wc_id,
                "content": content,
                "source_batch_key": source_batch_key,
                "conversation_message_id": conversation_message_id,
                "due_at": due_at,
            }
        )

    async def fake_contact_snapshot(*, w_id, wc_id):
        assert w_id == "wid"
        assert wc_id == "user"
        return {
            "remark_name": "兰友张姐",
            "nickname": "张女士",
            "avatar_url": "https://example.com/avatar.jpg",
        }

    monkeypatch.setattr(
        "app.services.message_risk_control_service.handle_chat", fake_handle_chat
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.get_eyun_contact_snapshot",
        fake_contact_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.utcnow",
        lambda: now - timedelta(seconds=120),
    )
    await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {"wId": "wid", "fromUser": "user", "content": "first", "newMsgId": 1},
        }
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.utcnow",
        lambda: now - timedelta(seconds=90),
    )
    await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {"wId": "wid", "fromUser": "user", "content": "second", "newMsgId": 2},
        }
    )
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    attempted = await process_due_eyun_inbound_batches(limit=10)

    assert attempted == 1
    assert len(calls) == 1
    assert calls[0].message == "first\nsecond"
    assert calls[0].metadata["remark_name"] == "兰友张姐"
    assert calls[0].metadata["avatar_url"] == "https://example.com/avatar.jpg"
    assert outbound[0]["content"] == "merged reply"


@pytest.mark.asyncio
async def test_process_due_batch_ignores_obsolete_profile_id_and_uses_sender(monkeypatch, tmp_path):
    del tmp_path
    from app.services import message_risk_control_service as service

    monkeypatch.setenv("EYUN_INBOUND_DEBOUNCE_SECONDS", "0")
    get_settings.cache_clear()

    calls = []

    async def fake_handle_chat(request):
        calls.append(request)
        return {"answer": "ok"}

    async def fake_enqueue_outbound(**kwargs):
        return kwargs

    async def fake_contact_snapshot(**kwargs):
        return {}

    monkeypatch.setattr(service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(service, "enqueue_wechat_outbound", fake_enqueue_outbound)
    monkeypatch.setattr(
        service,
        "is_first_eyun_inbound_message",
        lambda session, batch_key: False,
    )
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", fake_contact_snapshot)
    payload = {
        "_profile_user_id": "profile_internal_1",
        "messageType": "60001",
        "wcId": "owner_1",
        "data": {
            "wId": "wid",
            "fromUser": "external_customer",
            "toUser": "owner_1",
            "content": "hello",
            "newMsgId": 9001,
        },
    }
    await service.enqueue_eyun_inbound(payload)
    await service.process_due_eyun_inbound_batches(limit=5)

    assert calls[0].user_id == "external_customer"
    assert calls[0].metadata["from_user"] == "external_customer"


@pytest.mark.asyncio
async def test_process_due_batch_skips_group_messages(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        enqueue_eyun_inbound,
        process_due_eyun_inbound_batches,
    )

    now = datetime(2026, 7, 3, 12, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.message_risk_control_service.utcnow",
        lambda: now - timedelta(seconds=120),
    )
    batch = await enqueue_eyun_inbound(
        {
            "account": "acct",
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "user",
                "fromGroup": "group",
                "content": "group message",
                "newMsgId": 1,
            },
        }
    )

    async def fail_handle_chat(request):
        pytest.fail("group messages must not call AI")

    monkeypatch.setattr(
        "app.services.message_risk_control_service.handle_chat", fail_handle_chat
    )
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    attempted = await process_due_eyun_inbound_batches(limit=10)

    with _get_session() as session:
        stored = session.get(EyunInboundBatchModel, batch["id"])
        outbound_count = session.query(EyunOutboundMessageModel).count()

    assert attempted == 1
    assert stored.status == "skipped"
    assert outbound_count == 0


@pytest.mark.asyncio
async def test_enqueue_outbound_adds_random_due_at(monkeypatch):
    from app.services.message_risk_control_service import enqueue_wechat_outbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_reply_delay_seconds", lambda: 7
    )

    row = await enqueue_wechat_outbound(
        w_id="wid",
        wc_id="user",
        content="reply",
        source_batch_key="wid:user",
    )

    assert row["due_at"] == now + timedelta(seconds=7)
    assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_enqueue_outbound_always_creates_workbench_message(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        enqueue_wechat_outbound,
    )

    now = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    outbound = await enqueue_wechat_outbound(
        w_id="wid",
        wc_id="customer",
        content="SOP message",
        source_batch_key="unpurchased_sop:42:0:1",
        source_type="unpurchased_sop",
        source_id="42",
        sender_type="system",
        sender_id="unpurchased_sop",
        due_at=now,
    )

    assert outbound["conversation_message_id"] is not None
    with _get_session() as session:
        message = session.get(
            ConversationMessageModel, outbound["conversation_message_id"]
        )
        queue_row = session.get(EyunOutboundMessageModel, outbound["id"])

    assert queue_row.conversation_message_id == message.id
    assert message.conversation_id == "wechat:customer:default"
    assert message.content == "SOP message"
    assert message.sender_type == "system"
    assert message.delivery_status == "queued"
    assert json.loads(message.metadata_json)["source_type"] == "unpurchased_sop"


@pytest.mark.asyncio
async def test_process_batch_staggers_split_replies(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        _process_inbound_batch,
    )

    now = datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc)
    queued = []

    async def fake_handle_chat(request):
        del request
        return {
            "answer": "first\nsecond\nthird",
            "answer_segments": ["first", "second", "third"],
        }

    async def fake_enqueue_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 7,
    )
    spacings = iter((11, 15))
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_outbound_spacing_seconds",
        lambda: next(spacings),
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.handle_chat", fake_handle_chat
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: False,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )

    with _get_session() as session:
        batch = EyunInboundBatchModel(
            batch_key="wid:user",
            w_id="wid",
            wc_id="bot",
            target_wc_id="user",
            from_user="user",
            from_group=None,
            account="acct",
            message_type="60001",
            content="question",
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

    assert [row["due_at"] for row in queued] == [
        now + timedelta(seconds=7),
        now + timedelta(seconds=18),
        now + timedelta(seconds=33),
    ]


def test_random_outbound_spacing_uses_safe_thirty_per_minute_bounds(monkeypatch):
    from app.services.message_risk_control_service import random_outbound_spacing_seconds

    captured = {}

    def fake_uniform(minimum, maximum):
        captured["bounds"] = (minimum, maximum)
        return 2.5

    monkeypatch.setattr(
        "app.services.message_risk_control_service.random.uniform", fake_uniform
    )

    assert random_outbound_spacing_seconds() == 2.5
    assert captured["bounds"][0] > 2
    assert captured["bounds"][1] > captured["bounds"][0]


def test_outbound_text_messages_are_plain_short_messages():
    from app.services.message_risk_control_service import _outbound_messages

    messages = _outbound_messages(
        {
            "outbound_messages": [
                {
                    "type": "text",
                    "content": "1. **第一步**：剪掉烂根。然后放通风处晾干。",
                },
                {"type": "image", "content": "https://example.com/a.jpg"},
            ]
        }
    )

    assert messages == [
        {"type": "text", "content": "第一步：剪掉烂根。然后放通风处晾干。"},
        {"type": "image", "content": "https://example.com/a.jpg"},
    ]


def test_outbound_unsplit_text_also_removes_special_punctuation():
    from app.services.message_risk_control_service import _outbound_messages

    messages = _outbound_messages(
        {
            "outbound_messages": [
                {
                    "type": "text",
                    "content": "这是“芽黄素”（田黄玉）——您可以先看看图片。",
                    "split": False,
                }
            ]
        }
    )

    assert messages == [
        {"type": "text", "content": "这是芽黄素田黄玉您可以先看看图片。"}
    ]


def test_outbound_bare_link_is_removed_from_text_and_sent_as_card():
    from app.services.message_risk_control_service import _outbound_messages

    messages = _outbound_messages(
        {
            "answer": "这款比较适合您。购买链接：https://h5.youzan.com/goods/abc",
        }
    )

    assert messages[0] == {"type": "text", "content": "这款比较适合您"}
    assert messages[1]["type"] == "link_card"
    assert json.loads(messages[1]["content"]) == {
        "title": "查看详情",
        "url": "https://h5.youzan.com/goods/abc",
        "description": "点击卡片查看详情",
    }


def test_outbound_link_parser_preserves_chinese_text_after_url():
    from app.services.message_risk_control_service import _outbound_messages

    messages = _outbound_messages(
        {"answer": "详情：https://example.com/item?a=1&b=2，点击即可查看。"}
    )

    assert messages[0] == {"type": "text", "content": "点击即可查看"}
    assert json.loads(messages[1]["content"])["url"] == (
        "https://example.com/item?a=1&b=2"
    )


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_send_worker_records_eyun_create_time(monkeypatch):
    from app.services.message_risk_control_service import (
        _get_session,
        process_due_eyun_outbound_messages,
    )

    now = datetime(2026, 7, 13, 7, 0, 10, tzinfo=timezone.utc)
    provider_sent_at = datetime(2026, 7, 13, 7, 0, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    async def fake_send_eyun_text(**kwargs):
        del kwargs
        return {"code": "1000", "data": {"createTime": int(provider_sent_at.timestamp())}}

    monkeypatch.setattr(
        "app.services.eyun_callback_service.send_eyun_text", fake_send_eyun_text
    )

    with _get_session() as session:
        session.add(
            EyunInboundBatchModel(
                batch_key="wid:user",
                w_id="wid",
                wc_id="bot",
                target_wc_id="user",
                from_user="user",
                from_group=None,
                account="acct",
                message_type="60001",
                content="question",
                message_count=1,
                status="processed",
                due_at=now,
                created_at=now - timedelta(seconds=20),
                updated_at=now,
            )
        )
        session.add(
            ConversationModel(
                conversation_id="wechat:user:default",
                channel="wechat",
                user_id="user",
                tenant_id="tenant_default",
                status="ai_waiting",
                unread_count=0,
                created_at=now - timedelta(seconds=20),
                updated_at=now,
            )
        )
        session.add(
            ConversationMessageModel(
                conversation_id="wechat:user:default",
                sender_type="ai",
                sender_id="ai",
                content="reply",
                metadata_json="{}",
                created_at=now - timedelta(seconds=5),
            )
        )
        session.add(
            EyunOutboundMessageModel(
                w_id="wid",
                wc_id="user",
                content="reply",
                source_batch_key="wid:user",
                status="queued",
                due_at=now,
                attempts=0,
                created_at=now - timedelta(seconds=5),
                updated_at=now,
            )
        )
        session.commit()

    assert await process_due_eyun_outbound_messages(limit=5) == 1

    with _get_session() as session:
        message = session.query(ConversationMessageModel).one()
        assert message.created_at.replace(tzinfo=timezone.utc) == provider_sent_at


@pytest.mark.asyncio
async def test_send_worker_respects_account_min_interval(monkeypatch):
    from app.services import message_risk_control_service
    from app.services.message_risk_control_service import (
        _get_session,
        process_due_eyun_outbound_messages,
    )

    sent = []
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    async def fake_send_eyun_text(*, w_id, wc_id, content):
        sent.append((w_id, wc_id, content))

    monkeypatch.setattr(
        "app.services.eyun_callback_service.send_eyun_text", fake_send_eyun_text
    )

    with _get_session() as session:
        session.add(
            EyunOutboundMessageModel(
                w_id="wid",
                wc_id="user",
                content="reply",
                source_batch_key="wid:user",
                status="queued",
                due_at=now,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            EyunSendRateModel(
                w_id="wid",
                last_sent_at=now - timedelta(seconds=1),
                updated_at=now,
            )
        )
        session.commit()

    attempted = await process_due_eyun_outbound_messages(limit=10)

    assert attempted == 0
    assert sent == []


@pytest.mark.asyncio
async def test_worker_tick_processes_inbound_then_outbound(monkeypatch):
    from app.services.message_risk_control_service import eyun_worker_tick

    calls = []

    async def fake_inbound(limit=10):
        calls.append("inbound")
        return 1

    async def fake_outbound(limit=5):
        calls.append("outbound")
        return 1

    monkeypatch.setattr(
        "app.services.message_risk_control_service.process_due_eyun_inbound_batches",
        fake_inbound,
    )
    monkeypatch.setattr(
        "app.services.message_risk_control_service.process_due_eyun_outbound_messages",
        fake_outbound,
    )

    await eyun_worker_tick()

    assert calls == ["inbound", "outbound"]
