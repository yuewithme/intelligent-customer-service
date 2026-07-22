import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import MemoryEventModel, MemorySubjectModel
from app.domains.customers.schemas.memory import (
    MemoryEventAppendResult,
    MemoryEventCreate,
    MemoryEventRead,
)
from app.domains.customers.services.memory_repository import get_memory_session


class MemoryEventConflictError(ValueError):
    pass


class MemoryEventSubjectError(ValueError):
    pass


def append_memory_event(
    event: MemoryEventCreate | dict,
) -> MemoryEventAppendResult:
    if not isinstance(event, MemoryEventCreate):
        event = MemoryEventCreate.model_validate(event)
    content_json = _json_dumps(event.content)
    with get_memory_session() as session:
        subject = session.scalar(
            select(MemorySubjectModel).where(
                MemorySubjectModel.id == event.subject_id,
                MemorySubjectModel.tenant_id == event.tenant_id,
                MemorySubjectModel.deleted_at.is_(None),
            )
        )
        if subject is None:
            raise MemoryEventSubjectError("memory subject not found in tenant")

        existing = _find_scoped_event(
            session,
            tenant_id=event.tenant_id,
            subject_id=event.subject_id,
            event_uid=event.event_uid,
        )
        if existing is not None:
            _assert_same_event(existing, event, content_json)
            return MemoryEventAppendResult(event=_event_read(existing), created=False)

        model = MemoryEventModel(
            schema_version=event.schema_version,
            event_uid=event.event_uid,
            tenant_id=event.tenant_id,
            subject_id=event.subject_id,
            session_id=event.session_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            content_json=content_json,
            source_type=event.source_type,
            source_id=event.source_id,
            trace_id=event.trace_id,
            occurred_at=event.occurred_at.astimezone(timezone.utc),
            ingested_at=datetime.now(timezone.utc),
            sensitivity=event.sensitivity,
        )
        session.add(model)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = _find_scoped_event(
                session,
                tenant_id=event.tenant_id,
                subject_id=event.subject_id,
                event_uid=event.event_uid,
            )
            if existing is None:
                raise MemoryEventConflictError(
                    "event_uid already exists outside the scoped subject"
                ) from exc
            _assert_same_event(existing, event, content_json)
            return MemoryEventAppendResult(event=_event_read(existing), created=False)
        session.refresh(model)
        return MemoryEventAppendResult(event=_event_read(model), created=True)


def get_memory_event(
    *, tenant_id: str, subject_id: str, event_uid: str
) -> MemoryEventRead | None:
    with get_memory_session() as session:
        model = _find_scoped_event(
            session,
            tenant_id=tenant_id,
            subject_id=subject_id,
            event_uid=event_uid,
        )
        return _event_read(model) if model is not None else None


def get_memory_event_by_id(
    *, tenant_id: str, subject_id: str, event_id: int
) -> MemoryEventRead | None:
    with get_memory_session() as session:
        model = session.scalar(
            select(MemoryEventModel).where(
                MemoryEventModel.id == event_id,
                MemoryEventModel.tenant_id == tenant_id,
                MemoryEventModel.subject_id == subject_id,
                MemoryEventModel.deleted_at.is_(None),
            )
        )
        return _event_read(model) if model is not None else None


def list_memory_events(
    *,
    tenant_id: str,
    subject_id: str,
    session_id: str | None = None,
    limit: int = 20,
) -> list[MemoryEventRead]:
    limit = max(1, min(limit, 100))
    with get_memory_session() as session:
        query = select(MemoryEventModel).where(
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.deleted_at.is_(None),
        )
        if session_id is not None:
            query = query.where(MemoryEventModel.session_id == session_id)
        rows = list(
            session.scalars(
                query.order_by(
                    MemoryEventModel.occurred_at.desc(), MemoryEventModel.id.desc()
                ).limit(limit)
            )
        )
        return [_event_read(row) for row in reversed(rows)]


def _find_scoped_event(
    session, *, tenant_id: str, subject_id: str, event_uid: str
) -> MemoryEventModel | None:
    return session.scalar(
        select(MemoryEventModel).where(
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.event_uid == event_uid,
            MemoryEventModel.deleted_at.is_(None),
        )
    )


def _assert_same_event(
    model: MemoryEventModel, event: MemoryEventCreate, content_json: str
) -> None:
    expected = (
        event.schema_version,
        event.tenant_id,
        event.subject_id,
        event.session_id,
        event.event_type,
        event.actor_type,
        content_json,
        event.source_type,
        event.source_id,
        event.trace_id,
        _utc_iso(event.occurred_at),
        event.sensitivity,
    )
    actual = (
        model.schema_version,
        model.tenant_id,
        model.subject_id,
        model.session_id,
        model.event_type,
        model.actor_type,
        model.content_json,
        model.source_type,
        model.source_id,
        model.trace_id,
        _utc_iso(model.occurred_at),
        model.sensitivity,
    )
    if actual != expected:
        raise MemoryEventConflictError(
            "event_uid already exists with different immutable content"
        )


def _event_read(model: MemoryEventModel) -> MemoryEventRead:
    return MemoryEventRead(
        id=model.id,
        schema_version=model.schema_version,
        event_uid=model.event_uid,
        tenant_id=model.tenant_id,
        subject_id=model.subject_id,
        session_id=model.session_id,
        event_type=model.event_type,
        actor_type=model.actor_type,
        content=json.loads(model.content_json),
        source_type=model.source_type,
        source_id=model.source_id,
        trace_id=model.trace_id,
        occurred_at=_database_utc(model.occurred_at),
        ingested_at=_database_utc(model.ingested_at),
        sensitivity=model.sensitivity,
    )


def _json_dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
