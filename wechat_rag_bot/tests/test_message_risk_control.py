from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db.models import (
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
    assert settings.eyun_send_max_per_minute == 40
    assert settings.eyun_send_min_interval_seconds == 1.6
    assert settings.eyun_reply_jitter_min_seconds == 2
    assert settings.eyun_reply_jitter_max_seconds == 12


def test_risk_control_models_have_table_names():
    assert EyunInboundBatchModel.__tablename__ == "eyun_inbound_batches"
    assert EyunInboundMessageModel.__tablename__ == "eyun_inbound_messages"
    assert EyunOutboundMessageModel.__tablename__ == "eyun_outbound_messages"
    assert EyunSendRateModel.__tablename__ == "eyun_send_rates"


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

    async def fake_enqueue_outbound(*, w_id, wc_id, content, source_batch_key):
        outbound.append(
            {
                "w_id": w_id,
                "wc_id": wc_id,
                "content": content,
                "source_batch_key": source_batch_key,
            }
        )

    monkeypatch.setattr(
        "app.services.message_risk_control_service.handle_chat", fake_handle_chat
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
    assert outbound[0]["content"] == "merged reply"


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
    from app.services.message_risk_control_service import enqueue_eyun_outbound

    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr(
        "app.services.message_risk_control_service.random_reply_delay_seconds", lambda: 7
    )

    row = await enqueue_eyun_outbound(
        w_id="wid",
        wc_id="user",
        content="reply",
        source_batch_key="wid:user",
    )

    assert row["due_at"] == now + timedelta(seconds=7)
    assert row["status"] == "queued"


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
