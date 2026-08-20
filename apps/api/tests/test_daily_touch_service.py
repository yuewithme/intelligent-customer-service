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
    EyunOutboundMessageModel,
    UserProfileModel,
)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'business.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("DAILY_TOUCH_ENABLED", "true")
    monkeypatch.setenv("DAILY_TOUCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_MATERIAL_STORY_TIME", "07:00")
    monkeypatch.setenv("SERVICE_MATERIAL_KNOWLEDGE_TIME", "10:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOPIC_TIME", "14:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_GRACE_MINUTES", "20")
    get_settings.cache_clear()
    service._database_factories.clear()
    service._chat_factories.clear()
    service._last_contact_sync_at = None
    service._last_contact_sync_attempt_at = None


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


def _tag_service_customer():
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        session.add(
            UserProfileModel(
                user_id="customer-1",
                tenant_id="tenant_default",
                channel="wechat",
                customer_tags_json='["服务中"]',
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


def test_service_tag_schedules_three_fixed_daily_slots(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)

    assert service.ensure_service_material_touch_wakeups(now=now) == 3
    assert service.ensure_service_material_touch_wakeups(now=now) == 0
    with service._database_session() as session:
        rows = list(
            session.query(AgentWakeupModel)
            .order_by(AgentWakeupModel.due_at.asc())
            .all()
        )
    assert [row.kind for row in rows] == ["service_material_touch"] * 3
    assert [row.reason for row in rows] == [
        "service_material:story",
        "service_material:knowledge",
        "service_material:topic",
    ]
    assert [row.due_at.hour for row in rows] == [23, 2, 6]
    assert service.ensure_daily_touch_wakeups(
        now=datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    ) == 0


def test_late_service_tag_only_schedules_remaining_slot(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()

    assert service.ensure_service_material_touch_wakeups(
        now=datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)
    ) == 1
    with service._database_session() as session:
        row = session.query(AgentWakeupModel).one()
    assert row.reason == "service_material:topic"


def test_explicit_refusal_excludes_three_service_touches(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    service.record_agent_relationship_state(
        customer_id="customer-1",
        tenant_id="tenant_default",
        customer_signal="explicit_refusal",
        commercial_judgment="客户明确拒绝",
        relationship_purpose="保持服务关系",
        source_trace_id="trace-refusal-service",
    )

    assert service.ensure_service_material_touch_wakeups(now=now) == 0
    assert service.ensure_daily_touch_wakeups(now=now) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media", "expected_type"),
    (
        (
            {
                "format": "video",
                "url": "https://media.example.com/story.mp4",
                "thumb_url": "https://media.example.com/story.jpg",
                "copy_text": "今天分享一个兰花名品故事。",
            },
            "video",
        ),
        (
            {
                "format": "image",
                "url": "https://media.example.com/story.jpg",
                "thumb_url": "",
                "copy_text": "今天分享一个兰花名品故事。",
            },
            "image",
        ),
    ),
)
async def test_due_service_touch_queues_copy_before_media(
    monkeypatch, tmp_path, media, expected_type
):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    before_slot = datetime(2026, 8, 4, 22, 59, tzinfo=timezone.utc)
    assert service.ensure_service_material_touch_wakeups(now=before_slot) == 3

    monkeypatch.setattr(
        service,
        "select_scheduled_agent_media",
        lambda **_: media,
    )
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    from app.integrations.eyun.services import message_risk_control_service

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )
    assert await service.process_due_agent_wakeups(
        now=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    ) == 1

    assert [item["message_type"] for item in queued] == ["text", expected_type]
    assert queued[0]["depends_on_outbound_id"] is None
    assert queued[1]["depends_on_outbound_id"] == 1
    if expected_type == "video":
        assert json.loads(queued[1]["content"]) == {
            "path": "https://media.example.com/story.mp4",
            "thumb_path": "https://media.example.com/story.jpg",
        }
    else:
        assert queued[1]["content"] == "https://media.example.com/story.jpg"


@pytest.mark.asyncio
async def test_service_touch_continues_during_handoff_pending(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 59, tzinfo=timezone.utc)
    with service._chat_session() as session:
        session.add(
            ConversationModel(
                conversation_id="wechat:customer-1:default",
                channel="wechat",
                user_id="customer-1",
                session_id="default",
                tenant_id="tenant_default",
                status="handoff_pending",
                unread_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    assert service.ensure_service_material_touch_wakeups(now=now) == 3
    monkeypatch.setattr(
        service,
        "select_scheduled_agent_media",
        lambda **_: {
            "format": "image",
            "url": "https://media.example.com/story.jpg",
            "thumb_url": "",
            "copy_text": "今天分享一个兰花名品故事。",
        },
    )
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    from app.integrations.eyun.services import message_risk_control_service

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )

    assert await service.process_due_agent_wakeups(
        now=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    ) == 1
    assert [item["message_type"] for item in queued] == ["text", "image"]
    with service._database_session() as session:
        row = session.scalar(
            service.select(AgentWakeupModel).where(
                AgentWakeupModel.reason == "service_material:story"
            )
        )
    assert row.status == "queued"
    assert service.validate_agent_wakeup_before_send(f"agent_wakeup:{row.id}") is True


@pytest.mark.asyncio
async def test_contact_refresh_failure_does_not_block_scheduled_work(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    calls = []

    async def failed_refresh():
        calls.append("refresh")
        raise RuntimeError("provider unavailable")

    async def process_due():
        calls.append("process")
        return 0

    monkeypatch.setattr(service, "_refresh_contacts_if_due", failed_refresh)
    monkeypatch.setattr(
        service, "ensure_daily_touch_wakeups", lambda: calls.append("daily")
    )
    monkeypatch.setattr(
        service,
        "ensure_service_material_touch_wakeups",
        lambda: calls.append("service"),
    )
    monkeypatch.setattr(service, "process_due_agent_wakeups", process_due)

    await service._daily_touch_worker_tick()

    assert calls == ["daily", "service", "process", "refresh"]


def test_service_touch_completes_after_copy_and_media_are_sent(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    batch_key = "agent_wakeup:1"
    with service._database_session() as session:
        session.add(
            AgentWakeupModel(
                id=1,
                dedup_key="service-material:test:bundle",
                tenant_id="tenant_default",
                customer_id="customer-1",
                kind="service_material_touch",
                local_date="2026-08-05",
                reason="service_material:story",
                checklist_json="[]",
                status="queued",
                due_at=now,
                attempts=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            [
                EyunOutboundMessageModel(
                    w_id="wid-1",
                    wc_id="customer-1",
                    content=content,
                    source_batch_key=batch_key,
                    status=status,
                    priority=50,
                    due_at=now,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
                for content, status in (("文案", "sent"), ("视频", "queued"))
            ]
        )
        session.commit()

    service.sync_agent_wakeup_from_outbound(batch_key, "sent")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "queued"
        media = session.scalar(
            service.select(EyunOutboundMessageModel).where(
                EyunOutboundMessageModel.content == "视频"
            )
        )
        media.status = "sent"
        session.commit()

    service.sync_agent_wakeup_from_outbound(batch_key, "sent")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "completed"


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
