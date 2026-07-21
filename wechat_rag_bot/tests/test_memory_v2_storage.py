import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, select

from app.config import get_settings
from app.db.models import (
    MemoryEventModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemoryIdentityModel,
    MemorySubjectModel,
)
from app.schemas.memory import (
    FACT_SOURCE_POLICY,
    MemoryEventCreate,
    validate_fact_value,
)
from app.services import memory_repository
from app.services.memory_event_service import (
    MemoryEventConflictError,
    MemoryEventSubjectError,
    append_memory_event,
    get_memory_event,
)
from app.services.memory_identity_service import (
    MemoryIdentityConflictError,
    MemorySubjectNotFoundError,
    attach_identity,
    get_subject_for_identity,
    resolve_or_create_subject,
)
from app.services.memory_projection_service import build_legacy_profile_projection


@pytest.fixture
def memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    memory_repository.reset_memory_repository_cache()
    try:
        yield db_path
    finally:
        memory_repository.reset_memory_repository_cache()
        get_settings.cache_clear()


def _subject(*, tenant_id: str = "tenant_alpha", external_user_id: str = "user_1"):
    return resolve_or_create_subject(
        tenant_id=tenant_id,
        channel="wechat",
        external_user_id=external_user_id,
        owner_external_id="owner_1",
        identity_source="eyun_contact",
        verified=True,
    )


def _event(subject, *, event_uid: str = "conversation_message:1", content=None):
    return MemoryEventCreate(
        event_uid=event_uid,
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        session_id="session_1",
        event_type="customer_message",
        actor_type="customer",
        content=content or {"text": "这次预算三千元。"},
        source_type="conversation_message",
        source_id="1",
        trace_id="trace_1",
        occurred_at=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
        sensitivity="internal",
    )


def test_memory_schema_contains_core_wp1_tables_and_fact_contract(memory_db):
    _subject()
    inspector = inspect(create_engine(get_settings().database_url))

    assert {
        "memory_subjects",
        "memory_identities",
        "memory_events",
        "memory_facts",
        "memory_fact_evidence",
    }.issubset(inspector.get_table_names())
    fact_columns = {
        column["name"] for column in inspector.get_columns("memory_facts")
    }
    assert {
        "fact_uid",
        "tenant_id",
        "subject_id",
        "fact_key",
        "fact_value_json",
        "normalized_value",
        "source_type",
        "confidence",
        "valid_from",
        "valid_to",
        "recorded_at",
        "status",
        "supersedes_fact_id",
        "version",
    }.issubset(fact_columns)


def test_identity_resolution_is_stable_and_tenant_scoped(memory_db):
    first = _subject(tenant_id="tenant_alpha", external_user_id="same_user")
    repeated = _subject(tenant_id="tenant_alpha", external_user_id="same_user")
    other_tenant = _subject(tenant_id="tenant_beta", external_user_id="same_user")

    assert repeated.id == first.id
    assert other_tenant.id != first.id
    assert (
        get_subject_for_identity(
            tenant_id="tenant_beta",
            channel="wechat",
            owner_external_id="owner_1",
            external_user_id="same_user",
        ).id
        == other_tenant.id
    )

    with memory_repository.get_memory_session() as session:
        assert session.scalar(select(func.count(MemorySubjectModel.id))) == 2
        assert session.scalar(select(func.count(MemoryIdentityModel.id))) == 2


def test_owner_account_is_part_of_external_identity(memory_db):
    first = _subject(external_user_id="same_user")
    second = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="wechat",
        external_user_id="same_user",
        owner_external_id="owner_2",
        identity_source="eyun_contact",
    )

    assert first.id != second.id


def test_identity_verification_can_only_upgrade(memory_db):
    subject = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="api",
        external_user_id="verification_user",
        identity_source="unverified_api",
        verified=False,
    )
    repeated = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="api",
        external_user_id="verification_user",
        identity_source="verified_contact_provider",
        verified=True,
    )
    resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="api",
        external_user_id="verification_user",
        identity_source="later_unverified_api",
        verified=False,
    )

    assert repeated.id == subject.id
    with memory_repository.get_memory_session() as session:
        identity = session.scalar(
            select(MemoryIdentityModel).where(
                MemoryIdentityModel.subject_id == subject.id
            )
        )
        assert identity.verified is True
        assert identity.identity_source == "verified_contact_provider"


def test_attach_identity_rejects_cross_subject_rebinding(memory_db):
    first = _subject(external_user_id="first")
    second = _subject(external_user_id="second")
    attach_identity(
        tenant_id="tenant_alpha",
        subject_id=first.id,
        channel="api",
        external_user_id="shared_api_user",
        identity_source="api",
    )

    with pytest.raises(MemoryIdentityConflictError):
        attach_identity(
            tenant_id="tenant_alpha",
            subject_id=second.id,
            channel="api",
            external_user_id="shared_api_user",
            identity_source="api",
        )
    with pytest.raises(MemorySubjectNotFoundError):
        attach_identity(
            tenant_id="tenant_beta",
            subject_id=first.id,
            channel="api",
            external_user_id="another_user",
            identity_source="api",
        )


