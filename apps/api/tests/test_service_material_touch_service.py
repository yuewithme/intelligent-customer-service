import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domains.sales.services import service_material_touch_service as service
from app.infrastructure.database.models import (
    AgentWakeupModel,
    ConversationMemoryModel,
    ConversationMessageModel,
    MemoryEventModel,
    EyunContactModel,
    EyunOutboundDeliveryEventModel,
    EyunOutboundMessageModel,
    EyunOutboundProviderMessageIdModel,
    UserProfileModel,
)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'business.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_BATCH_SIZE", "20")
    monkeypatch.setenv("SERVICE_MATERIAL_STORY_TIME", "07:00")
    monkeypatch.setenv("SERVICE_MATERIAL_KNOWLEDGE_TIME", "10:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOPIC_TIME", "14:00")
    monkeypatch.setenv("SERVICE_MATERIAL_TOUCH_GRACE_MINUTES", "20")
    monkeypatch.setenv("EYUN_WID", "wid-1")
    monkeypatch.setenv("EYUN_WC_ID", "owner-1")
    monkeypatch.setenv("MEMORY_V2_WRITE_ENABLED", "false")
    get_settings.cache_clear()
    service._database_factories.clear()
    service._chat_factories.clear()
    service._last_contact_sync_at = None
    service._last_contact_sync_attempt_at = None


def _insert_contact(customer_id="customer-1"):
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        session.add(
            EyunContactModel(
                tenant_id="tenant_default",
                owner_account_id="owner-1",
                wc_id=customer_id,
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


def _tag_service_customer(customer_id="customer-1"):
    now = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)
    with service._database_session() as session:
        session.add(
            UserProfileModel(
                user_id=customer_id,
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


def test_seeding_requires_both_preferences_and_runs_alongside_service(monkeypatch, tmp_path):
    from app.domains.handoff.services.handoff_notification_service import update_sop_enabled
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope="seeding") == 0
    update_sop_enabled("seeding", True)
    with service._database_session() as session:
        profile = session.get(UserProfileModel, "customer-1")
        profile.customer_tags_json = json.dumps(["服务中", "建兰"], ensure_ascii=False)
        session.commit()
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope="seeding") == 0
    with service._database_session() as session:
        profile = session.get(UserProfileModel, "customer-1")
        profile.customer_tags_json = json.dumps(["服务中", "建兰", "浓香"], ensure_ascii=False)
        session.commit()
    assert service.ensure_service_material_touch_tasks(now=now) == 3
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope="seeding") == 1
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope="seeding") == 0
    with service._database_session() as session:
        row = session.query(AgentWakeupModel).filter_by(kind="seeding_material_touch").one()
        assert row.due_at.hour == 7
        assert row.local_date == "2026-08-05"


@pytest.mark.parametrize("scope", ["service", "seeding"])
@pytest.mark.asyncio
async def test_sop_off_cancels_pending_and_queued_touches(monkeypatch, tmp_path, scope):
    from app.domains.handoff.services.handoff_notification_service import update_sop_enabled
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    update_sop_enabled(scope, True)
    with service._database_session() as session:
        profile = session.get(UserProfileModel, "customer-1")
        profile.customer_tags_json = json.dumps(["服务中", "建兰", "浓香"], ensure_ascii=False)
        session.commit()
    now = datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope=scope) == 1
    update_sop_enabled(scope, False)
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope=scope) == 0
    assert await service.process_due_service_material_touches(now=now + timedelta(hours=1)) == 0
    with service._database_session() as session:
        row = session.query(AgentWakeupModel).one()
        assert row.status == "cancelled"
        row.status = "queued"
        task_id = row.id
        session.commit()
    assert service.validate_service_material_touch_before_send(f"service_material_touch:{task_id}") is False


