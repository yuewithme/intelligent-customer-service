import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.db.models import (
    MemoryEpisodeEventModel,
    MemoryEpisodeModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemorySubjectModel,
)
from app.schemas.memory import (
    MemoryApplyResult,
    MemoryOperationCandidate,
    ValidatedMemoryOperation,
)
from app.services.memory_repository import get_memory_session
from app.services.memory_validation_service import (
    MemoryValidationError,
    validate_memory_candidate_in_session,
)


SINGLETON_FACT_KEYS = frozenset(
    {
        "identity.display_name",
        "location.region",
        "communication.preferred_detail",
        "communication.preferred_channel",
        "purchase.budget",
        "purchase.status",
    }
)


def apply_memory_candidate(
    *,
    tenant_id: str,
    subject_id: str,
    candidate: MemoryOperationCandidate | dict,
    min_confidence: float = 0.85,
) -> MemoryApplyResult:
    with get_memory_session() as session:
        validated = validate_memory_candidate_in_session(
            session,
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            min_confidence=min_confidence,
        )
        if validated.operation == "NOOP":
            return MemoryApplyResult(
                memory_kind=validated.memory_kind,
                created=False,
                operation="NOOP",
            )
        if validated.memory_kind == "episode":
            result = _apply_episode(session, tenant_id, subject_id, validated)
        else:
            result = _apply_fact(session, tenant_id, subject_id, validated)
        session.commit()
        return result


