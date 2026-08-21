import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domains.sales.services import service_material_touch_service as service
from app.infrastructure.database.models import (
    AgentWakeupModel,
    EyunContactModel,
    EyunOutboundMessageModel,
    UserProfileModel,
)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'business.db'}")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_BATCH_SIZE", "20")
    monkeypatch.setenv("SERVICE_MATERIAL_STORY_TIME", "07:00")
    monkeypatch.setenv("SERVICE_MATERIAL_KNOWLEDGE_TIME", "10:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOPIC_TIME", "14:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_GRACE_MINUTES", "20")
    get_settings.cache_clear()
    service._database_factories.clear()
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


def test_service_tag_schedules_three_fixed_daily_slots(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)

    assert service.ensure_service_material_touch_tasks(now=now) == 3
    assert service.ensure_service_material_touch_tasks(now=now) == 0
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


def test_late_service_tag_only_schedules_remaining_slot(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()

    assert service.ensure_service_material_touch_tasks(
        now=datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)
    ) == 1
    with service._database_session() as session:
        row = session.query(AgentWakeupModel).one()
    assert row.reason == "service_material:topic"


def test_explicit_refusal_excludes_service_touches(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    service.record_agent_relationship_state(
        customer_id="customer-1",
        tenant_id="tenant_default",
        customer_signal="explicit_refusal",
        commercial_judgment="客户明确拒绝",
        relationship_purpose="停止自动触达",
        source_trace_id="trace-refusal-service",
    )

    assert service.ensure_service_material_touch_tasks(
        now=datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    ) == 0


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
    assert service.ensure_service_material_touch_tasks(now=before_slot) == 3
    monkeypatch.setattr(service, "select_scheduled_agent_media", lambda **_: media)
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}

    from app.integrations.eyun.services import (
        eyun_material_service,
        message_risk_control_service,
    )

    async def fake_materialize(**kwargs):
        assert kwargs["message_type"] == expected_type
        return {"id": 99}

    monkeypatch.setattr(
        eyun_material_service, "materialize_eyun_outbound_media", fake_materialize
    )
    monkeypatch.setattr(
        message_risk_control_service, "enqueue_wechat_outbound", fake_enqueue
    )

    assert await service.process_due_service_material_touches(
        now=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    ) == 1
    assert [item["message_type"] for item in queued] == ["text", expected_type]
    assert queued[0]["depends_on_outbound_id"] is None
    assert queued[1]["depends_on_outbound_id"] == 1
    assert [item["material_id"] for item in queued] == [None, 99]
    assert queued[0]["source_batch_key"].startswith("service_material_touch:")
    if expected_type == "video":
        assert json.loads(queued[1]["content"]) == {
            "path": "https://media.example.com/story.mp4",
            "thumb_path": "https://media.example.com/story.jpg",
        }
    else:
        assert queued[1]["content"] == "https://media.example.com/story.jpg"


@pytest.mark.asyncio
async def test_service_media_conversion_failure_retries_whole_bundle(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    before_slot = datetime(2026, 8, 4, 22, 59, tzinfo=timezone.utc)
    assert service.ensure_service_material_touch_tasks(now=before_slot) == 3
    monkeypatch.setattr(
        service,
        "select_scheduled_agent_media",
        lambda **_: {
            "format": "video",
            "url": "https://media.example.com/story.mp4",
            "thumb_url": "https://media.example.com/story.jpg",
            "copy_text": "今天分享一个兰花名品故事。",
        },
    )
    from app.integrations.eyun.services import (
        eyun_material_service,
        message_risk_control_service,
    )

    async def failed_materialize(**kwargs):
        raise TimeoutError("provider timeout")

    async def unexpected_enqueue(**kwargs):
        raise AssertionError("text must not enqueue before media is ready")

    monkeypatch.setattr(
        eyun_material_service, "materialize_eyun_outbound_media", failed_materialize
    )
    monkeypatch.setattr(
        message_risk_control_service, "enqueue_wechat_outbound", unexpected_enqueue
    )
    due = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)

    assert await service.process_due_service_material_touches(now=due) == 0
    with service._database_session() as session:
        row = session.scalar(
            service.select(AgentWakeupModel).where(
                AgentWakeupModel.reason == "service_material:story"
            )
        )
    assert row.status == "pending"
    assert row.due_at.replace(tzinfo=timezone.utc) == due + timedelta(minutes=15)
    assert "已安排重试" in row.last_error


