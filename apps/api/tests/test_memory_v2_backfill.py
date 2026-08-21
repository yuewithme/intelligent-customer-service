import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    ConversationMemoryModel,
    EyunContactModel,
    MemoryEventModel,
    MemoryFactModel,
    MemoryIdentityModel,
    MemoryJobModel,
    MemorySubjectModel,
    UserProfileModel,
)
from app.domains.customers.services.memory_backfill_service import (
    _event_uid,
    run_legacy_memory_backfill,
)
from app.domains.customers.schemas.memory import MemoryEventCreate
from app.domains.customers.services.memory_event_service import append_memory_event
from app.domains.customers.services.memory_identity_service import (
    resolve_or_create_subject,
)
from app.domains.customers.services.memory_job_service import enqueue_memory_job
from app.domains.customers.services.memory_repository import (
    reset_memory_repository_cache,
)


@pytest.fixture
def backfill_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2-backfill.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    reset_memory_repository_cache()
    engine = create_engine(get_settings().database_url)
    Base.metadata.create_all(
        engine,
        tables=[
            UserProfileModel.__table__,
            ConversationMemoryModel.__table__,
            EyunContactModel.__table__,
        ],
    )
    now = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(
            UserProfileModel(
                user_id="customer_1",
                tenant_id="tenant_alpha",
                channel="eyun",
                current_stage="rapport",
                risk_level="normal",
                customer_tags_json="[]",
                product_interests_json=json.dumps(["春兰"], ensure_ascii=False),
                preference_summary="偏好简短沟通",
                pain_points_json=json.dumps(["兰花出现黄叶"], ensure_ascii=False),
                active_opportunity_json="{}",
                basic_info_json=json.dumps(
                    {
                        "nickname": "兰友",
                        "shipping_city": "南宁",
                        "mobile": "13800000000",
                        "shipping_address": "不应迁移的详细地址",
                    },
                    ensure_ascii=False,
                ),
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            EyunContactModel(
                tenant_id="tenant_alpha",
                owner_account_id="owner_account",
                wc_id="customer_1",
                current_w_id="owner_1",
                first_seen_at=now,
                last_seen_at=now,
                status="active",
                missing_count=0,
                discovery_source="polling",
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            [
                ConversationMemoryModel(
                    user_id="customer_1",
                    tenant_id="tenant_alpha",
                    session_id="session_1",
                    role="user",
                    content="我这盆兰花叶子发黄。",
                    trace_id="trace_1",
                    created_at=now,
                ),
                ConversationMemoryModel(
                    user_id="customer_1",
                    tenant_id="tenant_alpha",
                    session_id="session_1",
                    role="assistant",
                    content="我先帮您判断一下。",
                    trace_id="trace_1",
                    created_at=now,
                ),
            ]
        )
        session.commit()
    engine.dispose()
    subject = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="eyun",
        owner_external_id="owner_1",
        external_user_id="customer_1",
        identity_source="live_conversation",
    )
    current_event = append_memory_event(
        MemoryEventCreate(
            event_uid="conversation:already-live",
            tenant_id="tenant_alpha",
            subject_id=subject.id,
            session_id="session_1",
            event_type="customer_message",
            actor_type="customer",
            content={"text": "我这盆兰花叶子发黄。"},
            source_type="conversation_message",
            source_id="provider_message_1",
            trace_id="trace_1",
            occurred_at=now,
            sensitivity="internal",
        )
    )
    enqueue_memory_job(
        tenant_id="tenant_alpha",
        subject_id=subject.id,
        trigger_event_id=current_event.event.id,
    )
    try:
        yield db_path
    finally:
        reset_memory_repository_cache()
        get_settings.cache_clear()


def test_backfill_is_idempotent_and_excludes_sensitive_contact_fields(backfill_db):
    dry_run = run_legacy_memory_backfill(apply=False)
    assert dry_run["profiles_seen"] == 1
    assert dry_run["profiles_with_owner_identity"] == 1
    assert dry_run["profile_facts_planned"] == 5
    assert dry_run["conversation_events_planned"] == 2

    first = run_legacy_memory_backfill(apply=True)
    second = run_legacy_memory_backfill(apply=True)

    assert first["errors"] == 0
    assert first["subjects_ensured"] == 1
    assert first["profile_events_created"] == 1
    assert first["facts_created"] == 5
    assert first["conversation_events_created"] == 1
    assert first["conversation_events_existing"] == 1
    assert first["jobs_ensured"] == 2
    assert second["errors"] == 0
    assert second["profile_events_existing"] == 1
    assert second["facts_existing"] == 5
    assert second["conversation_events_existing"] == 2

    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MemorySubjectModel)) == 1
        identity = session.scalar(select(MemoryIdentityModel))
        assert identity.channel == "eyun"
        assert identity.owner_external_id == "owner_1"
        assert session.scalar(select(func.count()).select_from(MemoryFactModel)) == 5
        assert session.scalar(select(func.count()).select_from(MemoryEventModel)) == 3
        assert session.scalar(select(func.count()).select_from(MemoryJobModel)) == 2
        snapshot = session.scalar(
            select(MemoryEventModel).where(
                MemoryEventModel.source_type == "legacy_profile"
            )
        )
        assert "13800000000" not in snapshot.content_json
        assert "不应迁移的详细地址" not in snapshot.content_json
    engine.dispose()


