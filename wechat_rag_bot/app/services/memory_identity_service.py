from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import MemoryIdentityModel, MemorySubjectModel
from app.schemas.memory import MemoryIdentityRead, MemorySubjectRead
from app.services.memory_repository import get_memory_session


class MemoryIdentityConflictError(ValueError):
    pass


class MemorySubjectNotFoundError(ValueError):
    pass


def resolve_or_create_subject(
    *,
    tenant_id: str,
    channel: str,
    external_user_id: str,
    owner_external_id: str = "",
    identity_source: str,
    verified: bool = False,
) -> MemorySubjectRead:
    key = _identity_key(
        tenant_id=tenant_id,
        channel=channel,
        owner_external_id=owner_external_id,
        external_user_id=external_user_id,
    )
    with get_memory_session() as session:
        identity = _find_identity(session, **key)
        if identity is not None:
            _upgrade_identity(
                session,
                identity,
                identity_source=identity_source,
                verified=verified,
            )
            subject = _get_subject(session, tenant_id, identity.subject_id)
            return _subject_read(subject)

        now = _now()
        subject = MemorySubjectModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            status="active",
            profile_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(subject)
        session.add(
            MemoryIdentityModel(
                subject_id=subject.id,
                identity_source=_required(identity_source, "identity_source"),
                verified=verified,
                created_at=now,
                updated_at=now,
                **key,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            identity = _find_identity(session, **key)
            if identity is None:
                raise
            subject = _get_subject(session, tenant_id, identity.subject_id)
        return _subject_read(subject)


def attach_identity(
    *,
    tenant_id: str,
    subject_id: str,
    channel: str,
    external_user_id: str,
    owner_external_id: str = "",
    identity_source: str,
    verified: bool = False,
) -> MemoryIdentityRead:
    key = _identity_key(
        tenant_id=tenant_id,
        channel=channel,
        owner_external_id=owner_external_id,
        external_user_id=external_user_id,
    )
    with get_memory_session() as session:
        _get_subject(session, tenant_id, subject_id)
        existing = _find_identity(session, **key)
        if existing is not None:
            if existing.subject_id != subject_id:
                raise MemoryIdentityConflictError(
                    "external identity already belongs to another subject"
                )
            _upgrade_identity(
                session,
                existing,
                identity_source=identity_source,
                verified=verified,
            )
            return _identity_read(existing)
        now = _now()
        identity = MemoryIdentityModel(
            subject_id=subject_id,
            identity_source=_required(identity_source, "identity_source"),
            verified=verified,
            created_at=now,
            updated_at=now,
            **key,
        )
        session.add(identity)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = _find_identity(session, **key)
            if existing is None or existing.subject_id != subject_id:
                raise MemoryIdentityConflictError(
                    "external identity already belongs to another subject"
                ) from exc
            identity = existing
        session.refresh(identity)
        return _identity_read(identity)


def get_subject_for_identity(
    *,
    tenant_id: str,
    channel: str,
    external_user_id: str,
    owner_external_id: str = "",
) -> MemorySubjectRead | None:
    key = _identity_key(
        tenant_id=tenant_id,
        channel=channel,
        owner_external_id=owner_external_id,
        external_user_id=external_user_id,
    )
    with get_memory_session() as session:
        identity = _find_identity(session, **key)
        if identity is None:
            return None
        return _subject_read(_get_subject(session, tenant_id, identity.subject_id))


def _identity_key(
    *, tenant_id: str, channel: str, owner_external_id: str, external_user_id: str
) -> dict[str, str]:
    return {
        "tenant_id": _required(tenant_id, "tenant_id"),
        "channel": _required(channel, "channel").lower(),
        "owner_external_id": owner_external_id.strip(),
        "external_user_id": _required(external_user_id, "external_user_id"),
    }


def _find_identity(session, **key) -> MemoryIdentityModel | None:
    return session.scalar(
        select(MemoryIdentityModel).where(
            MemoryIdentityModel.tenant_id == key["tenant_id"],
            MemoryIdentityModel.channel == key["channel"],
            MemoryIdentityModel.owner_external_id == key["owner_external_id"],
            MemoryIdentityModel.external_user_id == key["external_user_id"],
        )
    )


def _get_subject(session, tenant_id: str, subject_id: str) -> MemorySubjectModel:
    subject = session.scalar(
        select(MemorySubjectModel).where(
            MemorySubjectModel.id == subject_id,
            MemorySubjectModel.tenant_id == tenant_id,
            MemorySubjectModel.deleted_at.is_(None),
        )
    )
    if subject is None:
        raise MemorySubjectNotFoundError("memory subject not found in tenant")
    return subject


def _subject_read(model: MemorySubjectModel) -> MemorySubjectRead:
    return MemorySubjectRead(
        id=model.id,
        tenant_id=model.tenant_id,
        status=model.status,
        profile_version=model.profile_version,
        created_at=_database_utc(model.created_at),
        updated_at=_database_utc(model.updated_at),
    )


def _identity_read(model: MemoryIdentityModel) -> MemoryIdentityRead:
    return MemoryIdentityRead(
        id=model.id,
        subject_id=model.subject_id,
        tenant_id=model.tenant_id,
        channel=model.channel,
        owner_external_id=model.owner_external_id,
        external_user_id=model.external_user_id,
        identity_source=model.identity_source,
        verified=model.verified,
        created_at=_database_utc(model.created_at),
        updated_at=_database_utc(model.updated_at),
    )


def _upgrade_identity(
    session,
    model: MemoryIdentityModel,
    *,
    identity_source: str,
    verified: bool,
) -> None:
    if not verified or model.verified:
        return
    model.verified = True
    model.identity_source = _required(identity_source, "identity_source")
    model.updated_at = _now()
    session.commit()


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
