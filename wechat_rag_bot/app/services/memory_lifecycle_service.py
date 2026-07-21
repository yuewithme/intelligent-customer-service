from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.db.models import (
    ConversationMemoryModel,
    MemoryEpisodeEventModel,
    MemoryEpisodeModel,
    MemoryEventModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemoryFeedbackModel,
    MemoryIdentityModel,
    MemoryJobModel,
    MemoryPurgeAuditModel,
    MemoryShadowRunModel,
    MemorySubjectModel,
    ProfileEventModel,
    UserProfileModel,
)
from app.schemas.memory import MemoryEventCreate, MemoryOperationCandidate
from app.services.memory_consolidation_service import apply_memory_candidate
from app.services.memory_event_service import append_memory_event
from app.services.memory_repository import get_memory_session
from app.services.memory_vector_service import delete_memory_subject_points


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, field: str, *, max_length: int = 128) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    return normalized


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("valid_from must be timezone-aware")
    return value.astimezone(timezone.utc)


def correct_memory_fact(
    *,
    tenant_id: str,
    subject_id: str,
    target_fact_id: int,
    fact_value: Any,
    valid_from: datetime,
    request_id: str,
    reason_code: str,
    operator_id: str,
) -> dict[str, Any]:
    request_id = _required(request_id, "request_id")
    reason_code = _required(reason_code, "reason_code")
    operator_id = _required(operator_id, "operator_id")
    valid_from = _aware_utc(valid_from)
    with get_memory_session() as session:
        feedback = session.scalar(
            select(MemoryFeedbackModel).where(
                MemoryFeedbackModel.tenant_id == tenant_id,
                MemoryFeedbackModel.request_id == request_id,
            )
        )
        if feedback is not None:
            correction_event = session.get(
                MemoryEventModel, feedback.correction_event_id
            )
            expected_content = {
                "target_fact_id": target_fact_id,
                "fact_key": (
                    session.get(MemoryFactModel, target_fact_id).fact_key
                    if session.get(MemoryFactModel, target_fact_id) is not None
                    else None
                ),
                "fact_value": fact_value,
                "reason_code": reason_code,
            }
            actual_content = (
                json.loads(correction_event.content_json)
                if correction_event is not None
                else None
            )
            same_time = correction_event is not None and _database_utc(
                correction_event.occurred_at
            ) == valid_from
            if (
                feedback.subject_id != subject_id
                or feedback.target_fact_id != target_fact_id
                or feedback.reason_code != reason_code
                or feedback.operator_id != operator_id
                or actual_content != expected_content
                or not same_time
            ):
                raise ValueError("request_id already belongs to another correction")
            return _feedback_result(feedback)
        target = session.scalar(
            select(MemoryFactModel).where(
                MemoryFactModel.id == target_fact_id,
                MemoryFactModel.tenant_id == tenant_id,
                MemoryFactModel.subject_id == subject_id,
                MemoryFactModel.status.in_(("active", "disputed")),
            )
        )
        if target is None:
            raise ValueError("current fact not found in tenant and subject scope")
        if target.fact_key == "purchase.status":
            raise ValueError(
                "verified purchase status must be corrected by a business source"
            )
        if valid_from < _database_utc(target.valid_from):
            raise ValueError("correction valid_from precedes the target fact")
        fact_key = target.fact_key

    uid_material = f"{tenant_id}\0{subject_id}\0{request_id}"
    event_uid = "manual-correction:" + hashlib.sha256(
        uid_material.encode("utf-8")
    ).hexdigest()
    event = append_memory_event(
        MemoryEventCreate(
            event_uid=event_uid,
            tenant_id=tenant_id,
            subject_id=subject_id,
            event_type="manual_correction",
            actor_type="human_agent",
            content={
                "target_fact_id": target_fact_id,
                "fact_key": fact_key,
                "fact_value": fact_value,
                "reason_code": reason_code,
            },
            source_type="manual_correction",
            source_id=request_id,
            occurred_at=valid_from,
            sensitivity="internal",
        )
    ).event
    result = apply_memory_candidate(
        tenant_id=tenant_id,
        subject_id=subject_id,
        candidate=MemoryOperationCandidate(
            operation="SUPERSEDE",
            memory_kind="semantic_fact",
            fact_key=fact_key,
            fact_value=fact_value,
            evidence_event_ids=[event.id],
            source_type="manual_customer_correction",
            valid_from=valid_from,
            confidence=1.0,
            supersedes_fact_id=target_fact_id,
            reason=reason_code,
        ),
    )
    with get_memory_session() as session:
        feedback = session.scalar(
            select(MemoryFeedbackModel).where(
                MemoryFeedbackModel.tenant_id == tenant_id,
                MemoryFeedbackModel.request_id == request_id,
            )
        )
        if feedback is None:
            feedback = MemoryFeedbackModel(
                tenant_id=tenant_id,
                subject_id=subject_id,
                request_id=request_id,
                feedback_type="correction",
                target_fact_id=target_fact_id,
                correction_event_id=event.id,
                replacement_fact_id=result.record_id,
                reason_code=reason_code,
                operator_id=operator_id,
                created_at=_now(),
            )
            session.add(feedback)
            session.commit()
            session.refresh(feedback)
        return _feedback_result(feedback)