def test_backfill_rehomes_legacy_events_after_owner_identity_changes(backfill_db):
    old_subject = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="eyun",
        owner_external_id="",
        external_user_id="customer_1",
        identity_source="legacy_ownerless_identity",
    )
    now = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    append_memory_event(
        MemoryEventCreate(
            event_uid=_event_uid(
                "legacy_profile", "tenant_alpha", "eyun", "customer_1"
            ),
            tenant_id="tenant_alpha",
            subject_id=old_subject.id,
            event_type="contact_snapshot",
            actor_type="system",
            content={"migration_version": "legacy_memory_v1", "facts": []},
            source_type="legacy_profile",
            source_id="legacy_user_profile:customer_1",
            occurred_at=now,
            sensitivity="internal",
        )
    )
    append_memory_event(
        MemoryEventCreate(
            event_uid=_event_uid("legacy_conversation", "tenant_alpha", "2"),
            tenant_id="tenant_alpha",
            subject_id=old_subject.id,
            session_id="session_1",
            event_type="assistant_message",
            actor_type="assistant",
            content={"text": "我先帮您判断一下。"},
            source_type="conversation_message",
            source_id="legacy_conversation_memory:2",
            trace_id="trace_1",
            occurred_at=now,
            sensitivity="internal",
        )
    )

    first = run_legacy_memory_backfill(apply=True)
    second = run_legacy_memory_backfill(apply=True)

    assert first["errors"] == 0
    assert first["profile_events_created"] == 1
    assert first["conversation_events_created"] == 1
    assert second["errors"] == 0
    assert second["profile_events_existing"] == 1
    assert second["conversation_events_existing"] == 2

    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        current_subject_id = session.scalar(
            select(MemoryIdentityModel.subject_id).where(
                MemoryIdentityModel.tenant_id == "tenant_alpha",
                MemoryIdentityModel.channel == "eyun",
                MemoryIdentityModel.owner_external_id == "owner_1",
                MemoryIdentityModel.external_user_id == "customer_1",
            )
        )
        current_events = session.scalar(
            select(func.count())
            .select_from(MemoryEventModel)
            .where(MemoryEventModel.subject_id == current_subject_id)
        )
        old_events = session.scalar(
            select(func.count())
            .select_from(MemoryEventModel)
            .where(MemoryEventModel.subject_id == old_subject.id)
        )
    engine.dispose()

    assert current_events == 3
    assert old_events == 2
