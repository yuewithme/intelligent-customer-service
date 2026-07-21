import json
from datetime import timezone

from sqlalchemy import select

from app.db.models import MemoryEventModel
from app.schemas.memory import (
    FACT_SOURCE_POLICY,
    MemoryOperationCandidate,
    ValidatedMemoryOperation,
    validate_fact_value,
)
from app.services.memory_repository import get_memory_session


class MemoryValidationError(ValueError):
    pass


def validate_memory_candidate(
    *,
    tenant_id: str,
    subject_id: str,
    candidate: MemoryOperationCandidate | dict,
    min_confidence: float = 0.85,
) -> ValidatedMemoryOperation:
    with get_memory_session() as session:
        return validate_memory_candidate_in_session(
            session,
            tenant_id=tenant_id,
            subject_id=subject_id,
            candidate=candidate,
            min_confidence=min_confidence,
        )


def validate_memory_candidate_in_session(
    session,
    *,
    tenant_id: str,
    subject_id: str,
    candidate: MemoryOperationCandidate | dict,
    min_confidence: float = 0.85,
) -> ValidatedMemoryOperation:
    if not isinstance(candidate, MemoryOperationCandidate):
        candidate = MemoryOperationCandidate.model_validate(candidate)
    if candidate.operation == "NOOP":
        return ValidatedMemoryOperation(**candidate.model_dump())
    if candidate.confidence < min_confidence:
        raise MemoryValidationError("candidate confidence is below write threshold")

    event_ids = list(dict.fromkeys(candidate.evidence_event_ids))
    events = list(
        session.scalars(
            select(MemoryEventModel).where(
                MemoryEventModel.id.in_(event_ids),
                MemoryEventModel.tenant_id == tenant_id,
                MemoryEventModel.subject_id == subject_id,
                MemoryEventModel.deleted_at.is_(None),
            )
        )
    )
    if len(events) != len(event_ids):
        raise MemoryValidationError("evidence is missing or outside tenant scope")
    event_by_id = {event.id: event for event in events}
    ordered_events = [event_by_id[event_id] for event_id in event_ids]

    if candidate.memory_kind == "episode":
        _validate_episode(candidate, ordered_events)
        return ValidatedMemoryOperation(
            **candidate.model_dump(exclude={"evidence_event_ids"}),
            evidence_event_ids=event_ids,
        )

    if candidate.fact_key == "service.commitment":
        raise MemoryValidationError("service commitments must be stored as episodes")
    try:
        fact_value = validate_fact_value(candidate.fact_key or "", candidate.fact_value)
    except ValueError as exc:
        raise MemoryValidationError(str(exc)) from exc
    allowed_sources = FACT_SOURCE_POLICY[candidate.fact_key]
    if candidate.source_type not in allowed_sources:
        raise MemoryValidationError("source is not allowed for fact key")
    _validate_fact_source(candidate.source_type or "", ordered_events)
    normalized_value = json.dumps(
        fact_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if candidate.valid_from is not None and (
        candidate.valid_from.tzinfo is None
        or candidate.valid_from.utcoffset() is None
    ):
        raise MemoryValidationError("fact valid_from must be timezone-aware")
    valid_from = candidate.valid_from or _database_utc(
        max(event.occurred_at for event in events)
    )
    return ValidatedMemoryOperation(
        **candidate.model_dump(
            exclude={"fact_value", "valid_from", "evidence_event_ids"}
        ),
        fact_value=fact_value,
        valid_from=valid_from.astimezone(timezone.utc),
        evidence_event_ids=event_ids,
        normalized_value=normalized_value,
    )


def _validate_fact_source(source_type: str, events: list[MemoryEventModel]) -> None:
    actors = {event.actor_type for event in events}
    event_types = {event.event_type for event in events}
    if actors == {"assistant"}:
        raise MemoryValidationError("assistant-only evidence cannot support customer fact")
    if source_type == "customer_explicit" and "customer" not in actors:
        raise MemoryValidationError("customer fact requires customer evidence")
    if source_type == "manual_customer_correction" and (
        "manual_correction" not in event_types
        or not actors.intersection({"customer", "human_agent"})
    ):
        raise MemoryValidationError("manual correction requires correction evidence")
    if source_type == "verified_business_system" and not any(
        event.actor_type == "business_system"
        and event.source_type == "verified_business_system"
        for event in events
    ):
        raise MemoryValidationError("business fact requires verified business evidence")
    if source_type == "verified_contact_provider" and "contact_snapshot" not in event_types:
        raise MemoryValidationError("contact fact requires contact snapshot evidence")
    if source_type == "human_agent_annotation" and "human_agent" not in actors:
        raise MemoryValidationError("human annotation requires human agent evidence")
    if source_type == "customer_behavior" and not any(
        event.actor_type == "customer"
        or (
            event.event_type == "tool_observation"
            and event.actor_type == "business_system"
        )
        for event in events
    ):
        raise MemoryValidationError("customer behavior requires observed evidence")


def _validate_episode(
    candidate: MemoryOperationCandidate, events: list[MemoryEventModel]
) -> None:
    if candidate.operation != "ADD":
        raise MemoryValidationError("WP2 episodes support ADD only")
    if candidate.started_at is None:
        raise MemoryValidationError("episode requires started_at")
    if candidate.started_at.tzinfo is None:
        raise MemoryValidationError("episode started_at must be timezone-aware")
    if candidate.ended_at is not None:
        if candidate.ended_at.tzinfo is None:
            raise MemoryValidationError("episode ended_at must be timezone-aware")
        if candidate.ended_at < candidate.started_at:
            raise MemoryValidationError("episode ended_at precedes started_at")
    actors = {event.actor_type for event in events}
    if candidate.source_type == "customer_explicit" and "customer" not in actors:
        raise MemoryValidationError("customer episode requires customer evidence")
    if candidate.source_type == "assistant_commitment" and "assistant" not in actors:
        raise MemoryValidationError("assistant commitment requires assistant evidence")
    if candidate.source_type == "human_agent_annotation" and "human_agent" not in actors:
        raise MemoryValidationError("human episode requires human agent evidence")
    if candidate.source_type == "verified_business_system" and not any(
        event.actor_type == "business_system"
        and event.source_type == "verified_business_system"
        for event in events
    ):
        raise MemoryValidationError("business episode requires verified evidence")
    if candidate.source_type not in {
        "customer_explicit",
        "assistant_commitment",
        "human_agent_annotation",
        "verified_business_system",
    }:
        raise MemoryValidationError("unsupported episode source")
    if candidate.episode_type == "commitment":
        if not actors.intersection({"assistant", "human_agent"}):
            raise MemoryValidationError(
                "commitment episode requires assistant or human evidence"
            )


def _database_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
