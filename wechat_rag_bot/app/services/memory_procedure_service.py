from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.models import (
    MemoryFeedbackModel,
    MemoryJobModel,
    MemoryProcedureCandidateModel,
    MemoryProcedureEvidenceModel,
    MemoryRolloutGateModel,
    MemoryShadowRunModel,
)
from app.services.memory_repository import get_memory_session


_PII_PATTERN = re.compile(
    r"(?:\b1[3-9]\d{9}\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    return normalized


def submit_memory_procedure_candidate(
    *,
    tenant_id: str,
    request_id: str,
    title: str,
    proposal: str,
    rationale: str,
    feedback_ids: list[int],
    proposed_by: str,
) -> dict[str, Any]:
    request_id = _required(request_id, "request_id", 128)
    title = _required(title, "title", 256)
    proposal = _required(proposal, "proposal", 4000)
    rationale = _required(rationale, "rationale", 4000)
    proposed_by = _required(proposed_by, "proposed_by", 128)
    feedback_ids = list(dict.fromkeys(feedback_ids))
    if len(feedback_ids) < 2:
        raise ValueError("procedural candidate requires at least two feedback records")
    candidate_uid = hashlib.sha256(
        f"{tenant_id}\0{request_id}".encode("utf-8")
    ).hexdigest()
    with get_memory_session() as session:
        existing = session.scalar(
            select(MemoryProcedureCandidateModel).where(
                MemoryProcedureCandidateModel.tenant_id == tenant_id,
                MemoryProcedureCandidateModel.candidate_uid == candidate_uid,
            )
        )
        if existing is not None:
            existing_feedback_ids = list(
                session.scalars(
                    select(MemoryProcedureEvidenceModel.feedback_id).where(
                        MemoryProcedureEvidenceModel.candidate_id == existing.id
                    )
                )
            )
            if (
                existing.title != title
                or existing.proposal != proposal
                or existing.rationale != rationale
                or existing.proposed_by != proposed_by
                or set(existing_feedback_ids) != set(feedback_ids)
            ):
                raise ValueError("request_id already belongs to another candidate")
            return _candidate_result(existing, existing_feedback_ids)

        feedback = list(
            session.scalars(
                select(MemoryFeedbackModel).where(
                    MemoryFeedbackModel.id.in_(feedback_ids),
                    MemoryFeedbackModel.tenant_id == tenant_id,
                )
            )
        )
        if len(feedback) != len(feedback_ids):
            raise ValueError("feedback evidence is missing or outside tenant scope")
        forbidden_identifiers = {row.subject_id for row in feedback}
        combined = f"{title}\n{proposal}\n{rationale}"
        if _PII_PATTERN.search(combined) or any(
            identifier in combined for identifier in forbidden_identifiers
        ):
            raise ValueError("procedural candidate must not contain customer identifiers")
        candidate = MemoryProcedureCandidateModel(
            tenant_id=tenant_id,
            candidate_uid=candidate_uid,
            title=title,
            proposal=proposal,
            rationale=rationale,
            status="pending",
            version=1,
            proposed_by=proposed_by,
            created_at=_now(),
        )
        session.add(candidate)
        session.flush()
        session.add_all(
            [
                MemoryProcedureEvidenceModel(
                    candidate_id=candidate.id,
                    feedback_id=feedback_id,
                    created_at=_now(),
                )
                for feedback_id in feedback_ids
            ]
        )
        session.commit()
        session.refresh(candidate)
        return _candidate_result(candidate, feedback_ids)


def review_memory_procedure_candidate(
    *,
    tenant_id: str,
    candidate_id: int,
    decision: str,
    reviewed_by: str,
    review_reason: str,
) -> dict[str, Any]:
    decision = decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    reviewed_by = _required(reviewed_by, "reviewed_by", 128)
    review_reason = _required(review_reason, "review_reason", 4000)
    with get_memory_session() as session:
        candidate = session.scalar(
            select(MemoryProcedureCandidateModel).where(
                MemoryProcedureCandidateModel.id == candidate_id,
                MemoryProcedureCandidateModel.tenant_id == tenant_id,
            )
        )
        if candidate is None:
            raise ValueError("procedural candidate not found in tenant")
        if candidate.status != "pending":
            if (
                candidate.status == decision
                and candidate.reviewed_by == reviewed_by
                and candidate.review_reason == review_reason
            ):
                feedback_ids = list(
                    session.scalars(
                        select(MemoryProcedureEvidenceModel.feedback_id).where(
                            MemoryProcedureEvidenceModel.candidate_id == candidate.id
                        )
                    )
                )
                return _candidate_result(candidate, feedback_ids)
            raise ValueError("procedural candidate has already been reviewed")
        candidate.status = decision
        candidate.reviewed_by = reviewed_by
        candidate.review_reason = review_reason
        candidate.reviewed_at = _now()
        session.commit()
        session.refresh(candidate)
        feedback_ids = list(
            session.scalars(
                select(MemoryProcedureEvidenceModel.feedback_id).where(
                    MemoryProcedureEvidenceModel.candidate_id == candidate.id
                )
            )
        )
        return _candidate_result(candidate, feedback_ids)


def list_memory_procedure_candidates(
    *, tenant_id: str, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with get_memory_session() as session:
        query = select(MemoryProcedureCandidateModel).where(
            MemoryProcedureCandidateModel.tenant_id == tenant_id
        )
        if status is not None:
            if status not in {"pending", "approved", "rejected"}:
                raise ValueError("unsupported procedural candidate status")
            query = query.where(MemoryProcedureCandidateModel.status == status)
        candidates = list(
            session.scalars(
                query.order_by(
                    MemoryProcedureCandidateModel.created_at.desc(),
                    MemoryProcedureCandidateModel.id.desc(),
                ).limit(limit)
            )
        )
        if not candidates:
            return []
        evidence_rows = session.execute(
            select(
                MemoryProcedureEvidenceModel.candidate_id,
                MemoryProcedureEvidenceModel.feedback_id,
            ).where(
                MemoryProcedureEvidenceModel.candidate_id.in_(
                    [candidate.id for candidate in candidates]
                )
            )
        ).all()
        evidence_by_candidate: dict[int, list[int]] = {}
        for candidate_id, feedback_id in evidence_rows:
            evidence_by_candidate.setdefault(candidate_id, []).append(feedback_id)
        return [
            _candidate_result(candidate, evidence_by_candidate.get(candidate.id, []))
            for candidate in candidates
        ]


def get_memory_operations_status(tenant_id: str) -> dict[str, Any]:
    with get_memory_session() as session:
        job_counts = dict(
            session.execute(
                select(MemoryJobModel.status, func.count())
                .where(MemoryJobModel.tenant_id == tenant_id)
                .group_by(MemoryJobModel.status)
            ).all()
        )
        shadow_counts = dict(
            session.execute(
                select(MemoryShadowRunModel.status, func.count())
                .where(MemoryShadowRunModel.tenant_id == tenant_id)
                .group_by(MemoryShadowRunModel.status)
            ).all()
        )
        violations = session.execute(
            select(
                func.coalesce(func.sum(MemoryShadowRunModel.scope_violation), 0),
                func.coalesce(
                    func.sum(MemoryShadowRunModel.verified_business_violation), 0
                ),
            ).where(MemoryShadowRunModel.tenant_id == tenant_id)
        ).one()
        procedure_counts = dict(
            session.execute(
                select(MemoryProcedureCandidateModel.status, func.count())
                .where(MemoryProcedureCandidateModel.tenant_id == tenant_id)
                .group_by(MemoryProcedureCandidateModel.status)
            ).all()
        )
        latest_gate = session.scalar(
            select(MemoryRolloutGateModel)
            .where(MemoryRolloutGateModel.tenant_id == tenant_id)
            .order_by(
                MemoryRolloutGateModel.approved_at.desc(),
                MemoryRolloutGateModel.id.desc(),
            )
            .limit(1)
        )
    return {
        "tenant_id": tenant_id,
        "jobs": job_counts,
        "shadow_runs": shadow_counts,
        "scope_violations": int(violations[0]),
        "verified_business_violations": int(violations[1]),
        "procedure_candidates": procedure_counts,
        "latest_rollout_gate": (
            {
                "evaluation_version": latest_gate.evaluation_version,
                "status": latest_gate.status,
                "sample_count": latest_gate.sample_count,
                "approved_at": latest_gate.approved_at,
            }
            if latest_gate
            else None
        ),
    }


def _candidate_result(
    candidate: MemoryProcedureCandidateModel, feedback_ids: list[int]
) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "tenant_id": candidate.tenant_id,
        "title": candidate.title,
        "proposal": candidate.proposal,
        "rationale": candidate.rationale,
        "status": candidate.status,
        "version": candidate.version,
        "feedback_ids": sorted(feedback_ids),
        "proposed_by": candidate.proposed_by,
        "reviewed_by": candidate.reviewed_by,
        "review_reason": candidate.review_reason,
        "created_at": candidate.created_at,
        "reviewed_at": candidate.reviewed_at,
    }