def _feedback_result(feedback: MemoryFeedbackModel) -> dict[str, Any]:
    return {
        "request_id": feedback.request_id,
        "tenant_id": feedback.tenant_id,
        "subject_id": feedback.subject_id,
        "target_fact_id": feedback.target_fact_id,
        "replacement_fact_id": feedback.replacement_fact_id,
        "correction_event_id": feedback.correction_event_id,
        "status": "completed",
    }


async def purge_memory_subject(
    *,
    tenant_id: str,
    subject_id: str,
    confirm_subject_id: str,
    request_id: str,
    requested_by: str,
) -> dict[str, Any]:
    request_id = _required(request_id, "request_id")
    requested_by = _required(requested_by, "requested_by")
    if confirm_subject_id != subject_id:
        raise ValueError("confirm_subject_id does not match subject_id")
    subject_hash = hashlib.sha256(
        f"{tenant_id}\0{subject_id}".encode("utf-8")
    ).hexdigest()

    with get_memory_session() as session:
        audit = session.scalar(
            select(MemoryPurgeAuditModel).where(
                MemoryPurgeAuditModel.tenant_id == tenant_id,
                MemoryPurgeAuditModel.request_id == request_id,
            )
        )
        if audit is not None and audit.status == "completed":
            if (
                audit.subject_id_hash != subject_hash
                or audit.requested_by != requested_by
            ):
                raise ValueError("request_id already belongs to another purge")
            return _audit_result(audit)
        if audit is not None and (
            audit.subject_id_hash != subject_hash or audit.requested_by != requested_by
        ):
            raise ValueError("request_id already belongs to another purge")
        subject = session.scalar(
            select(MemorySubjectModel).where(
                MemorySubjectModel.id == subject_id,
                MemorySubjectModel.tenant_id == tenant_id,
                MemorySubjectModel.deleted_at.is_(None),
            )
        )
        if subject is None:
            raise ValueError("memory subject not found in tenant")
        external_user_ids = list(
            session.scalars(
                select(MemoryIdentityModel.external_user_id).where(
                    MemoryIdentityModel.tenant_id == tenant_id,
                    MemoryIdentityModel.subject_id == subject_id,
                )
            )
        )
        if audit is None:
            audit = MemoryPurgeAuditModel(
                tenant_id=tenant_id,
                subject_id_hash=subject_hash,
                request_id=request_id,
                status="processing",
                requested_by=requested_by,
                requested_at=_now(),
            )
            session.add(audit)
        else:
            audit.status = "processing"
            audit.error_code = None
        session.commit()

    try:
        vector_count = await delete_memory_subject_points(
            tenant_id=tenant_id, subject_id=subject_id
        )
        _ensure_legacy_profile_tables()
        counts = _delete_subject_rows(
            tenant_id=tenant_id,
            subject_id=subject_id,
            external_user_ids=external_user_ids,
            vector_count=vector_count,
            request_id=request_id,
        )
    except Exception as exc:
        _mark_purge_failed(tenant_id, request_id, type(exc).__name__[:64])
        raise
    return counts


def _ensure_legacy_profile_tables() -> None:
    from app.services import user_profile_service

    with user_profile_service._get_session():
        pass