def _apply_fact(
    session,
    tenant_id: str,
    subject_id: str,
    candidate: ValidatedMemoryOperation,
) -> MemoryApplyResult:
    duplicate = _find_applied_fact(session, tenant_id, subject_id, candidate)
    if duplicate is not None:
        return MemoryApplyResult(
            memory_kind="semantic_fact",
            record_id=duplicate.id,
            created=False,
            operation=candidate.operation,
        )

    active_exact = session.scalar(
        select(MemoryFactModel)
        .where(
            MemoryFactModel.tenant_id == tenant_id,
            MemoryFactModel.subject_id == subject_id,
            MemoryFactModel.fact_key == candidate.fact_key,
            MemoryFactModel.normalized_value == candidate.normalized_value,
            MemoryFactModel.status == "active",
        )
        .order_by(MemoryFactModel.version.desc(), MemoryFactModel.id.desc())
    )
    active_current = session.scalar(
        select(MemoryFactModel)
        .where(
            MemoryFactModel.tenant_id == tenant_id,
            MemoryFactModel.subject_id == subject_id,
            MemoryFactModel.fact_key == candidate.fact_key,
            MemoryFactModel.status == "active",
        )
        .order_by(MemoryFactModel.version.desc(), MemoryFactModel.id.desc())
    )

    operation = candidate.operation
    target = _target_fact(session, tenant_id, subject_id, candidate)
    if operation == "ADD" and active_exact is not None:
        operation = "REINFORCE"
        target = active_exact
    elif (
        operation == "ADD"
        and candidate.fact_key in SINGLETON_FACT_KEYS
        and active_current is not None
    ):
        operation = "SUPERSEDE"
        target = active_current
    elif operation == "REINFORCE" and target is None:
        target = active_exact
    elif (
        operation == "SUPERSEDE"
        and target is None
        and candidate.fact_key in SINGLETON_FACT_KEYS
    ):
        target = active_current

    if (
        operation in {"ADD", "SUPERSEDE"}
        and candidate.fact_key in SINGLETON_FACT_KEYS
        and active_current is not None
        and _database_utc(candidate.valid_from)
        < _database_utc(active_current.valid_from)
    ):
        return MemoryApplyResult(
            memory_kind="semantic_fact",
            record_id=active_current.id,
            created=False,
            operation="NOOP",
        )

    now = datetime.now(timezone.utc)
    if operation == "ADD":
        fact = _new_fact(
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            fact_uid=str(uuid4()),
            version=1,
            status="active",
            now=now,
        )
        session.add(fact)
        session.flush()
        _add_fact_evidence(session, fact.id, candidate.evidence_event_ids, now)
    elif operation == "REINFORCE":
        if target is None or target.status != "active":
            raise MemoryValidationError("REINFORCE requires an active matching fact")
        if target.normalized_value != candidate.normalized_value:
            raise MemoryValidationError("REINFORCE value does not match target fact")
        target.status = "superseded"
        target.updated_at = now
        fact = _new_fact(
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            fact_uid=target.fact_uid,
            version=target.version + 1,
            status="active",
            now=now,
            value_json=target.fact_value_json,
            normalized_value=target.normalized_value,
            valid_from=target.valid_from,
            confidence=max(target.confidence, candidate.confidence),
            supersedes_fact_id=target.id,
        )
        session.add(fact)
        session.flush()
        evidence_ids = _fact_evidence_ids(session, target.id)
        evidence_ids.extend(candidate.evidence_event_ids)
        _add_fact_evidence(session, fact.id, list(dict.fromkeys(evidence_ids)), now)
    elif operation == "SUPERSEDE":
        if target is None or target.status not in {"active", "disputed"}:
            raise MemoryValidationError("SUPERSEDE requires a current fact")
        _require_same_fact_key(target, candidate)
        target.status = "superseded"
        target.valid_to = candidate.valid_from
        target.updated_at = now
        fact = _new_fact(
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            fact_uid=target.fact_uid,
            version=target.version + 1,
            status="active",
            now=now,
            supersedes_fact_id=target.id,
        )
        session.add(fact)
        session.flush()
        _add_fact_evidence(session, fact.id, candidate.evidence_event_ids, now)
    elif operation == "DISPUTE":
        if target is None:
            target = active_current
        if target is None:
            raise MemoryValidationError("DISPUTE requires an existing fact")
        _require_same_fact_key(target, candidate)
        target.status = "disputed"
        target.updated_at = now
        fact = _new_fact(
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            fact_uid=str(uuid4()),
            version=1,
            status="disputed",
            now=now,
        )
        session.add(fact)
        session.flush()
        _add_fact_evidence(session, fact.id, candidate.evidence_event_ids, now)
    elif operation == "RESOLVE":
        if target is None or target.status != "disputed":
            raise MemoryValidationError("RESOLVE requires a disputed fact")
        _require_same_fact_key(target, candidate)
        disputed = list(
            session.scalars(
                select(MemoryFactModel).where(
                    MemoryFactModel.tenant_id == tenant_id,
                    MemoryFactModel.subject_id == subject_id,
                    MemoryFactModel.fact_key == candidate.fact_key,
                    MemoryFactModel.status == "disputed",
                )
            )
        )
        for row in disputed:
            row.status = "superseded"
            row.valid_to = candidate.valid_from
            row.updated_at = now
        fact = _new_fact(
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            fact_uid=target.fact_uid,
            version=target.version + 1,
            status="active",
            now=now,
            supersedes_fact_id=target.id,
        )
        session.add(fact)
        session.flush()
        _add_fact_evidence(session, fact.id, candidate.evidence_event_ids, now)
    else:
        raise MemoryValidationError(f"unsupported fact operation: {operation}")

    _increment_profile_version(session, tenant_id, subject_id, now)
    return MemoryApplyResult(
        memory_kind="semantic_fact",
        record_id=fact.id,
        created=True,
        operation=operation,
    )


