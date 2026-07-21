from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import (
    MemoryFeedbackModel,
    MemoryProcedureCandidateModel,
    MemoryProcedureEvidenceModel,
)
from app.schemas.memory import MemoryEventCreate, MemoryOperationCandidate
from app.schemas.prompt import PromptBuildInput
from app.services import memory_repository
from app.services.memory_consolidation_service import apply_memory_candidate
from app.services.memory_event_service import append_memory_event
from app.services.memory_identity_service import resolve_or_create_subject
from app.services.memory_lifecycle_service import correct_memory_fact
from app.services.memory_procedure_service import (
    get_memory_operations_status,
    list_memory_procedure_candidates,
    review_memory_procedure_candidate,
    submit_memory_procedure_candidate,
)
from app.services.prompt_builder import build_prompt


@pytest.fixture
def memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2-procedure.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    memory_repository.reset_memory_repository_cache()
    try:
        yield
    finally:
        memory_repository.reset_memory_repository_cache()
        get_settings.cache_clear()


def _feedback(user_id: str, index: int) -> int:
    subject = resolve_or_create_subject(
        tenant_id="tenant_alpha",
        channel="wechat",
        owner_external_id="owner_1",
        external_user_id=user_id,
        identity_source="test",
        verified=True,
    )
    occurred_at = datetime(2026, 1, index, tzinfo=timezone.utc)
    event = append_memory_event(
        MemoryEventCreate(
            event_uid=f"procedure:event:{index}",
            tenant_id="tenant_alpha",
            subject_id=subject.id,
            event_type="customer_message",
            actor_type="customer",
            content={"text": "预算三千"},
            source_type="conversation_message",
            source_id=f"procedure:event:{index}",
            occurred_at=occurred_at,
        )
    ).event
    fact = apply_memory_candidate(
        tenant_id="tenant_alpha",
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="semantic_fact",
            fact_key="purchase.budget",
            fact_value={
                "amount": 3000,
                "currency": "CNY",
                "scope": "current_purchase",
            },
            evidence_event_ids=[event.id],
            source_type="customer_explicit",
            valid_from=occurred_at,
            confidence=0.99,
            reason="procedure_test",
        ),
    )
    result = correct_memory_fact(
        tenant_id="tenant_alpha",
        subject_id=subject.id,
        target_fact_id=fact.record_id,
        fact_value={
            "amount": 5000,
            "currency": "CNY",
            "scope": "current_purchase",
        },
        valid_from=occurred_at + timedelta(hours=1),
        request_id=f"procedure:correction:{index}",
        reason_code="repeated_budget_clarification",
        operator_id="admin_1",
    )
    with memory_repository.get_memory_session() as session:
        return session.scalar(
            select(MemoryFeedbackModel.id).where(
                MemoryFeedbackModel.request_id == result["request_id"]
            )
        )


@pytest.mark.asyncio
async def test_procedure_candidate_requires_review_and_never_mutates_prompt(memory_db):
    feedback_ids = [_feedback("user_1", 1), _feedback("user_2", 2)]
    proposal = "在预算变化时先复述最新金额，再继续推荐。"

    pending = submit_memory_procedure_candidate(
        tenant_id="tenant_alpha",
        request_id="procedure:budget-confirmation:v1",
        title="预算变更确认步骤",
        proposal=proposal,
        rationale="两条独立人工纠错显示需要明确确认最新预算。",
        feedback_ids=feedback_ids,
        proposed_by="analyst_1",
    )
    retry = submit_memory_procedure_candidate(
        tenant_id="tenant_alpha",
        request_id="procedure:budget-confirmation:v1",
        title="预算变更确认步骤",
        proposal=proposal,
        rationale="两条独立人工纠错显示需要明确确认最新预算。",
        feedback_ids=list(reversed(feedback_ids)),
        proposed_by="analyst_1",
    )
    assert retry["id"] == pending["id"]
    assert pending["status"] == "pending"

    approved = review_memory_procedure_candidate(
        tenant_id="tenant_alpha",
        candidate_id=pending["id"],
        decision="approved",
        reviewed_by="reviewer_1",
        review_reason="仅批准进入人工实施评审",
    )
    prompt = await build_prompt(PromptBuildInput(user_message="我的预算改了"))

    assert approved["status"] == "approved"
    assert proposal not in prompt
    assert list_memory_procedure_candidates(
        tenant_id="tenant_alpha", status="approved"
    )[0]["id"] == approved["id"]
    with memory_repository.get_memory_session() as session:
        assert session.scalar(
            select(func.count(MemoryProcedureCandidateModel.id))
        ) == 1
        assert session.scalar(
            select(func.count(MemoryProcedureEvidenceModel.id))
        ) == 2


def test_procedure_candidate_rejects_identifiers_and_reports_content_free_status(
    memory_db,
):
    feedback_ids = [_feedback("user_1", 1), _feedback("user_2", 2)]
    with pytest.raises(ValueError, match="identifiers"):
        submit_memory_procedure_candidate(
            tenant_id="tenant_alpha",
            request_id="procedure:unsafe:v1",
            title="联系客户",
            proposal="联系 13800138000 复核",
            rationale="测试",
            feedback_ids=feedback_ids,
            proposed_by="analyst_1",
        )

    status = get_memory_operations_status("tenant_alpha")
    assert status["procedure_candidates"] == {}
    assert "proposal" not in status
    assert "rationale" not in status