def test_global_handoff_does_not_disable_service_touches(monkeypatch, tmp_path):
    from app.domains.handoff.schemas.handoff_notification import (
        HandoffNotificationSettingsUpdateRequest,
    )
    from app.domains.handoff.services.handoff_notification_service import (
        update_handoff_notification_settings,
    )

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    with service._database_session() as session:
        contact_id = session.query(EyunContactModel.id).scalar()
    update_handoff_notification_settings(
        HandoffNotificationSettingsUpdateRequest(
            global_handoff_enabled=True,
            recipient_contact_ids=[contact_id],
            message_text="请及时跟进。",
        )
    )

    assert service.ensure_service_material_touch_tasks(
        now=datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    ) == 3


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
    assert queued[0]["conversation_message_id"] != queued[1]["conversation_message_id"]
    assert queued[0]["delivery_metadata"]["scheduled_at"]
    assert queued[1]["delivery_metadata"]["scheduled_at"]
    assert {item["sender_type"] for item in queued} == {"system"}
    assert {item["sender_id"] for item in queued} == {"service_material_touch"}
    assert {item["session_id"] for item in queued} == {"owner-1"}
    if expected_type == "video":
        assert json.loads(queued[1]["content"]) == {
            "path": "https://media.example.com/story.mp4",
            "thumb_path": "https://media.example.com/story.jpg",
        }
    else:
        assert queued[1]["content"] == "https://media.example.com/story.jpg"


@pytest.mark.asyncio
async def test_seeding_queues_paired_video_and_rechecks_changed_preferences(monkeypatch, tmp_path):
    from app.domains.handoff.services.handoff_notification_service import update_sop_enabled
    from app.integrations.eyun.services import eyun_material_service, message_risk_control_service
    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    update_sop_enabled("seeding", True)
    with service._database_session() as session:
        profile = session.get(UserProfileModel, "customer-1")
        profile.customer_tags_json = json.dumps(["建兰", "浓香"], ensure_ascii=False)
        session.commit()
    selected = []
    def select_video(**kwargs):
        selected.append(kwargs["preference_groups"])
        return {"format": "video", "url": "https://media.example.com/product.mp4",
                "thumb_url": "https://media.example.com/product.jpg", "copy_text": "今天分享这款浓香建兰。"}
    queued = []
    async def materialize(**kwargs):
        return {"id": 99}
    async def enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}
    monkeypatch.setattr(service, "select_preference_agent_video", select_video)
    monkeypatch.setattr(eyun_material_service, "materialize_eyun_outbound_media", materialize)
    monkeypatch.setattr(message_risk_control_service, "enqueue_wechat_outbound", enqueue)
    now = datetime(2026, 8, 5, 7, 0, tzinfo=timezone.utc)
    assert service.ensure_service_material_touch_tasks(now=now, sop_scope="seeding") == 1
    assert await service.process_due_service_material_touches(now=now) == 1
    assert selected == [({"建兰"}, {"浓香"})]
    assert [item["message_type"] for item in queued] == ["text", "video"]
    assert queued[1]["depends_on_outbound_id"] == 1
    assert {item["source_type"] for item in queued} == {"seeding_material_touch"}
    batch = queued[0]["source_batch_key"]
    assert service.validate_service_material_touch_before_send(batch)
    with service._database_session() as session:
        profile = session.get(UserProfileModel, "customer-1")
        profile.customer_tags_json = json.dumps(["春兰", "浓香"], ensure_ascii=False)
        session.commit()
    assert not service.validate_service_material_touch_before_send(batch)


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
    with service._chat_session() as session:
        messages = session.query(ConversationMessageModel).order_by(
            ConversationMessageModel.id
        ).all()
    assert len(messages) == 2
    assert {message.delivery_status for message in messages} == {"waiting_material"}
    assert all(
        json.loads(message.metadata_json)["delivery_timestamps"]["scheduled_at"]
        for message in messages
    )
    assert service.retry_service_material_touch_task_now(
        f"service_material_touch:{row.id}"
    )
    with service._database_session() as session:
        retried = session.get(AgentWakeupModel, row.id)
        assert retried.status == "pending"
        assert retried.attempts == 0
        assert "立即重试" in retried.last_error


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


def test_touch_completes_only_after_copy_and_media_are_confirmed(monkeypatch, tmp_path):
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
        session.commit()
    with service._chat_session() as session:
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

    service.sync_service_material_touch_from_outbound(batch_key, "confirmed")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "queued"
    with service._chat_session() as session:
        media = session.scalar(
            service.select(EyunOutboundMessageModel).where(
                EyunOutboundMessageModel.content == "视频"
            )
        )
        media.status = "sent"
        session.commit()

    service.sync_service_material_touch_from_outbound(batch_key, "confirmed")
    with service._database_session() as session:
        assert session.get(AgentWakeupModel, 1).status == "completed"


