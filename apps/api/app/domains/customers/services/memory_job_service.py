from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import MemoryEventModel, MemoryJobModel, MemorySubjectModel
from app.domains.customers.schemas.memory import MemoryJobRead
from app.domains.customers.services.memory_repository import get_memory_session


class MemoryJobLeaseError(ValueError):
    pass


class MemoryJobScopeError(ValueError):
    pass


def enqueue_memory_job(
    *,
    tenant_id: str,
    subject_id: str,
    trigger_event_id: int,
    job_type: str = "extract_memory",
    dedup_key: str | None = None,
    available_at: datetime | None = None,
) -> MemoryJobRead:
    dedup_key = (dedup_key or f"{job_type}:{trigger_event_id}").strip()
    if not dedup_key:
        raise ValueError("dedup_key must not be empty")
    if len(dedup_key) > 512:
        raise ValueError("dedup_key exceeds 512 characters")
    job_type = job_type.strip()
    if not job_type or len(job_type) > 64:
        raise ValueError("job_type must contain at most 64 characters")
    now = _now()
    available_at = _aware_utc(available_at or now, "available_at")
    with get_memory_session() as session:
        _require_scope(session, tenant_id, subject_id, trigger_event_id)
        existing = session.scalar(
            select(MemoryJobModel).where(
                MemoryJobModel.tenant_id == tenant_id,
                MemoryJobModel.dedup_key == dedup_key,
            )
        )
        if existing is not None:
            if (
                existing.subject_id != subject_id
                or existing.trigger_event_id != trigger_event_id
                or existing.job_type != job_type
            ):
                raise MemoryJobScopeError(
                    "dedup_key already belongs to a different memory job"
                )
            return _job_read(existing)
        model = MemoryJobModel(
            tenant_id=tenant_id,
            subject_id=subject_id,
            job_type=job_type,
            dedup_key=dedup_key,
            trigger_event_id=trigger_event_id,
            status="pending",
            attempts=0,
            available_at=available_at,
            created_at=now,
            updated_at=now,
        )
        session.add(model)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = session.scalar(
                select(MemoryJobModel).where(
                    MemoryJobModel.tenant_id == tenant_id,
                    MemoryJobModel.dedup_key == dedup_key,
                )
            )
            if existing is None:
                raise
            if (
                existing.subject_id != subject_id
                or existing.trigger_event_id != trigger_event_id
                or existing.job_type != job_type
            ):
                raise MemoryJobScopeError(
                    "dedup_key already belongs to a different memory job"
                ) from exc
            model = existing
        session.refresh(model)
        return _job_read(model)


def claim_memory_job(
    *, worker_id: str, lease_seconds: int, now: datetime | None = None
) -> MemoryJobRead | None:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id must not be empty")
    now = _aware_utc(now or _now(), "now")
    lease_cutoff = now - timedelta(seconds=max(1, lease_seconds))
    with get_memory_session() as session:
        eligible = or_(
            (
                MemoryJobModel.status.in_(("pending", "retry"))
                & (MemoryJobModel.available_at <= now)
            ),
            (
                (MemoryJobModel.status == "processing")
                & (MemoryJobModel.locked_at <= lease_cutoff)
            ),
        )
        job_id = session.scalar(
            select(MemoryJobModel.id)
            .where(eligible)
            .order_by(MemoryJobModel.available_at, MemoryJobModel.id)
            .limit(1)
        )
        if job_id is None:
            return None
        result = session.execute(
            update(MemoryJobModel)
            .where(MemoryJobModel.id == job_id, eligible)
            .values(
                status="processing",
                attempts=MemoryJobModel.attempts + 1,
                locked_at=now,
                locked_by=worker_id,
                last_error=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            session.rollback()
            return None
        session.commit()
        return _job_read(session.get(MemoryJobModel, job_id))


def complete_memory_job(
    *, job_id: int, worker_id: str, now: datetime | None = None
) -> MemoryJobRead:
    now = _aware_utc(now or _now(), "now")
    with get_memory_session() as session:
        job = _leased_job(session, job_id, worker_id)
        job.status = "completed"
        job.completed_at = now
        job.locked_at = None
        job.locked_by = None
        job.updated_at = now
        session.commit()
        return _job_read(job)


def fail_memory_job(
    *,
    job_id: int,
    worker_id: str,
    error: str,
    max_attempts: int,
    retry_base_seconds: int,
    now: datetime | None = None,
) -> MemoryJobRead:
    now = _aware_utc(now or _now(), "now")
    with get_memory_session() as session:
        job = _leased_job(session, job_id, worker_id)
        terminal = job.attempts >= max(1, max_attempts)
        job.status = "dead" if terminal else "retry"
        job.available_at = now + timedelta(
            seconds=max(1, retry_base_seconds) * max(1, job.attempts)
        )
        job.last_error = str(error)[:2000]
        job.locked_at = None
        job.locked_by = None
        job.updated_at = now
        session.commit()
        return _job_read(job)


def get_memory_job(job_id: int) -> MemoryJobRead | None:
    with get_memory_session() as session:
        job = session.get(MemoryJobModel, job_id)
        return _job_read(job) if job is not None else None


def _require_scope(session, tenant_id: str, subject_id: str, event_id: int) -> None:
    subject = session.scalar(
        select(MemorySubjectModel.id).where(
            MemorySubjectModel.id == subject_id,
            MemorySubjectModel.tenant_id == tenant_id,
            MemorySubjectModel.deleted_at.is_(None),
        )
    )
    event = session.scalar(
        select(MemoryEventModel.id).where(
            MemoryEventModel.id == event_id,
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.deleted_at.is_(None),
        )
    )
    if subject is None or event is None:
        raise MemoryJobScopeError("job subject or trigger event is outside tenant scope")


def _leased_job(session, job_id: int, worker_id: str) -> MemoryJobModel:
    job = session.scalar(
        select(MemoryJobModel).where(
            MemoryJobModel.id == job_id,
            MemoryJobModel.status == "processing",
            MemoryJobModel.locked_by == worker_id,
        )
    )
    if job is None:
        raise MemoryJobLeaseError("memory job lease is not owned by this worker")
    return job


def _job_read(model: MemoryJobModel) -> MemoryJobRead:
    return MemoryJobRead(
        id=model.id,
        tenant_id=model.tenant_id,
        subject_id=model.subject_id,
        job_type=model.job_type,
        dedup_key=model.dedup_key,
        trigger_event_id=model.trigger_event_id,
        status=model.status,
        attempts=model.attempts,
        available_at=_database_utc(model.available_at),
        locked_at=_database_utc(model.locked_at) if model.locked_at else None,
        locked_by=model.locked_by,
        last_error=model.last_error,
        created_at=_database_utc(model.created_at),
        updated_at=_database_utc(model.updated_at),
        completed_at=(
            _database_utc(model.completed_at) if model.completed_at else None
        ),
    )


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
