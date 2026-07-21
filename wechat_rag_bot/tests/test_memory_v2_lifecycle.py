from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import (
    ConversationMemoryModel,
    MemoryEpisodeModel,
    MemoryEventModel,
    MemoryFactModel,
    MemoryFeedbackModel,
    MemoryIdentityModel,
    MemoryJobModel,
    MemoryPurgeAuditModel,
    MemoryShadowRunModel,
    MemorySubjectModel,
    UserProfileModel,
)
from app.schemas.memory import MemoryEventCreate, MemoryOperationCandidate
from app.services import memory_repository, user_profile_service
from app.services.embedding_service import embed_text
from app.services.memory_consolidation_service import apply_memory_candidate
from app.services.memory_event_service import append_memory_event
from app.services.memory_identity_service import resolve_or_create_subject
from app.services.memory_job_service import enqueue_memory_job
from app.services.memory_lifecycle_service import (
    correct_memory_fact,
    purge_memory_subject,
)
from app.services.memory_vector_service import (
    index_memory_episode,
    rebuild_memory_subject_index,
    reset_memory_vector_cache,
    search_memory_episodes,
)
from app.services.user_profile_service import append_conversation_memory


@pytest.fixture
def memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2-lifecycle.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("QDRANT_URL", "")
    monkeypatch.setenv("QDRANT_VECTOR_SIZE", "32")
    monkeypatch.setenv("MEMORY_V2_WRITE_ENABLED", "false")
    get_settings.cache_clear()
    memory_repository.reset_memory_repository_cache()
    user_profile_service._sessionmakers.clear()
    reset_memory_vector_cache()
    try:
        yield monkeypatch
    finally:
        reset_memory_vector_cache()
        user_profile_service._sessionmakers.clear()
        memory_repository.reset_memory_repository_cache()
        get_settings.cache_clear()


def _subject(user_id="user_1"):
    return resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="wechat",
        owner_external_id="owner_1",
        external_user_id=user_id,
        identity_source="test",
        verified=True,
    )


def _event(subject, uid, text, *, occurred_at=None, source_type="conversation_message"):
    return append_memory_event(
        MemoryEventCreate(
            event_uid=uid,
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            session_id="session_1",
            event_type="customer_message",
            actor_type="customer",
            content={"text": text},
            source_type=source_type,
            source_id=uid,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
    ).event


def _budget_fact(subject, event, amount):
    return apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="semantic_fact",
            fact_key="purchase.budget",
            fact_value={
                "amount": amount,
                "currency": "CNY",
                "scope": "current_purchase",
            },
            evidence_event_ids=[event.id],
            source_type="customer_explicit",
            valid_from=event.occurred_at,
            confidence=0.99,
            reason="test_budget",
        ),
    )


def _episode(subject, event, title):
    return apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="episode",
            evidence_event_ids=[event.id],
            source_type="customer_explicit",
            confidence=0.95,
            reason="test_episode",
            episode_type="product_consultation",
            title=title,
            summary=title,
            importance=0.7,
            started_at=event.occurred_at,
            ended_at=event.occurred_at,
        ),
    )


