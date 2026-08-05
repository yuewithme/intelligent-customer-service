import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domains.sales.services import daily_touch_service as service
from app.infrastructure.database.models import (
    AgentWakeupModel,
    ConversationMessageModel,
    ConversationModel,
    EyunContactModel,
)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'business.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("DAILY_TOUCH_ENABLED", "true")
    monkeypatch.setenv("DAILY_TOUCH_TIMEZONE", "Asia/Shanghai")
    get_settings.cache_clear()
    service._database_factories.clear()
    service._chat_factories.clear()


def _insert_contact():
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        session.add(
            EyunContactModel(
                tenant_id="tenant_default",
                owner_account_id="owner-1",
                wc_id="customer-1",
                current_w_id="wid-1",
                display_name="兰友",
                first_seen_at=now,
                last_seen_at=now,
                status="active",
                missing_count=0,
                discovery_source="test",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def test_daily_wakeup_is_durable_and_idempotent(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)

    assert service.ensure_daily_touch_wakeups(now=now) == 1
    assert service.ensure_daily_touch_wakeups(now=now) == 0
    with service._database_session() as session:
        rows = list(session.query(AgentWakeupModel).all())
    assert len(rows) == 1
    assert rows[0].kind == "daily_touch"
    assert rows[0].local_date == "2026-08-05"


def test_only_real_sent_message_completes_daily_touch(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._chat_session() as session:
        conversation = ConversationModel(
            conversation_id="wechat:customer-1:default",
            channel="wechat",
            user_id="customer-1",
            session_id="default",
            tenant_id="tenant_default",
            status="ai_active",
            unread_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(conversation)
        session.add_all(
            [
                ConversationMessageModel(
                    conversation_id=conversation.conversation_id,
                    delivery_status="queued",
                    sender_type="ai",
                    sender_id="ai",
                    content="排队消息",
                    metadata_json="{}",
                    created_at=now,
                ),
                ConversationMessageModel(
                    conversation_id=conversation.conversation_id,
                    delivery_status="sent",
                    sender_type="human",
                    sender_id="operator-1",
                    content="人工已经完成今日服务触达",
                    metadata_json="{}",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    snapshot = service.get_daily_touch_snapshot("customer-1", now=now)
    assert snapshot["completed_today"] is True
    assert snapshot["sent_message_count"] == 1
    assert snapshot["last_sender"] == "human"
    assert service.ensure_daily_touch_wakeups(now=now) == 0


def test_explicit_refusal_reduces_daily_maximum_without_abandoning(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    service.record_agent_relationship_state(
        customer_id="customer-1",
        tenant_id="tenant_default",
        customer_signal="explicit_refusal",
        commercial_judgment="客户明确拒绝，后续降低压力",
        relationship_purpose="保持服务关系",
        source_trace_id="trace-refusal",
    )

    snapshot = service.get_daily_touch_snapshot("customer-1", now=now)
    assert snapshot["explicit_refusal"] is True
    assert snapshot["daily_maximum"] == 1
    assert service.ensure_daily_touch_wakeups(now=now) == 1


def test_multi_message_bundle_counts_as_one_touch(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._chat_session() as session:
        conversation = ConversationModel(
            conversation_id="wechat:customer-1:default",
            channel="wechat",
            user_id="customer-1",
            session_id="default",
            tenant_id="tenant_default",
            status="ai_active",
            unread_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(conversation)
        session.add_all(
            [
                ConversationMessageModel(
                    conversation_id=conversation.conversation_id,
                    delivery_status="sent",
                    sender_type="ai",
                    sender_id="ai",
                    content=content,
                    metadata_json=json.dumps(
                        {"source_batch_key": "agent_wakeup:42"}
                    ),
                    created_at=now + timedelta(seconds=index),
                )
                for index, content in enumerate(("先回应客户", "再发送资料"))
            ]
        )
        session.commit()

    snapshot = service.get_daily_touch_snapshot("customer-1", now=now)
    assert snapshot["sent_message_count"] == 1
    assert snapshot["sent_unit_count"] == 2


def test_failed_daily_touch_is_rescheduled_with_bounded_retry(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with service._database_session() as session:
        row = AgentWakeupModel(
            dedup_key="daily:test:retry",
            tenant_id="tenant_default",
            customer_id="customer-1",
            kind="daily_touch",
            local_date="2026-08-05",
            reason="完成今日触达",
            checklist_json="[]",
            status="queued",
            due_at=now,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        wakeup_id = row.id

    service.sync_agent_wakeup_from_outbound(
        f"agent_wakeup:{wakeup_id}", "failed", "provider timeout"
    )

    with service._database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        assert row.status == "pending"
        assert row.due_at.replace(tzinfo=timezone.utc) > now
        assert "已安排重试" in row.last_error


@pytest.mark.asyncio
async def test_stale_processing_wakeup_recovers_its_lease(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        row = AgentWakeupModel(
            dedup_key="daily:test:lease",
            tenant_id="tenant_default",
            customer_id="customer-1",
            kind="daily_touch",
            local_date="2026-08-05",
            reason="完成今日触达",
            checklist_json="[]",
            status="processing",
            due_at=now - timedelta(hours=1),
            attempts=1,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(minutes=20),
        )
        session.add(row)
        session.commit()
        wakeup_id = row.id

    processed = []

    async def fake_process(candidate_id, *, now):
        processed.append((candidate_id, now))
        return False

    monkeypatch.setattr(service, "_process_wakeup", fake_process)
    assert await service.process_due_agent_wakeups(now=now) == 0

    assert processed == [(wakeup_id, now)]
    with service._database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        assert row.status == "processing"
        assert row.attempts == 2