def test_touch_delivery_stats_expose_failure_details(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with service._database_session() as session:
        session.add(
            AgentWakeupModel(
                id=91,
                dedup_key="service-material:stats:91",
                tenant_id="tenant_default",
                customer_id="customer-91",
                kind="service_material_touch",
                local_date="2026-09-02",
                reason="service_material:topic",
                checklist_json="[]",
                status="failed",
                due_at=now,
                attempts=3,
                last_error="媒体发送失败",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    with service._chat_session() as session:
        outbound = EyunOutboundMessageModel(
                w_id="wid-1",
                wc_id="customer-91",
                content="image",
                source_batch_key="service_material_touch:91",
                delivery_key="service_material_touch:91:media",
                status="failed",
                priority=20,
                due_at=now,
                attempts=4,
                last_error="provider failed",
                created_at=now,
                updated_at=now,
            )
        session.add(outbound)
        session.flush()
        session.add(
            EyunOutboundProviderMessageIdModel(
                outbound_message_id=outbound.id,
                w_id="wid-1",
                wc_id="customer-91",
                provider_message_id="provider-91",
                source="send_response",
                created_at=now,
            )
        )
        session.add(
            EyunOutboundDeliveryEventModel(
                outbound_message_id=outbound.id,
                source_batch_key="service_material_touch:91",
                event="send_failed",
                status_from="sending",
                status_to="failed",
                attempt=4,
                provider_code="2001",
                provider_message_id="provider-91",
                error="provider failed",
                metadata_json="{}",
                created_at=now,
            )
        )
        session.commit()

    stats = service.get_service_material_touch_delivery_stats()
    assert stats["task_statuses"] == {"failed": 1}
    assert stats["outbound_statuses"] == {"failed": 1}
    assert stats["attention_count"] == 1
    message = stats["items"][0]["messages"][0]
    assert message["last_error"] == "provider failed"
    assert message["delivery_key"] == "service_material_touch:91:media"
    assert message["provider_message_ids"] == ["provider-91"]
    assert message["events"][0]["event"] == "send_failed"


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


@pytest.mark.parametrize("memory_v2", (False, True))
@pytest.mark.asyncio
async def test_touch_memory_includes_accepted_history_once_and_keeps_account_scope(monkeypatch, tmp_path, memory_v2):
    from app.domains.conversations.services import conversation_service
    from app.domains.customers.services.user_profile_service import get_profile_bundle, _get_session

    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("MEMORY_V2_WRITE_ENABLED", str(memory_v2).lower())
    get_settings.cache_clear()
    occurred_at = datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)
    for index, (owner, status, content) in enumerate((
        ("owner-1", "unconfirmed", "刚才分享的是兰花叶艺，你看过吗？"),
        ("owner-1", "queued", "尚未发送的触达"),
        ("owner-1", "failed", "发送失败的触达"),
        ("owner-2", "accepted", "其他客服的触达"),
    )):
        await conversation_service.ensure_outbound_conversation_message(
            channel="wechat", user_id="customer-1", session_id="default",
            sender_type="system", content=content, route="service_material_touch",
            trace_id=f"service_material_touch:{index}:copy", delivery_status=status,
            metadata={"owner_wc_id": owner, "source_batch_key": f"service_material_touch:{index}",
                      "delivery_timestamps": {"accepted_at": occurred_at.isoformat()}},
        )
    current = await conversation_service.ensure_outbound_conversation_message(
        channel="wechat", user_id="customer-1", session_id="owner-1",
        sender_type="system", content="[图片]", message_type="image", route="service_material_touch",
        trace_id="service_material_touch:10:media",
        metadata={"owner_wc_id": "owner-1", "source_batch_key": "service_material_touch:10",
                  "message_role": "media", "material_title": "兰花叶艺知识分享"},
    )
    conversation_service.update_outbound_message_delivery(current["id"], status="accepted", sent_at=occurred_at + timedelta(seconds=1))
    conversation_service.update_outbound_message_delivery(current["id"], status="confirmed", sent_at=occurred_at + timedelta(seconds=1))
    for _ in range(2):
        bundle = await get_profile_bundle("customer-1", conversation_char_budget=12000,
                                          conversation_session_id="owner-1")
        assert [row["content"] for row in bundle["recent_memories"]] == [
            "刚才分享的是兰花叶艺，你看过吗？", "[图片] 兰花叶艺知识分享",
        ]
    with _get_session() as session:
        memories = list(session.scalars(service.select(ConversationMemoryModel).where(
            ConversationMemoryModel.session_id == "owner-1",
        )))
    assert len(memories) == 2
    assert sorted(row.created_at.replace(tzinfo=timezone.utc) for row in memories) == [
        occurred_at, occurred_at + timedelta(seconds=1),
    ]
    if memory_v2:
        with _get_session() as session:
            events = list(session.scalars(service.select(MemoryEventModel).where(
                MemoryEventModel.source_id.like("scheduled_touch:%"),
            )))
        assert len(events) == 2
        assert {row.session_id for row in events} == {"owner-1"}


@pytest.mark.parametrize("sender,minutes,status,owner,cancelled", (
    ("customer", 29, None, "owner-1", True),
    ("customer", 30, None, "owner-1", True),
    ("customer", 31, None, "owner-1", False),
    ("human", 5, "sent", "owner-1", True),
    ("ai", 5, "accepted", "owner-1", True),
    ("system", 5, "accepted", "owner-1", False),
    ("human", 5, "failed", "owner-1", False),
    ("customer", 5, None, "owner-2", False),
))
@pytest.mark.asyncio
async def test_recent_chat_cancels_only_current_touch(monkeypatch, tmp_path, sender, minutes, status, owner, cancelled):
    from app.domains.conversations.services import conversation_service
    from app.integrations.eyun.services import eyun_material_service, message_risk_control_service

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utcnow", lambda: now)
    assert service.ensure_service_material_touch_tasks(now=now - timedelta(minutes=1)) == 3
    message = await conversation_service.ensure_outbound_conversation_message(
        channel="wechat", user_id="customer-1", session_id=owner,
        sender_type=sender, content="正在聊天", delivery_status=status,
        metadata={"owner_wc_id": owner},
    )
    with service._chat_session() as session:
        session.get(ConversationMessageModel, message["id"]).created_at = now - timedelta(minutes=minutes)
        session.commit()
    monkeypatch.setattr(service, "select_scheduled_agent_media", lambda **_: {
        "format": "image", "url": "https://media.example.com/leaf.jpg", "copy_text": "叶艺分享",
    })
    queued = []
    async def materialize(**kwargs):
        return {"id": 99}
    async def enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued)}
    monkeypatch.setattr(eyun_material_service, "materialize_eyun_outbound_media", materialize)
    monkeypatch.setattr(message_risk_control_service, "enqueue_wechat_outbound", enqueue)
    assert await service.process_due_service_material_touches(now=now) == (0 if cancelled else 1)
    assert len(queued) == (0 if cancelled else 2)
    with service._database_session() as session:
        tasks = list(session.scalars(service.select(AgentWakeupModel).order_by(AgentWakeupModel.due_at)))
    assert tasks[0].status == ("cancelled" if cancelled else "queued")
    assert [row.status for row in tasks[1:]] == ["pending", "pending"]
    assert service.ensure_service_material_touch_tasks(now=now) == 0