def _apply_episode(
    session,
    tenant_id: str,
    subject_id: str,
    candidate: ValidatedMemoryOperation,
) -> MemoryApplyResult:
    episode_uid = _episode_uid(tenant_id, subject_id, candidate)
    existing = session.scalar(
        select(MemoryEpisodeModel)
        .where(
            MemoryEpisodeModel.tenant_id == tenant_id,
            MemoryEpisodeModel.subject_id == subject_id,
            MemoryEpisodeModel.episode_uid == episode_uid,
        )
        .order_by(MemoryEpisodeModel.version.desc())
    )
    if existing is not None:
        return MemoryApplyResult(
            memory_kind="episode",
            record_id=existing.id,
            created=False,
            operation=candidate.operation,
        )
    now = datetime.now(timezone.utc)
    episode = MemoryEpisodeModel(
        episode_uid=episode_uid,
        tenant_id=tenant_id,
        subject_id=subject_id,
        episode_type=candidate.episode_type,
        title=candidate.title,
        summary=candidate.summary,
        outcome=candidate.outcome,
        importance=candidate.importance,
        started_at=candidate.started_at,
        ended_at=candidate.ended_at,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(episode)
    session.flush()
    session.add_all(
        [
            MemoryEpisodeEventModel(
                episode_id=episode.id,
                event_id=event_id,
                position=position,
                created_at=now,
            )
            for position, event_id in enumerate(candidate.evidence_event_ids)
        ]
    )
    _increment_profile_version(session, tenant_id, subject_id, now)
    return MemoryApplyResult(
        memory_kind="episode",
        record_id=episode.id,
        created=True,
        operation="ADD",
    )


def _new_fact(
    *,
    tenant_id: str,
    subject_id: str,
    candidate: ValidatedMemoryOperation,
    fact_uid: str,
    version: int,
    status: str,
    now: datetime,
    value_json: str | None = None,
    normalized_value: str | None = None,
    valid_from: datetime | None = None,
    confidence: float | None = None,
    supersedes_fact_id: int | None = None,
) -> MemoryFactModel:
    return MemoryFactModel(
        fact_uid=fact_uid,
        tenant_id=tenant_id,
        subject_id=subject_id,
        fact_key=candidate.fact_key,
        fact_value_json=value_json or candidate.normalized_value,
        normalized_value=normalized_value or candidate.normalized_value,
        source_type=candidate.source_type,
        confidence=confidence if confidence is not None else candidate.confidence,
        valid_from=valid_from or candidate.valid_from,
        recorded_at=now,
        status=status,
        supersedes_fact_id=supersedes_fact_id,
        version=version,
        created_by=(candidate.reason or "memory_worker")[:64],
        created_at=now,
        updated_at=now,
    )


def _target_fact(
    session,
    tenant_id: str,
    subject_id: str,
    candidate: ValidatedMemoryOperation,
) -> MemoryFactModel | None:
    if candidate.supersedes_fact_id is None:
        return None
    return session.scalar(
        select(MemoryFactModel).where(
            MemoryFactModel.id == candidate.supersedes_fact_id,
            MemoryFactModel.tenant_id == tenant_id,
            MemoryFactModel.subject_id == subject_id,
        )
    )


def _find_applied_fact(
    session,
    tenant_id: str,
    subject_id: str,
    candidate: ValidatedMemoryOperation,
) -> MemoryFactModel | None:
    candidates = list(
        session.scalars(
            select(MemoryFactModel)
            .where(
                MemoryFactModel.tenant_id == tenant_id,
                MemoryFactModel.subject_id == subject_id,
                MemoryFactModel.fact_key == candidate.fact_key,
                MemoryFactModel.normalized_value == candidate.normalized_value,
                MemoryFactModel.status.in_(("active", "disputed", "superseded")),
            )
            .order_by(MemoryFactModel.id.desc())
        )
    )
    expected = set(candidate.evidence_event_ids)
    return next(
        (
            fact
            for fact in candidates
            if expected.issubset(set(_fact_evidence_ids(session, fact.id)))
        ),
        None,
    )


def _fact_evidence_ids(session, fact_id: int) -> list[int]:
    return list(
        session.scalars(
            select(MemoryFactEvidenceModel.event_id).where(
                MemoryFactEvidenceModel.fact_id == fact_id
            )
        )
    )


def _add_fact_evidence(
    session, fact_id: int, event_ids: list[int], now: datetime
) -> None:
    session.add_all(
        [
            MemoryFactEvidenceModel(
                fact_id=fact_id,
                event_id=event_id,
                created_at=now,
            )
            for event_id in event_ids
        ]
    )


def _require_same_fact_key(
    target: MemoryFactModel, candidate: ValidatedMemoryOperation
) -> None:
    if target.fact_key != candidate.fact_key:
        raise MemoryValidationError("target fact key does not match candidate")


def _increment_profile_version(
    session, tenant_id: str, subject_id: str, now: datetime
) -> None:
    subject = session.scalar(
        select(MemorySubjectModel).where(
            MemorySubjectModel.id == subject_id,
            MemorySubjectModel.tenant_id == tenant_id,
        )
    )
    if subject is None:
        raise MemoryValidationError("memory subject not found in tenant")
    subject.profile_version += 1
    subject.updated_at = now


def _episode_uid(
    tenant_id: str, subject_id: str, candidate: ValidatedMemoryOperation
) -> str:
    payload = "|".join(
        (
            tenant_id,
            subject_id,
            candidate.episode_type or "",
            ",".join(str(value) for value in candidate.evidence_event_ids),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
