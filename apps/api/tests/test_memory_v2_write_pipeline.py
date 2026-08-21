import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.infrastructure.database.models import (
    MemoryEpisodeEventModel,
    MemoryEpisodeModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemoryJobModel,
)
from app.domains.customers.schemas.memory import MemoryEventCreate, MemoryOperationCandidate
from app.services import memory_repository
from app.domains.customers.services.memory_consolidation_service import apply_memory_candidate
from app.domains.customers.services.memory_event_service import append_memory_event, list_memory_events
from app.domains.customers.services.memory_extraction_service import extract_memory_candidates
from app.domains.customers.services.memory_identity_service import resolve_or_create_subject
from app.domains.customers.services.memory_job_service import (
    MemoryJobLeaseError,
    claim_memory_job,
    complete_memory_job,
    enqueue_memory_job,
    fail_memory_job,
    get_memory_job,
)
from app.domains.customers.services.memory_validation_service import MemoryValidationError
from app.domains.customers.workers.memory_worker import process_memory_job


@pytest.fixture
def memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2-write.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    memory_repository.reset_memory_repository_cache()
    try:
        yield db_path
    finally:
        memory_repository.reset_memory_repository_cache()
        get_settings.cache_clear()


def _subject(tenant_id="tenant_alpha", external_user_id="user_1"):
    return resolve_or_create_subject(
        tenant_id=tenant_id,
        channel="wechat",
        owner_external_id="owner_1",
        external_user_id=external_user_id,
        identity_source="eyun_contact",
        verified=True,
    )


def _append(
    subject,
    *,
    uid,
    text="",
    event_type="customer_message",
    actor_type="customer",
    source_type="conversation_message",
    content=None,
    occurred_at=None,
):
    occurred_at = occurred_at or datetime.now(timezone.utc)
    return append_memory_event(
        MemoryEventCreate(
            event_uid=uid,
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            session_id="session_1",
            event_type=event_type,
            actor_type=actor_type,
            content=content or {"text": text},
            source_type=source_type,
            source_id=uid,
            occurred_at=occurred_at,
        )
    ).event


def _fact_candidate(event, value, **updates):
    payload = {
        "operation": "ADD",
        "memory_kind": "semantic_fact",
        "fact_key": "purchase.budget",
        "fact_value": {
            "amount": value,
            "currency": "CNY",
            "scope": "current_purchase",
        },
        "evidence_event_ids": [event.id],
        "source_type": "customer_explicit",
        "valid_from": event.occurred_at,
        "confidence": 0.99,
        "reason": "test_explicit_budget",
    }
    payload.update(updates)
    return MemoryOperationCandidate.model_validate(payload)


def test_memory_job_is_deduplicated_and_requires_lease_owner(memory_db):
    subject = _subject()
    event = _append(subject, uid="message:job:1", text="预算三千元")

    first = enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.id,
    )
    repeated = enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.id,
    )
    claimed = claim_memory_job(worker_id="worker_a", lease_seconds=60)

    assert repeated.id == first.id
    assert claimed.id == first.id
    assert claimed.attempts == 1
    with pytest.raises(MemoryJobLeaseError):
        complete_memory_job(job_id=claimed.id, worker_id="worker_b")
    completed = complete_memory_job(job_id=claimed.id, worker_id="worker_a")
    assert completed.status == "completed"
    assert claim_memory_job(worker_id="worker_a", lease_seconds=60) is None


def test_expired_job_lease_is_reclaimable_and_failures_become_dead(memory_db):
    subject = _subject()
    event = _append(subject, uid="message:job:2", text="预算三千元")
    start = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.id,
        available_at=start,
    )

    first = claim_memory_job(worker_id="worker_a", lease_seconds=60, now=start)
    reclaimed = claim_memory_job(
        worker_id="worker_b", lease_seconds=60, now=start + timedelta(seconds=61)
    )
    assert reclaimed.id == first.id
    assert reclaimed.attempts == 2

    dead = fail_memory_job(
        job_id=reclaimed.id,
        worker_id="worker_b",
        error="synthetic failure",
        max_attempts=2,
        retry_base_seconds=1,
        now=start + timedelta(seconds=62),
    )
    assert dead.status == "dead"
    assert dead.last_error == "synthetic failure"