@pytest.mark.asyncio
async def test_worker_refresh_failure_does_not_block_scheduled_work(
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
        service, "ensure_service_material_touch_tasks", lambda: calls.append("create")
    )
    monkeypatch.setattr(service, "process_due_service_material_touches", process_due)

    await service._service_material_touch_worker_tick()

    assert calls == ["create", "process", "refresh"]


def test_touch_completes_only_after_copy_and_media_are_sent(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    batch_key = "service_material_touch:1"
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

    service.sync_service_material_touch_from_outbound(batch_key, "sent")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "queued"
        media = session.scalar(
            service.select(EyunOutboundMessageModel).where(
                EyunOutboundMessageModel.content == "视频"
            )
        )
        media.status = "sent"
        session.commit()

    service.sync_service_material_touch_from_outbound(batch_key, "sent")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "completed"


@pytest.mark.asyncio
async def test_stale_service_task_recovers_its_lease(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        row = AgentWakeupModel(
            dedup_key="service-material:test:lease",
            tenant_id="tenant_default",
            customer_id="customer-1",
            kind="service_material_touch",
            local_date="2026-08-05",
            reason="service_material:story",
            checklist_json="[]",
            status="processing",
            due_at=now - timedelta(hours=1),
            attempts=1,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(minutes=20),
        )
        session.add(row)
        session.commit()
        task_id = row.id

    processed = []

    async def fake_process(candidate_id, *, now):
        processed.append((candidate_id, now))
        return False

    monkeypatch.setattr(service, "_process_service_material_touch", fake_process)
    assert await service.process_due_service_material_touches(now=now) == 0
    assert processed == [(task_id, now)]
    with service._database_session() as session:
        row = session.get(AgentWakeupModel, task_id)
        assert row.status == "processing"
        assert row.attempts == 2


@pytest.mark.asyncio
async def test_worker_ignores_non_service_tasks(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        for kind in ("retired_touch", "service_material_touch"):
            session.add(
                AgentWakeupModel(
                    dedup_key=f"isolation:{kind}",
                    tenant_id="tenant_default",
                    customer_id="customer-1",
                    kind=kind,
                    local_date="2026-08-05",
                    reason="隔离测试",
                    checklist_json="[]",
                    status="pending",
                    due_at=now,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        ids_by_kind = {
            row.kind: row.id for row in session.query(AgentWakeupModel).all()
        }

    processed = []

    async def fake_process(candidate_id, *, now):
        processed.append(candidate_id)
        return True

    monkeypatch.setattr(service, "_process_service_material_touch", fake_process)

    assert await service.process_due_service_material_touches(now=now) == 1
    assert processed == [ids_by_kind["service_material_touch"]]
    with service._database_session() as session:
        retired = session.get(AgentWakeupModel, ids_by_kind["retired_touch"])
        assert retired.status == "pending"


def test_queued_non_service_task_is_blocked_before_send(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        row = AgentWakeupModel(
            dedup_key="isolation:queued-retired",
            tenant_id="tenant_default",
            customer_id="customer-1",
            kind="retired_touch",
            local_date="2026-08-05",
            reason="隔离测试",
            checklist_json="[]",
            status="queued",
            due_at=now,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        task_id = row.id

    assert service.validate_service_material_touch_before_send(
        f"agent_wakeup:{task_id}"
    ) is False
    with service._database_session() as session:
        row = session.get(AgentWakeupModel, task_id)
        assert row.status == "cancelled"
        assert row.last_error == "非服务中触达任务已停用"


def test_legacy_touch_entry_points_are_removed():
    assert not hasattr(service, "ensure_daily_touch_wakeups")
    assert not hasattr(service, "schedule_agent_wakeup")
    assert not hasattr(service, "process_due_agent_wakeups")