def _delete_subject_rows(
    *,
    tenant_id: str,
    subject_id: str,
    external_user_ids: list[str],
    vector_count: int,
    request_id: str,
) -> dict[str, Any]:
    with get_memory_session() as session:
        episode_ids = select(MemoryEpisodeModel.id).where(
            MemoryEpisodeModel.tenant_id == tenant_id,
            MemoryEpisodeModel.subject_id == subject_id,
        )
        fact_ids = select(MemoryFactModel.id).where(
            MemoryFactModel.tenant_id == tenant_id,
            MemoryFactModel.subject_id == subject_id,
        )
        counts = {
            "identities_deleted": 0,
            "events_deleted": 0,
            "facts_deleted": 0,
            "episodes_deleted": 0,
            "jobs_deleted": 0,
            "feedback_deleted": 0,
            "shadow_runs_deleted": 0,
            "legacy_rows_deleted": 0,
            "vector_points_deleted": vector_count,
        }
        counts["feedback_deleted"] = session.execute(
            delete(MemoryFeedbackModel).where(
                MemoryFeedbackModel.tenant_id == tenant_id,
                MemoryFeedbackModel.subject_id == subject_id,
            )
        ).rowcount
        session.execute(
            delete(MemoryEpisodeEventModel).where(
                MemoryEpisodeEventModel.episode_id.in_(episode_ids)
            )
        )
        session.execute(
            delete(MemoryFactEvidenceModel).where(
                MemoryFactEvidenceModel.fact_id.in_(fact_ids)
            )
        )
        counts["jobs_deleted"] = session.execute(
            delete(MemoryJobModel).where(
                MemoryJobModel.tenant_id == tenant_id,
                MemoryJobModel.subject_id == subject_id,
            )
        ).rowcount
        counts["shadow_runs_deleted"] = session.execute(
            delete(MemoryShadowRunModel).where(
                MemoryShadowRunModel.tenant_id == tenant_id,
                MemoryShadowRunModel.subject_id == subject_id,
            )
        ).rowcount
        counts["episodes_deleted"] = session.execute(
            delete(MemoryEpisodeModel).where(
                MemoryEpisodeModel.tenant_id == tenant_id,
                MemoryEpisodeModel.subject_id == subject_id,
            )
        ).rowcount
        counts["facts_deleted"] = session.execute(
            delete(MemoryFactModel).where(
                MemoryFactModel.tenant_id == tenant_id,
                MemoryFactModel.subject_id == subject_id,
            )
        ).rowcount
        counts["events_deleted"] = session.execute(
            delete(MemoryEventModel).where(
                MemoryEventModel.tenant_id == tenant_id,
                MemoryEventModel.subject_id == subject_id,
            )
        ).rowcount
        counts["identities_deleted"] = session.execute(
            delete(MemoryIdentityModel).where(
                MemoryIdentityModel.tenant_id == tenant_id,
                MemoryIdentityModel.subject_id == subject_id,
            )
        ).rowcount
        if external_user_ids:
            for model in (ConversationMemoryModel, ProfileEventModel, UserProfileModel):
                counts["legacy_rows_deleted"] += session.execute(
                    delete(model).where(
                        model.tenant_id == tenant_id,
                        model.user_id.in_(external_user_ids),
                    )
                ).rowcount
        session.execute(
            delete(MemorySubjectModel).where(
                MemorySubjectModel.id == subject_id,
                MemorySubjectModel.tenant_id == tenant_id,
            )
        )
        audit = session.scalar(
            select(MemoryPurgeAuditModel).where(
                MemoryPurgeAuditModel.tenant_id == tenant_id,
                MemoryPurgeAuditModel.request_id == request_id,
            )
        )
        audit.status = "completed"
        audit.error_code = None
        audit.completed_at = _now()
        for key, value in counts.items():
            setattr(audit, key, value)
        session.commit()
        session.refresh(audit)
        return _audit_result(audit)


def _mark_purge_failed(tenant_id: str, request_id: str, error_code: str) -> None:
    with get_memory_session() as session:
        audit = session.scalar(
            select(MemoryPurgeAuditModel).where(
                MemoryPurgeAuditModel.tenant_id == tenant_id,
                MemoryPurgeAuditModel.request_id == request_id,
            )
        )
        if audit is not None:
            audit.status = "failed"
            audit.error_code = error_code
            audit.completed_at = _now()
            session.commit()


def _audit_result(audit: MemoryPurgeAuditModel) -> dict[str, Any]:
    return {
        "request_id": audit.request_id,
        "tenant_id": audit.tenant_id,
        "subject_id_hash": audit.subject_id_hash,
        "status": audit.status,
        "identities_deleted": audit.identities_deleted,
        "events_deleted": audit.events_deleted,
        "facts_deleted": audit.facts_deleted,
        "episodes_deleted": audit.episodes_deleted,
        "jobs_deleted": audit.jobs_deleted,
        "feedback_deleted": audit.feedback_deleted,
        "shadow_runs_deleted": audit.shadow_runs_deleted,
        "legacy_rows_deleted": audit.legacy_rows_deleted,
        "vector_points_deleted": audit.vector_points_deleted,
        "completed_at": audit.completed_at,
    }


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
