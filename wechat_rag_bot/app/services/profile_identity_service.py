import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ProfileRefreshJobModel,
    UserIdentityModel,
    UserProfileModel,
)
from app.schemas.profile_identity import (
    ContactSnapshot,
    ProviderIdentityKey,
    ResolvedProfileIdentity,
)


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [
    UserProfileModel.__table__,
    UserIdentityModel.__table__,
    ProfileRefreshJobModel.__table__,
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_user_id(key: ProviderIdentityKey) -> str:
    raw = "|".join(
        (key.tenant_id, key.provider, key.owner_external_id, key.external_user_id)
    )
    return f"profile_{uuid5(NAMESPACE_URL, raw).hex}"


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


async def resolve_private_contact(
    *,
    key: ProviderIdentityKey,
    channel: str,
    latest_w_id: str | None,
    source_account: str | None,
    contact: ContactSnapshot,
) -> ResolvedProfileIdentity:
    now = _now()
    with _get_session() as session:
        identity = session.scalar(
            select(UserIdentityModel).where(
                UserIdentityModel.tenant_id == key.tenant_id,
                UserIdentityModel.provider == key.provider,
                UserIdentityModel.owner_external_id == key.owner_external_id,
                UserIdentityModel.external_user_id == key.external_user_id,
            )
        )
        created = identity is None
        if identity is None:
            profile_user_id = _profile_user_id(key)
            profile = session.get(UserProfileModel, profile_user_id)
            if profile is None:
                profile = UserProfileModel(
                    user_id=profile_user_id,
                    tenant_id=key.tenant_id,
                    channel=channel,
                    current_stage="unknown",
                    risk_level="normal",
                    created_at=now,
                    updated_at=now,
                )
                session.add(profile)
                session.flush()
            identity = UserIdentityModel(
                profile_user_id=profile_user_id,
                tenant_id=key.tenant_id,
                provider=key.provider,
                channel=channel,
                owner_external_id=key.owner_external_id,
                external_user_id=key.external_user_id,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(identity)

        identity.latest_w_id = latest_w_id or identity.latest_w_id
        identity.source_account = source_account or identity.source_account
        identity.last_seen_at = now
        for field in (
            "alias_name",
            "nickname",
            "remark_name",
            "avatar_url",
            "province",
            "city",
        ):
            value = getattr(contact, field)
            if value:
                setattr(identity, field, value)
        if contact.label_ids:
            identity.label_ids_json = json.dumps(contact.label_ids, ensure_ascii=False)
        session.commit()
        return ResolvedProfileIdentity(
            profile_user_id=identity.profile_user_id,
            key=key,
            created=created,
        )