def test_admin_correction_versions_fact_and_is_idempotent(memory_db):
    subject = _subject()
    original_event = _event(
        subject,
        "event:budget:old",
        "预算三千",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    original = _budget_fact(subject, original_event, 3000)
    correction_time = datetime(2026, 2, 1, tzinfo=timezone.utc)

    first = correct_memory_fact(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        target_fact_id=original.record_id,
        fact_value={
            "amount": 5000,
            "currency": "CNY",
            "scope": "current_purchase",
        },
        valid_from=correction_time,
        request_id="correction:budget:1",
        reason_code="customer_requested_fix",
        operator_id="admin_1",
    )
    retry = correct_memory_fact(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        target_fact_id=original.record_id,
        fact_value={
            "amount": 5000,
            "currency": "CNY",
            "scope": "current_purchase",
        },
        valid_from=correction_time,
        request_id="correction:budget:1",
        reason_code="customer_requested_fix",
        operator_id="admin_1",
    )
    with pytest.raises(ValueError, match="another correction"):
        correct_memory_fact(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            target_fact_id=original.record_id,
            fact_value={
                "amount": 9000,
                "currency": "CNY",
                "scope": "current_purchase",
            },
            valid_from=correction_time,
            request_id="correction:budget:1",
            reason_code="customer_requested_fix",
            operator_id="admin_1",
        )

    assert retry == first
    with memory_repository.get_memory_session() as session:
        facts = list(
            session.scalars(
                select(MemoryFactModel).order_by(MemoryFactModel.version.asc())
            )
        )
        correction_event = session.get(MemoryEventModel, first["correction_event_id"])
        assert [fact.status for fact in facts] == ["superseded", "active"]
        assert facts[1].version == 2
        assert facts[1].source_type == "manual_customer_correction"
        assert correction_event.event_type == "manual_correction"
        assert correction_event.actor_type == "human_agent"
        assert session.scalar(select(func.count(MemoryFeedbackModel.id))) == 1


def test_manual_correction_cannot_assert_verified_purchase_status(memory_db):
    subject = _subject()
    now = datetime.now(timezone.utc)
    business_event = append_memory_event(
        MemoryEventCreate(
            event_uid="business:paid:1",
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            event_type="tool_observation",
            actor_type="business_system",
            content={"order_id": "order_1", "status": "paid"},
            source_type="verified_business_system",
            source_id="business:paid:1",
            occurred_at=now,
        )
    ).event
    fact = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="semantic_fact",
            fact_key="purchase.status",
            fact_value={"order_id": "order_1", "status": "paid"},
            evidence_event_ids=[business_event.id],
            source_type="verified_business_system",
            valid_from=now,
            confidence=1.0,
            reason="verified_business",
        ),
    )

    with pytest.raises(ValueError, match="business source"):
        correct_memory_fact(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            target_fact_id=fact.record_id,
            fact_value={"order_id": "order_1", "status": "refunded"},
            valid_from=now + timedelta(minutes=1),
            request_id="correction:purchase:1",
            reason_code="manual_override",
            operator_id="admin_1",
        )


@pytest.mark.asyncio
async def test_subject_vector_index_rebuilds_from_sql(memory_db):
    subject = _subject()
    event = _event(subject, "event:rebuild", "历史咨询")
    episode = _episode(subject, event, "rebuild consultation")
    await index_memory_episode(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        episode_id=episode.record_id,
    )
    reset_memory_vector_cache()

    rebuilt = await rebuild_memory_subject_index(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
    )
    hits = await search_memory_episodes(
        await embed_text("rebuild consultation"),
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        top_k=5,
    )

    assert rebuilt == 1
    assert [hit["episode_id"] for hit in hits] == [episode.record_id]