def test_failed_job_waits_for_retry_schedule(memory_db):
    subject = _subject()
    event = _append(subject, uid="message:job:retry", text="预算三千元")
    start = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.id,
        available_at=start,
    )
    claimed = claim_memory_job(worker_id="worker_a", lease_seconds=60, now=start)
    retrying = fail_memory_job(
        job_id=claimed.id,
        worker_id="worker_a",
        error="temporary",
        max_attempts=3,
        retry_base_seconds=10,
        now=start,
    )

    assert retrying.status == "retry"
    assert claim_memory_job(
        worker_id="worker_b", lease_seconds=60, now=start + timedelta(seconds=9)
    ) is None
    assert claim_memory_job(
        worker_id="worker_b", lease_seconds=60, now=start + timedelta(seconds=10)
    ).id == claimed.id


def test_fact_versions_reinforce_then_supersede_without_duplicates(memory_db):
    subject = _subject()
    first_event = _append(subject, uid="message:fact:1", text="预算三千元")
    first_candidate = _fact_candidate(first_event, 3000)

    first = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=first_candidate,
    )
    retry = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=first_candidate,
    )
    assert first.created is True
    assert retry.created is False

    second_event = _append(subject, uid="message:fact:2", text="预算还是三千元")
    reinforced = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(second_event, 3000),
    )
    assert reinforced.operation == "REINFORCE"

    third_event = _append(subject, uid="message:fact:3", text="预算提高到五千元")
    superseded = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(third_event, 5000, operation="SUPERSEDE"),
    )
    assert superseded.operation == "SUPERSEDE"
    stale_retry = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=first_candidate,
    )
    assert stale_retry.created is False
    late_old_event = _append(
        subject,
        uid="message:fact:late-old",
        text="之前预算两千元",
        occurred_at=first_event.occurred_at - timedelta(days=1),
    )
    late_old = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(late_old_event, 2000),
    )
    assert late_old.operation == "NOOP"
    assert late_old.created is False

    with memory_repository.get_memory_session() as session:
        facts = list(
            session.scalars(
                select(MemoryFactModel)
                .where(MemoryFactModel.subject_id == subject.id)
                .order_by(MemoryFactModel.version)
            )
        )
        assert [fact.version for fact in facts] == [1, 2, 3]
        assert len({fact.fact_uid for fact in facts}) == 1
        assert [fact.status for fact in facts] == [
            "superseded",
            "superseded",
            "active",
        ]
        assert json.loads(facts[-1].fact_value_json)["amount"] == 5000
        assert facts[-1].supersedes_fact_id == facts[-2].id
        evidence = list(
            session.scalars(
                select(MemoryFactEvidenceModel.event_id).where(
                    MemoryFactEvidenceModel.fact_id == facts[-1].id
                )
            )
        )
        assert evidence == [third_event.id]


def test_active_facts_always_have_evidence(memory_db):
    subject = _subject()
    event = _append(subject, uid="message:evidence:1", text="预算三千元")
    apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(event, 3000),
    )

    with memory_repository.get_memory_session() as session:
        active_ids = list(
            session.scalars(
                select(MemoryFactModel.id).where(
                    MemoryFactModel.status == "active"
                )
            )
        )
        grounded_ids = set(
            session.scalars(
                select(MemoryFactEvidenceModel.fact_id).where(
                    MemoryFactEvidenceModel.fact_id.in_(active_ids)
                )
            )
        )
    assert active_ids
    assert set(active_ids) == grounded_ids


def test_disputed_fact_can_be_resolved_with_new_evidence(memory_db):
    subject = _subject()
    first_event = _append(subject, uid="message:dispute:1", text="预算三千元")
    first = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(first_event, 3000),
    )
    conflict_event = _append(subject, uid="message:dispute:2", text="预算是五千元")
    disputed = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(
            conflict_event,
            5000,
            operation="DISPUTE",
            supersedes_fact_id=first.record_id,
        ),
    )
    retry = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(
            conflict_event,
            5000,
            operation="DISPUTE",
            supersedes_fact_id=first.record_id,
        ),
    )
    assert disputed.created is True
    assert retry.created is False

    correction_event = _append(
        subject,
        uid="correction:dispute:3",
        event_type="manual_correction",
        source_type="manual_correction",
        content={
            "fact_key": "purchase.budget",
            "fact_value": {
                "amount": 4000,
                "currency": "CNY",
                "scope": "current_purchase",
            },
        },
    )
    resolved = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=_fact_candidate(
            correction_event,
            4000,
            operation="RESOLVE",
            supersedes_fact_id=disputed.record_id,
            source_type="manual_customer_correction",
        ),
    )
    assert resolved.operation == "RESOLVE"

    with memory_repository.get_memory_session() as session:
        rows = list(
            session.scalars(
                select(MemoryFactModel).where(
                    MemoryFactModel.subject_id == subject.id
                )
            )
        )
        active = [row for row in rows if row.status == "active"]
        assert len(active) == 1
        assert json.loads(active[0].fact_value_json)["amount"] == 4000
        assert all(row.status != "disputed" for row in rows)