@pytest.mark.asyncio
async def test_chat_after_copy_cancels_queued_media_before_send(monkeypatch, tmp_path):
    from app.domains.conversations.services import conversation_service
    from app.integrations.eyun.services import message_risk_control_service as risk

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utcnow", lambda: now)
    service.ensure_service_material_touch_tasks(now=now - timedelta(minutes=1))
    with service._database_session() as session:
        task = session.scalar(service.select(AgentWakeupModel).order_by(AgentWakeupModel.due_at))
        task.status = "queued"
        task_id = task.id
        session.commit()
    batch = f"service_material_touch:{task_id}"
    copy = await risk.enqueue_wechat_outbound(w_id="wid-1", wc_id="customer-1", content="叶艺分享",
        source_batch_key=batch, due_at=now, session_id="owner-1", sender_type="system")
    media = await risk.enqueue_wechat_outbound(w_id="wid-1", wc_id="customer-1", content="图片素材",
        source_batch_key=batch, due_at=now, session_id="owner-1", sender_type="system", depends_on_outbound_id=copy["id"])
    with service._chat_session() as session:
        session.get(EyunOutboundMessageModel, copy["id"]).status = "accepted"
        session.commit()
    customer = await conversation_service.ensure_outbound_conversation_message(
        channel="wechat", user_id="customer-1", session_id="owner-1", sender_type="customer",
        content="这些有什么不同？", metadata={"owner_wc_id": "owner-1"},
    )
    with service._chat_session() as session:
        session.get(ConversationMessageModel, customer["id"]).created_at = now
        session.commit()
    assert not service.validate_service_material_touch_before_send(batch)
    with service._chat_session() as session:
        assert session.get(EyunOutboundMessageModel, copy["id"]).status == "accepted"
        assert session.get(EyunOutboundMessageModel, media["id"]).status == "cancelled"
        assert session.get(ConversationMessageModel, media["conversation_message_id"]).delivery_status == "cancelled"
    assert not service.get_agent_relationship_state("customer-1")["touch_failed_at"]