@pytest.mark.asyncio
async def test_subject_purge_removes_sql_vector_and_legacy_residue(memory_db):
    alpha = _subject("user_alpha")
    beta = _subject("user_beta")
    alpha_event = _event(alpha, "event:alpha", "预算三千")
    beta_event = _event(beta, "event:beta", "预算五千")
    alpha_fact = _budget_fact(alpha, alpha_event, 3000)
    _budget_fact(beta, beta_event, 5000)
    alpha_episode = _episode(alpha, alpha_event, "alpha consultation")
    beta_episode = _episode(beta, beta_event, "beta consultation")
    await index_memory_episode(
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        episode_id=alpha_episode.record_id,
    )
    await index_memory_episode(
        tenant_id=beta.tenant_id,
        subject_id=beta.id,
        episode_id=beta_episode.record_id,
    )
    enqueue_memory_job(
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        trigger_event_id=alpha_event.id,
    )
    correct_memory_fact(
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        target_fact_id=alpha_fact.record_id,
        fact_value={
            "amount": 3500,
            "currency": "CNY",
            "scope": "current_purchase",
        },
        valid_from=alpha_event.occurred_at + timedelta(seconds=1),
        request_id="correction:alpha",
        reason_code="customer_requested_fix",
        operator_id="admin_1",
    )
    await append_conversation_memory(
        user_id="user_alpha",
        tenant_id="tenant_alpha",
        session_id="session_1",
        role="user",
        content="legacy alpha",
    )
    await append_conversation_memory(
        user_id="user_beta",
        tenant_id="tenant_alpha",
        session_id="session_1",
        role="user",
        content="legacy beta",
    )
    with memory_repository.get_memory_session() as session:
        session.add(
            MemoryShadowRunModel(
                tenant_id="tenant_alpha",
                subject_id=alpha.id,
                trace_id="trace_alpha",
                query_hash="a" * 64,
                status="shadow",
                injected=False,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    result = await purge_memory_subject(
        tenant_id="tenant_alpha",
        subject_id=alpha.id,
        confirm_subject_id=alpha.id,
        request_id="purge:alpha:1",
        requested_by="admin_1",
    )
    retry = await purge_memory_subject(
        tenant_id="tenant_alpha",
        subject_id=alpha.id,
        confirm_subject_id=alpha.id,
        request_id="purge:alpha:1",
        requested_by="admin_1",
    )

    assert retry == result
    assert result["status"] == "completed"
    assert result["subject_id_hash"] != alpha.id
    assert result["vector_points_deleted"] == 1
    with memory_repository.get_memory_session() as session:
        for model in (
            MemorySubjectModel,
            MemoryIdentityModel,
            MemoryEventModel,
            MemoryFactModel,
            MemoryEpisodeModel,
            MemoryJobModel,
            MemoryFeedbackModel,
            MemoryShadowRunModel,
        ):
            assert session.scalar(
                select(func.count()).select_from(model).where(
                    getattr(model, "subject_id", MemorySubjectModel.id) == alpha.id
                )
            ) == 0
        assert session.get(MemorySubjectModel, beta.id) is not None
        assert session.scalar(select(func.count(MemoryPurgeAuditModel.id))) == 1
        assert session.scalar(
            select(func.count(ConversationMemoryModel.id)).where(
                ConversationMemoryModel.user_id == "user_alpha"
            )
        ) == 0
        assert session.get(UserProfileModel, "user_alpha") is None
        assert session.get(UserProfileModel, "user_beta") is not None

    alpha_hits = await search_memory_episodes(
        await embed_text("consultation"),
        tenant_id="tenant_alpha",
        subject_id=alpha.id,
        top_k=10,
    )
    beta_hits = await search_memory_episodes(
        await embed_text("consultation"),
        tenant_id="tenant_alpha",
        subject_id=beta.id,
        top_k=10,
    )
    assert alpha_hits == []
    assert [hit["episode_id"] for hit in beta_hits] == [beta_episode.record_id]


@pytest.mark.asyncio
async def test_purge_fails_closed_when_vector_deletion_fails(memory_db, monkeypatch):
    from app.services import memory_lifecycle_service

    subject = _subject()
    event = _event(subject, "event:retain", "保留")

    async def fail_vector_delete(**kwargs):
        raise RuntimeError("synthetic vector failure")

    monkeypatch.setattr(
        memory_lifecycle_service,
        "delete_memory_subject_points",
        fail_vector_delete,
    )
    with pytest.raises(RuntimeError, match="synthetic vector failure"):
        await purge_memory_subject(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            confirm_subject_id=subject.id,
            request_id="purge:failed:1",
            requested_by="admin_1",
        )

    with memory_repository.get_memory_session() as session:
        assert session.get(MemorySubjectModel, subject.id) is not None
        assert session.get(MemoryEventModel, event.id) is not None
        audit = session.scalar(select(MemoryPurgeAuditModel))
        assert audit.status == "failed"
        assert audit.error_code == "RuntimeError"