def test_assistant_text_cannot_create_customer_preference(memory_db):
    subject = _subject()
    event = _append(
        subject,
        uid="message:assistant:1",
        text="我建议你喜欢树皮植料。",
        event_type="assistant_message",
        actor_type="assistant",
    )
    candidate = MemoryOperationCandidate(
        operation="ADD",
        memory_kind="semantic_fact",
        fact_key="service.preference",
        fact_value={"topic": "potting_medium", "value": "bark"},
        evidence_event_ids=[event.id],
        source_type="customer_explicit",
        valid_from=event.occurred_at,
        confidence=0.99,
        reason="invalid_assistant_inference",
    )

    with pytest.raises(MemoryValidationError, match="assistant-only|customer evidence"):
        apply_memory_candidate(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            candidate=candidate,
        )


def test_purchase_status_requires_verified_business_event(memory_db):
    subject = _subject()
    customer_event = _append(
        subject, uid="message:payment:claim", text="我好像已经付款了"
    )
    claim = MemoryOperationCandidate(
        operation="ADD",
        memory_kind="semantic_fact",
        fact_key="purchase.status",
        fact_value={"order_id": "synthetic_order", "status": "paid"},
        evidence_event_ids=[customer_event.id],
        source_type="verified_business_system",
        valid_from=customer_event.occurred_at,
        confidence=1.0,
        reason="customer_payment_claim",
    )
    with pytest.raises(MemoryValidationError, match="verified business"):
        apply_memory_candidate(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            candidate=claim,
        )

    verified_event = _append(
        subject,
        uid="tool:payment:verified",
        event_type="tool_observation",
        actor_type="business_system",
        source_type="verified_business_system",
        content={"order_id": "synthetic_order", "status": "paid"},
    )
    verified = claim.model_copy(
        update={
            "evidence_event_ids": [verified_event.id],
            "valid_from": verified_event.occurred_at,
            "reason": "verified_tool_result",
        }
    )
    assert apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=verified,
    ).created


def test_episode_creation_is_evidence_linked_and_retry_safe(memory_db):
    subject = _subject()
    customer = _append(
        subject, uid="message:commitment:customer", text="处理后告诉我结果"
    )
    assistant = _append(
        subject,
        uid="message:commitment:assistant",
        text="我会处理后告诉您结果。",
        event_type="assistant_message",
        actor_type="assistant",
        occurred_at=customer.occurred_at + timedelta(seconds=1),
    )
    candidate = MemoryOperationCandidate(
        operation="ADD",
        memory_kind="episode",
        evidence_event_ids=[customer.id, assistant.id],
        source_type="assistant_commitment",
        confidence=0.98,
        reason="explicit_commitment",
        episode_type="commitment",
        title="客服承诺",
        summary="处理完成后在当前会话同步结果",
        importance=0.8,
        started_at=customer.occurred_at,
        ended_at=assistant.occurred_at,
    )

    first = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=candidate,
    )
    retry = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=candidate,
    )
    assert first.created is True
    assert retry.created is False
    with memory_repository.get_memory_session() as session:
        assert session.scalar(select(func.count(MemoryEpisodeModel.id))) == 1
        assert session.scalar(select(func.count(MemoryEpisodeEventModel.id))) == 2


@pytest.mark.asyncio
async def test_deterministic_extraction_uses_customer_and_commitment_evidence(memory_db):
    subject = _subject()
    customer = _append(
        subject, uid="message:extract:customer", text="这次预算提高到五千元"
    )
    assistant = _append(
        subject,
        uid="message:extract:assistant",
        text="我会处理后告诉您结果。",
        event_type="assistant_message",
        actor_type="assistant",
        occurred_at=customer.occurred_at + timedelta(seconds=1),
    )
    events = list_memory_events(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        session_id="session_1",
    )

    budget = await extract_memory_candidates(
        events=events, trigger_event_id=customer.id, use_llm=False
    )
    commitment = await extract_memory_candidates(
        events=events, trigger_event_id=assistant.id, use_llm=False
    )

    assert budget[0].fact_key == "purchase.budget"
    assert budget[0].fact_value["amount"] == 5000
    episode = next(item for item in commitment if item.memory_kind == "episode")
    assert episode.evidence_event_ids == [customer.id, assistant.id]