def test_event_append_is_idempotent_and_immutable(memory_db):
    subject = _subject()
    event = _event(subject)

    first = append_memory_event(event)
    repeated = append_memory_event(event)

    assert first.created is True
    assert repeated.created is False
    assert repeated.event.id == first.event.id
    assert repeated.event.content == {"text": "这次预算三千元。"}
    with memory_repository.get_memory_session() as session:
        assert session.scalar(select(func.count(MemoryEventModel.id))) == 1

    changed = event.model_copy(update={"content": {"text": "不同内容"}})
    with pytest.raises(MemoryEventConflictError):
        append_memory_event(changed)


def test_event_uid_is_globally_unique_without_cross_tenant_read(memory_db):
    tenant_a = _subject(tenant_id="tenant_alpha", external_user_id="user_a")
    tenant_b = _subject(tenant_id="tenant_beta", external_user_id="user_b")
    append_memory_event(_event(tenant_a, event_uid="provider:event:shared"))

    with pytest.raises(MemoryEventConflictError):
        append_memory_event(_event(tenant_b, event_uid="provider:event:shared"))
    assert (
        get_memory_event(
            tenant_id="tenant_beta",
            subject_id=tenant_b.id,
            event_uid="provider:event:shared",
        )
        is None
    )


def test_event_rejects_subject_from_another_tenant(memory_db):
    subject = _subject(tenant_id="tenant_alpha")
    event = _event(subject).model_copy(update={"tenant_id": "tenant_beta"})

    with pytest.raises(MemoryEventSubjectError):
        append_memory_event(event)


def test_fact_registry_is_typed_and_purchase_status_is_trusted_only(memory_db):
    assert validate_fact_value(
        "purchase.budget",
        {"amount": 3000, "currency": "CNY", "scope": "current_purchase"},
    ) == {"amount": 3000.0, "currency": "CNY", "scope": "current_purchase"}
    assert FACT_SOURCE_POLICY["purchase.status"] == {"verified_business_system"}
    with pytest.raises(ValueError, match="region requires"):
        validate_fact_value("location.region", {})
    with pytest.raises(ValueError, match="product interest requires"):
        validate_fact_value("purchase.product_interest", {})
    with pytest.raises(ValueError, match="unsupported fact key"):
        validate_fact_value("temporary.free_form", "must not persist")


def test_projection_rebuilds_legacy_shape_from_active_evidenced_facts(memory_db):
    subject = _subject(external_user_id="projection_user")
    event = append_memory_event(_event(subject)).event
    now = datetime.now(timezone.utc)
    with memory_repository.get_memory_session() as session:
        facts = [
            _fact(
                subject,
                key="identity.display_name",
                value="兰友乙",
                normalized="兰友乙",
                now=now,
            ),
            _fact(
                subject,
                key="communication.preferred_detail",
                value="concise",
                normalized="concise",
                now=now,
            ),
            _fact(
                subject,
                key="purchase.product_interest",
                value={"category": "兰花植料"},
                normalized="兰花植料",
                now=now,
            ),
            _fact(
                subject,
                key="service.pain_point",
                value={"topic": "root_rot", "detail": "换盆后容易烂根"},
                normalized="root_rot",
                now=now,
            ),
            _fact(
                subject,
                key="service.pain_point",
                value={"topic": "future", "detail": "未来事实"},
                normalized="future",
                now=now + timedelta(days=1),
            ),
        ]
        orphan_fact = _fact(
            subject,
            key="service.pain_point",
            value={"topic": "orphan", "detail": "没有证据的事实"},
            normalized="orphan",
            now=now,
        )
        facts.append(orphan_fact)
        session.add_all(facts)
        session.flush()
        session.add_all(
            [
                MemoryFactEvidenceModel(
                    fact_id=fact.id, event_id=event.id, created_at=now
                )
                for fact in facts
                if fact is not orphan_fact
            ]
        )
        session.commit()

    projection = build_legacy_profile_projection(
        tenant_id="tenant_alpha",
        subject_id=subject.id,
        channel="wechat",
        as_of=now,
    )

    assert projection.user_id == "projection_user"
    assert projection.basic_info == {"nickname": "兰友乙"}
    assert projection.product_interests == ["兰花植料"]
    assert projection.pain_points == ["换盆后容易烂根"]
    assert projection.preference_summary == "偏好简洁说明"
    with pytest.raises(ValueError, match="not found in tenant"):
        build_legacy_profile_projection(
            tenant_id="tenant_beta", subject_id=subject.id, as_of=now
        )


def _fact(subject, *, key, value, normalized, now):
    return MemoryFactModel(
        fact_uid=str(uuid4()),
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        fact_key=key,
        fact_value_json=json.dumps(value, ensure_ascii=False, sort_keys=True),
        normalized_value=normalized,
        source_type="customer_explicit",
        confidence=0.95,
        valid_from=now,
        recorded_at=now,
        status="active",
        version=1,
        created_by="test",
        created_at=now,
        updated_at=now,
    )