@pytest.mark.parametrize("expired_media", (False, True))
@pytest.mark.asyncio
async def test_first_send_failure_stops_future_touches_but_keeps_chat_and_other_customer(monkeypatch, tmp_path, expired_media):
    from app.integrations.eyun.services import eyun_callback_service, eyun_material_service, message_risk_control_service as risk

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    _insert_contact("customer-2")
    _tag_service_customer("customer-2")
    now = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utcnow", lambda: now)
    monkeypatch.setattr(risk, "utcnow", lambda: now)
    async def ready_material(*args, **kwargs):
        return {"media_type": "image", "raw_xml": "<msg>image</msg>"}
    monkeypatch.setattr(eyun_material_service, "get_ready_material", lambda *_: {"id": 99})
    monkeypatch.setattr(eyun_material_service, "ensure_material_sendable", ready_material)
    service.ensure_service_material_touch_tasks(now=now - timedelta(minutes=1))
    with service._database_session() as session:
        tasks = list(session.scalars(service.select(AgentWakeupModel).where(
            AgentWakeupModel.customer_id == "customer-1",
        ).order_by(AgentWakeupModel.due_at)))
        tasks[0].status = tasks[1].status = "queued"
        task_ids = [row.id for row in tasks]
        session.commit()
    queued = []
    for task_id in task_ids[:2]:
        queued.append(await risk.enqueue_wechat_outbound(
            w_id="wid-1", wc_id="customer-1", content="定时触达",
            source_batch_key=f"service_material_touch:{task_id}", due_at=now,
            session_id="owner-1", sender_type="system",
            material_id=99 if expired_media else None,
        ))
    chat = await risk.enqueue_wechat_outbound(w_id="wid-1", wc_id="customer-1", content="正常客户回复",
        source_batch_key="customer-chat", session_id="owner-1", due_at=now + timedelta(minutes=5))
    other = await risk.enqueue_wechat_outbound(w_id="wid-1", wc_id="customer-2", content="另一位客户的消息",
        source_batch_key="other-chat", session_id="owner-1", due_at=now + timedelta(minutes=5))
    calls = []
    async def fail_send(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("素材过期" if expired_media else "Eyun sendText failed: code=1001")
    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", fail_send)
    monkeypatch.setattr(eyun_callback_service, "send_eyun_received_media", fail_send)
    assert await risk.process_due_eyun_outbound_messages() == 1
    assert len(calls) == 1
    with service._database_session() as session:
        assert [session.get(AgentWakeupModel, task_id).status for task_id in task_ids] == ["failed", "cancelled", "cancelled"]
    with service._chat_session() as session:
        assert session.get(EyunOutboundMessageModel, queued[0]["id"]).status == "failed"
        assert session.get(EyunOutboundMessageModel, queued[1]["id"]).status == "cancelled"
        for row in (chat, other):
            outbound = session.get(EyunOutboundMessageModel, row["id"])
            assert outbound.status == "queued"
            assert risk._validate_outbound_before_send(session, outbound)
    service._database_factories.clear()
    assert service.get_agent_relationship_state("customer-1")["touch_failed_at"]
    assert service.ensure_service_material_touch_tasks(now=now + timedelta(days=1)) == 3
    with service._database_session() as session:
        new_tasks = list(session.scalars(service.select(AgentWakeupModel).where(
            AgentWakeupModel.local_date == "2026-08-06",
        )))
    assert {row.customer_id for row in new_tasks} == {"customer-2"}
    assert not service.retry_service_material_touch_task_now(f"service_material_touch:{task_ids[0]}")


def test_touch_failure_column_is_added_to_existing_database(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    with service._database_session() as session:
        session.execute(service.text("ALTER TABLE agent_customer_states DROP COLUMN touch_failed_at"))
        session.commit()
    service._database_factories.clear()
    assert service.get_agent_relationship_state("customer-1")["touch_failed_at"] is None


@pytest.mark.parametrize("event,stopped", (
    ("send_failed", True),
    ("material_prepare_failed", False),
    ("delivery_result_unknown", False),
    ("confirmation_timeout_unconfirmed", False),
))
def test_recorded_send_failure_stops_existing_and_future_tasks(monkeypatch, tmp_path, event, stopped):
    from app.integrations.eyun.services import message_risk_control_service as risk

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)
    service.ensure_service_material_touch_tasks(now=now - timedelta(minutes=1))
    with service._database_session() as session:
        task = session.scalar(service.select(AgentWakeupModel).order_by(AgentWakeupModel.due_at))
        task_id = task.id
    with service._chat_session() as session:
        outbound = EyunOutboundMessageModel(
            w_id="wid-1", wc_id="customer-1", content="触达",
            source_batch_key=f"service_material_touch:{task_id}", status="failed",
            due_at=now, attempts=1, created_at=now, updated_at=now,
        )
        session.add(outbound)
        session.flush()
        risk._record_outbound_delivery_event(session, outbound, event=event, error="历史记录")
        session.commit()
    assert service.ensure_service_material_touch_tasks(now=now + timedelta(days=1)) == (0 if stopped else 3)
    assert bool(service.get_agent_relationship_state("customer-1")["touch_failed_at"]) == stopped
    with service._database_session() as session:
        tasks = list(session.scalars(service.select(AgentWakeupModel).where(
            AgentWakeupModel.local_date == "2026-08-05",
        )))
    assert {row.status for row in tasks} == ({"cancelled"} if stopped else {"pending"})


@pytest.mark.asyncio
async def test_chat_during_material_preparation_cancels_bundle_before_queue(monkeypatch, tmp_path):
    from app.domains.conversations.services import conversation_service
    from app.integrations.eyun.services import eyun_material_service, message_risk_control_service as risk

    _configure(monkeypatch, tmp_path)
    _insert_contact()
    _tag_service_customer()
    now = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_utcnow", lambda: now)
    monkeypatch.setattr(conversation_service, "_now", lambda: now)
    service.ensure_service_material_touch_tasks(now=now - timedelta(minutes=1))
    monkeypatch.setattr(service, "select_scheduled_agent_media", lambda **_: {
        "format": "image", "url": "https://media.example.com/leaf.jpg", "copy_text": "叶艺分享",
    })
    async def materialize(**kwargs):
        await conversation_service.ensure_outbound_conversation_message(
            channel="wechat", user_id="customer-1", session_id="owner-1",
            sender_type="customer", content="现在想咨询养护", metadata={"owner_wc_id": "owner-1"},
        )
        return {"id": 99}
    async def unexpected_enqueue(**kwargs):
        raise AssertionError("聊天后不得继续入队")
    monkeypatch.setattr(eyun_material_service, "materialize_eyun_outbound_media", materialize)
    monkeypatch.setattr(risk, "enqueue_wechat_outbound", unexpected_enqueue)
    assert await service.process_due_service_material_touches(now=now) == 0
    with service._chat_session() as session:
        messages = list(session.scalars(service.select(ConversationMessageModel).where(
            ConversationMessageModel.sender_type == "system",
        )))
    assert len(messages) == 2
    assert {row.delivery_status for row in messages} == {"cancelled"}