@pytest.mark.asyncio
async def test_llm_candidate_still_requires_runtime_evidence_validation(
    memory_db, monkeypatch
):
    from app.services import memory_extraction_service

    subject = _subject()
    customer = _append(
        subject, uid="message:extract:llm", text="介质方面我更喜欢树皮。"
    )
    events = list_memory_events(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        session_id="session_1",
    )

    async def fake_generate_json(prompt, purpose):
        assert purpose == "profile"
        assert "untrusted data" in prompt
        return {
            "operations": [
                {
                    "operation": "ADD",
                    "memory_kind": "semantic_fact",
                    "fact_key": "service.preference",
                    "fact_value": {"topic": "potting_medium", "value": "bark"},
                    "evidence_event_ids": [customer.id],
                    "source_type": "customer_explicit",
                    "confidence": 0.96,
                    "reason": "customer_explicit",
                }
            ]
        }

    monkeypatch.setattr(memory_extraction_service, "generate_json", fake_generate_json)
    candidates = await extract_memory_candidates(
        events=events, trigger_event_id=customer.id, use_llm=True
    )

    assert len(candidates) == 1
    assert apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=candidates[0],
    ).created


@pytest.mark.asyncio
async def test_worker_restart_after_fact_commit_is_idempotent(memory_db):
    subject = _subject()
    event = _append(
        subject, uid="message:worker:restart", text="这次预算三千元"
    )
    job = enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.id,
    )
    start = datetime.now(timezone.utc)
    first_claim = claim_memory_job(
        worker_id="worker_before_restart", lease_seconds=1, now=start
    )
    assert await process_memory_job(
        first_claim, use_llm=False, min_confidence=0.85
    ) == 1

    reclaimed = claim_memory_job(
        worker_id="worker_after_restart",
        lease_seconds=1,
        now=start + timedelta(seconds=2),
    )
    assert reclaimed.id == job.id
    assert await process_memory_job(
        reclaimed, use_llm=False, min_confidence=0.85
    ) == 0
    complete_memory_job(job_id=reclaimed.id, worker_id="worker_after_restart")

    with memory_repository.get_memory_session() as session:
        assert session.scalar(select(func.count(MemoryFactModel.id))) == 1
        assert session.scalar(select(func.count(MemoryFactEvidenceModel.id))) == 1
        assert session.scalar(select(func.count(MemoryJobModel.id))) == 1
    assert get_memory_job(job.id).status == "completed"


@pytest.mark.asyncio
async def test_worker_keeps_old_trigger_inside_long_session_context(memory_db):
    subject = _subject()
    started_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    trigger = _append(
        subject,
        uid="message:worker:old-trigger",
        text="这次预算三千元",
        occurred_at=started_at,
    )
    for index in range(1, 26):
        _append(
            subject,
            uid=f"message:worker:later:{index}",
            text=f"后续消息 {index}",
            occurred_at=started_at + timedelta(seconds=index),
        )
    job = enqueue_memory_job(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        trigger_event_id=trigger.id,
    )

    assert await process_memory_job(
        job, use_llm=False, min_confidence=0.85
    ) == 1


@pytest.mark.asyncio
async def test_lifespan_starts_memory_worker_only_when_write_flag_enabled(monkeypatch):
    import app.bootstrap.lifecycle as lifecycle_module

    started = []

    async def worker(stop_event, name):
        started.append(name)
        await stop_event.wait()

    @asynccontextmanager
    async def fake_mcp_manager():
        yield

    monkeypatch.setattr(
        lifecycle_module,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {"evaluation_mode": False, "memory_v2_write_enabled": True},
        )(),
    )
    monkeypatch.setattr(
        lifecycle_module, "eyun_risk_control_worker", lambda stop: worker(stop, "eyun")
    )
    monkeypatch.setattr(
        lifecycle_module,
        "eyun_login_monitor_worker",
        lambda stop: worker(stop, "eyun_login_monitor"),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "service_material_touch_worker",
        lambda stop: worker(stop, "service_material_touch"),
    )
    monkeypatch.setattr(
        lifecycle_module, "youzan_product_sync_worker", lambda stop: worker(stop, "youzan")
    )
    monkeypatch.setattr(
        lifecycle_module, "youzan_order_sync_worker", lambda stop: worker(stop, "youzan_order")
    )
    monkeypatch.setattr(
        lifecycle_module, "memory_worker", lambda stop: worker(stop, "memory")
    )
    monkeypatch.setattr(
        lifecycle_module, "run_sales_mcp_session_manager", fake_mcp_manager
    )
    app = type("App", (), {"state": type("State", (), {})()})()

    async with lifecycle_module.lifespan(app):
        await asyncio.sleep(0)
        assert set(started) == {
            "eyun",
            "eyun_login_monitor",
            "service_material_touch",
            "youzan",
            "youzan_order",
            "memory",
        }
