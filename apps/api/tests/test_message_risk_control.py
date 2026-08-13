import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import (
    ConversationMessageModel,
    ConversationModel,
    EyunInboundBatchModel,
    EyunInboundMessageModel,
    EyunOpeningControlModel,
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

    assert settings.eyun_inbound_debounce_seconds == 5
    assert settings.eyun_inbound_debounce_max_seconds == 15
    assert settings.eyun_send_max_per_minute == 30
    assert settings.eyun_send_min_interval_seconds == 2.1
    assert settings.eyun_send_max_interval_seconds == 3.0
    assert settings.eyun_opening_min_interval_seconds == 6.0
    assert settings.eyun_opening_max_interval_seconds == 10.0
    assert settings.eyun_opening_followup_min_seconds == 8.0
    assert settings.eyun_opening_followup_max_seconds == 15.0
    assert settings.eyun_opening_failure_pause_threshold == 2
    assert settings.eyun_opening_pause_minutes == 30
    assert settings.eyun_reply_jitter_min_seconds == 0
    assert settings.eyun_reply_jitter_max_seconds == 2


def test_risk_control_models_have_table_names():
    from app.infrastructure.database import models

    assert EyunInboundBatchModel.__tablename__ == "eyun_inbound_batches"
    assert EyunInboundMessageModel.__tablename__ == "eyun_inbound_messages"
    assert EyunOutboundMessageModel.__tablename__ == "eyun_outbound_messages"
    assert EyunSendRateModel.__tablename__ == "eyun_send_rates"
    assert EyunOpeningControlModel.__tablename__ == "eyun_opening_controls"
    image_prompt_rate_model = getattr(models, "EyunImagePromptRateModel", None)
    assert image_prompt_rate_model is not None
    assert image_prompt_rate_model.__tablename__ == "eyun_image_prompt_rates"


@pytest.mark.asyncio
async def test_eyun_handoff_updates_workbench_and_stays_silent(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    handoffs = []

    async def fake_force_handoff(conversation_id, operator_id, reason):
        handoffs.append((conversation_id, operator_id, reason))
        return {"status": "handoff_pending"}

    monkeypatch.setattr(service, "force_handoff", fake_force_handoff)
    result = await service._finalize_eyun_handoff(
        batch={
            "from_user": "wxid_customer",
            "target_wc_id": "wxid_customer",
            "from_group": None,
        },
        chat_result={
            "answer": "",
            "route": "human",
            "need_human": True,
            "handoff": {"reason": "order_query_unavailable"},
        },
    )

    assert handoffs == [
        (
            "wechat:wxid_customer:default",
            "system",
            "order_query_unavailable",
        )
    ]
    assert result["answer"] == ""
    assert result.get("outbound_messages", []) == []


def test_build_eyun_batch_key_prefers_group():
    from app.integrations.eyun.services.message_risk_control_service import build_eyun_batch_key

    key = build_eyun_batch_key(
        w_id="wid", target_wc_id="group_a", from_user="user_a"
    )

    assert key == "wid:group_a"


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_merges_same_user_window(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import enqueue_eyun_inbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

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
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
        lambda: now + timedelta(seconds=3),
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
    assert first["due_at"] == now
    assert second["due_at"] == now + timedelta(seconds=8)


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_caps_rolling_window(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import enqueue_eyun_inbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    offsets = iter((0, 4, 8, 12))
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
        lambda: now + timedelta(seconds=next(offsets)),
    )

    batches = []
    for message_id in range(1, 5):
        batches.append(
            await enqueue_eyun_inbound(
                {
                    "account": "acct",
                    "messageType": "60001",
                    "wcId": "bot",
                    "data": {
                        "wId": "wid",
                        "fromUser": "user",
                        "content": f"part-{message_id}",
                        "newMsgId": message_id,
                    },
                }
            )
        )

    assert [batch["due_at"] for batch in batches] == [
        now,
        now + timedelta(seconds=9),
        now + timedelta(seconds=13),
        now + timedelta(seconds=15),
    ]
    assert batches[-1]["message_count"] == 4


@pytest.mark.asyncio
async def test_enqueue_eyun_inbound_dedupes_provider_message_id(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import enqueue_eyun_inbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
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
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        enqueue_eyun_inbound,
    )

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
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
    assert second["due_at"] == now + timedelta(seconds=5)


@pytest.mark.asyncio
async def test_process_due_batch_calls_ai_once_for_merged_content(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import (
        enqueue_eyun_inbound,
        process_due_eyun_inbound_batches,
    )

    calls = []
    outbound = []
    now = datetime(2026, 7, 3, 12, 2, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

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
        depends_on_outbound_id=None,
        due_at=None,
    ):
        outbound.append(
            {
                "w_id": w_id,
                "wc_id": wc_id,
                "content": content,
                "source_batch_key": source_batch_key,
                "conversation_message_id": conversation_message_id,
                "depends_on_outbound_id": depends_on_outbound_id,
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
        "app.integrations.eyun.services.message_risk_control_service.handle_chat", fake_handle_chat
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.get_eyun_contact_snapshot",
        fake_contact_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_eyun_outbound",
        fake_enqueue_outbound,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
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
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
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
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

    attempted = await process_due_eyun_inbound_batches(limit=10)

    assert attempted == 1
    assert len(calls) == 1
    assert calls[0].message == "first\nsecond"
    assert calls[0].metadata["source_trace_id"].startswith("eyun_")
    assert calls[0].metadata["provider_message_ids"] == ["1", "2"]
    assert calls[0].metadata["source_message_count"] == 2
    assert calls[0].metadata["remark_name"] == "兰友张姐"
    assert calls[0].metadata["avatar_url"] == "https://example.com/avatar.jpg"
    assert outbound[0]["content"] == "merged reply"


@pytest.mark.asyncio
async def test_internal_workbench_title_is_not_sent_to_ai(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        enqueue_eyun_inbound,
        process_due_eyun_inbound_batches,
    )

    monkeypatch.setenv("EYUN_INBOUND_DEBOUNCE_SECONDS", "0")
    get_settings.cache_clear()

    async def fail_handle_chat(request):
        pytest.fail(f"internal title must not call AI: {request.message}")

    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.handle_chat", fail_handle_chat
    )
    batch = await enqueue_eyun_inbound(
        {
            "messageType": "60001",
            "wcId": "bot",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "content": "销售工作台 - 销售 Agent",
                "newMsgId": 9002,
            },
        }
    )

    assert await process_due_eyun_inbound_batches(limit=5) == 1
    with _get_session() as session:
        assert session.get(EyunInboundBatchModel, batch["id"]).status == "skipped"


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
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        enqueue_eyun_inbound,
        process_due_eyun_inbound_batches,
    )

    now = datetime(2026, 7, 3, 12, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.utcnow",
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
        "app.integrations.eyun.services.message_risk_control_service.handle_chat", fail_handle_chat
    )
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

    attempted = await process_due_eyun_inbound_batches(limit=10)

    with _get_session() as session:
        stored = session.get(EyunInboundBatchModel, batch["id"])
        outbound_count = session.query(EyunOutboundMessageModel).count()

    assert attempted == 1
    assert stored.status == "skipped"
    assert outbound_count == 0


@pytest.mark.asyncio
async def test_enqueue_outbound_adds_random_due_at(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import enqueue_wechat_outbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_reply_delay_seconds", lambda: 7
    )

    row = await enqueue_wechat_outbound(
        w_id="wid",
        wc_id="user",
        content="reply",
        source_batch_key="wid:user",
    )

    assert row["due_at"] == now + timedelta(seconds=7)
    assert row["status"] == "queued"
    assert row["priority"] == 100


@pytest.mark.asyncio
async def test_enqueue_outbound_always_creates_workbench_message(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        enqueue_wechat_outbound,
    )

    now = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

    outbound = await enqueue_wechat_outbound(
        w_id="wid",
        wc_id="customer",
        content="SOP message",
        source_batch_key="agent_wakeup:42",
        source_type="agent_wakeup",
        source_id="42",
        sender_type="system",
        sender_id="agent_wakeup",
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
    assert json.loads(message.metadata_json)["source_type"] == "agent_wakeup"


@pytest.mark.asyncio
async def test_process_batch_staggers_split_replies(monkeypatch):
    from app.integrations.eyun.services.message_risk_control_service import (
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

    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_reply_delay_seconds",
        lambda: 7,
    )
    spacings = iter((11, 15))
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random_outbound_spacing_seconds",
        lambda: next(spacings),
        raising=False,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.handle_chat", fake_handle_chat
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.get_eyun_contact_snapshot",
        lambda **kwargs: _async_value({}),
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.is_first_eyun_inbound_message",
        lambda session, batch_key: False,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.enqueue_eyun_outbound",
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
    from app.integrations.eyun.services.message_risk_control_service import random_outbound_spacing_seconds

    captured = {}

    def fake_uniform(minimum, maximum):
        captured["bounds"] = (minimum, maximum)
        return 2.5

    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.random.uniform", fake_uniform
    )

    assert random_outbound_spacing_seconds() == 2.5
    assert captured["bounds"][0] > 2
    assert captured["bounds"][1] > captured["bounds"][0]


@pytest.mark.asyncio
async def test_opening_slots_persistently_serialize_customers(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    now = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "random_opening_followup_seconds", lambda: 8)
    monkeypatch.setattr(service, "random_opening_interval_seconds", lambda: 6)

    first = await service._reserve_opening_delivery_slots(
        w_id="wid",
        message_count=2,
    )
    second = await service._reserve_opening_delivery_slots(
        w_id="wid",
        message_count=2,
    )

    assert first == [now, now + timedelta(seconds=8)]
    assert second == [
        now + timedelta(seconds=14),
        now + timedelta(seconds=22),
    ]
    with service._get_session() as session:
        control = session.get(EyunOpeningControlModel, "wid")
        assert control.next_due_at.replace(tzinfo=timezone.utc) == now + timedelta(
            seconds=28
        )


@pytest.mark.asyncio
async def test_new_friend_opening_sends_service_copy_without_first_order_package(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    now = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    queued = []
    recorded = []

    async def ignore_memories(*args, **kwargs):
        return None

    async def ensure_message(**kwargs):
        recorded.append(kwargs)
        return {"id": 100 + len(queued)}

    async def enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": 200 + len(queued)}

    async def reserve_slots(*, w_id, message_count):
        assert w_id == "wid"
        assert message_count == 1
        return [now]

    monkeypatch.setattr(service, "_record_opening_memories", ignore_memories)
    monkeypatch.setattr(service, "ensure_outbound_conversation_message", ensure_message)
    monkeypatch.setattr(service, "enqueue_eyun_outbound", enqueue)
    monkeypatch.setattr(service, "_reserve_opening_delivery_slots", reserve_slots)

    await service._send_opening_for_new_friend(
        {
            "w_id": "wid",
            "target_wc_id": "customer",
            "from_user": "customer",
            "from_group": None,
            "batch_key": "wid:customer",
            "created_at": now,
        }
    )

    assert [item["due_at"] for item in queued] == [now]
    assert [item["depends_on_outbound_id"] for item in queued] == [None]
    assert [item["route"] for item in recorded] == ["opening"]
    assert queued[0]["content"] == service.SERVICE_OPENING
    assert "老朋友，欢迎加到我私人微信～" in queued[0]["content"]
    assert "有老客专属铭品、成套组合福利" in queued[0]["content"]


@pytest.mark.asyncio
async def test_opening_backlog_keeps_queueing_without_pause(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    now = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    alerts = []

    async def capture_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(service, "random_reply_delay_seconds", lambda: 0)
    monkeypatch.setattr(service, "random_opening_followup_seconds", lambda: 8)
    monkeypatch.setattr(service, "random_opening_interval_seconds", lambda: 6)
    monkeypatch.setattr(service, "send_feishu_webhook_alert", capture_alert)

    slots = await service._reserve_opening_delivery_slots(
        w_id="wid",
        message_count=50,
    )

    assert len(slots) == 50
    assert slots[0] == now
    assert slots[-1] == now + timedelta(seconds=49 * 8)
    assert alerts == []
    with service._get_session() as session:
        control = session.get(EyunOpeningControlModel, "wid")
        assert control.pause_reason is None
        assert control.paused_until is None


def test_legacy_backlog_pause_is_cleared_on_startup():
    from app.integrations.eyun.services import message_risk_control_service as service

    now = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
    with service._get_session() as session:
        session.add(
            EyunOpeningControlModel(
                w_id="wid",
                paused_until=now + timedelta(minutes=30),
                pause_reason="opening_queue_backlog",
                consecutive_failures=0,
                updated_at=now,
            )
        )
        session.commit()

    service._initialized_urls.discard(get_settings().chat_log_db_url)
    with service._get_session() as session:
        control = session.get(EyunOpeningControlModel, "wid")
        assert control.paused_until is None
        assert control.pause_reason is None


@pytest.mark.asyncio
async def test_opening_provider_risk_signal_pauses_immediately(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    now = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    alerts = []

    async def capture_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(service, "send_feishu_webhook_alert", capture_alert)
    with service._get_session() as session:
        message = ConversationMessageModel(
            conversation_id="wechat:customer:default",
            delivery_status="queued",
            sender_type="ai",
            sender_id="ai",
            content="opening",
            route="opening",
            metadata_json="{}",
            created_at=now,
        )
        session.add(message)
        session.flush()
        outbound = EyunOutboundMessageModel(
            w_id="wid",
            wc_id="customer",
            content="opening",
            conversation_message_id=message.id,
            status="queued",
            priority=100,
            due_at=now,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
        session.add(outbound)
        session.commit()
        outbound_id = outbound.id

    await service._handle_opening_send_failure(
        outbound_id,
        RuntimeError("发送频率过快，请稍后重试"),
    )

    assert alerts and "开场白风控暂停" in alerts[0]
    with service._get_session() as session:
        control = session.get(EyunOpeningControlModel, "wid")
        assert control.pause_reason == "provider_risk_signal"
        assert control.consecutive_failures == 1


@pytest.mark.asyncio
async def test_existing_opening_queue_also_obeys_slow_send_interval(monkeypatch):
    from app.integrations.eyun.services import message_risk_control_service as service

    last_sent_at = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    now = last_sent_at + timedelta(seconds=1)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(service, "random_opening_interval_seconds", lambda: 6)
    with service._get_session() as session:
        session.add(
            EyunOpeningControlModel(
                w_id="wid",
                last_sent_at=last_sent_at,
                consecutive_failures=0,
                updated_at=last_sent_at,
            )
        )
        message = ConversationMessageModel(
            conversation_id="wechat:customer:default",
            delivery_status="queued",
            sender_type="ai",
            sender_id="ai",
            content="opening",
            route="opening",
            metadata_json="{}",
            created_at=last_sent_at,
        )
        session.add(message)
        session.flush()
        outbound = EyunOutboundMessageModel(
            w_id="wid",
            wc_id="customer",
            content="opening",
            conversation_message_id=message.id,
            status="queued",
            priority=100,
            due_at=now,
            attempts=0,
            created_at=last_sent_at,
            updated_at=last_sent_at,
        )
        session.add(outbound)
        session.commit()
        outbound_id = outbound.id

    attempted = await service.process_due_eyun_outbound_messages()

    assert attempted == 0
    with service._get_session() as session:
        outbound = session.get(EyunOutboundMessageModel, outbound_id)
        assert outbound.status == "queued"
        assert outbound.due_at.replace(tzinfo=timezone.utc) == last_sent_at + timedelta(
            seconds=6
        )


def test_outbound_text_messages_are_plain_short_messages():
    from app.integrations.eyun.services.message_risk_control_service import _outbound_messages

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
    from app.integrations.eyun.services.message_risk_control_service import _outbound_messages

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
    from app.integrations.eyun.services.message_risk_control_service import _outbound_messages

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
    from app.integrations.eyun.services.message_risk_control_service import _outbound_messages

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
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        process_due_eyun_outbound_messages,
    )

    now = datetime(2026, 7, 13, 7, 0, 10, tzinfo=timezone.utc)
    provider_sent_at = datetime(2026, 7, 13, 7, 0, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

    async def fake_send_eyun_text(**kwargs):
        del kwargs
        return {"code": "1000", "data": {"createTime": int(provider_sent_at.timestamp())}}

    monkeypatch.setattr(
        "app.integrations.eyun.services.eyun_callback_service.send_eyun_text", fake_send_eyun_text
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
    from app.integrations.eyun.services.message_risk_control_service import (
        _get_session,
        process_due_eyun_outbound_messages,
    )

    sent = []
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.integrations.eyun.services.message_risk_control_service.utcnow", lambda: now)

    async def fake_send_eyun_text(*, w_id, wc_id, content):
        sent.append((w_id, wc_id, content))

    monkeypatch.setattr(
        "app.integrations.eyun.services.eyun_callback_service.send_eyun_text", fake_send_eyun_text
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
    from app.integrations.eyun.services.message_risk_control_service import eyun_worker_tick

    calls = []

    async def fake_inbound(limit=10):
        calls.append("inbound")
        return 1

    async def fake_outbound(limit=5):
        calls.append("outbound")
        return 1

    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.process_due_eyun_inbound_batches",
        fake_inbound,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.message_risk_control_service.process_due_eyun_outbound_messages",
        fake_outbound,
    )

    await eyun_worker_tick()

    assert calls == ["inbound", "outbound"]
