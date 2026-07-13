import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ProfileRefreshJobModel, UserProfileModel
from app.services.user_profile_service import refresh_profile_from_memory


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [UserProfileModel.__table__, ProfileRefreshJobModel.__table__]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


async def schedule_profile_refresh(
    profile_user_id: str, *, delay_seconds: float = 30
) -> None:
    now = _now()
    due_at = now + timedelta(seconds=max(float(delay_seconds), 0))
    with _get_session() as session:
        row = session.get(ProfileRefreshJobModel, profile_user_id)
        if row is None:
            row = ProfileRefreshJobModel(
                profile_user_id=profile_user_id,
                status="pending",
                due_at=due_at,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.status = "pending"
            row.due_at = min(_aware(row.due_at), due_at)
            row.last_error = None
            row.updated_at = now
        session.commit()


def list_profile_refresh_jobs() -> list[dict]:
    with _get_session() as session:
        rows = session.scalars(
            select(ProfileRefreshJobModel).order_by(ProfileRefreshJobModel.due_at.asc())
        ).all()
        return [
            {
                "profile_user_id": row.profile_user_id,
                "status": row.status,
                "due_at": _aware(row.due_at).isoformat(),
                "attempts": row.attempts,
                "last_error": row.last_error,
            }
            for row in rows
        ]


async def process_due_profile_refresh_jobs(limit: int = 5) -> int:
    now = _now()
    with _get_session() as session:
        ids = session.scalars(
            select(ProfileRefreshJobModel.profile_user_id)
            .where(
                ProfileRefreshJobModel.status == "pending",
                ProfileRefreshJobModel.due_at <= now,
            )
            .order_by(ProfileRefreshJobModel.due_at.asc())
            .limit(limit)
        ).all()
    processed = 0
    for profile_user_id in ids:
        with _get_session() as session:
            row = session.get(ProfileRefreshJobModel, profile_user_id)
            if row is None or row.status != "pending":
                continue
            row.status = "processing"
            row.updated_at = _now()
            session.commit()
        try:
            await refresh_profile_from_memory(profile_user_id)
        except Exception as exc:
            with _get_session() as session:
                row = session.get(ProfileRefreshJobModel, profile_user_id)
                row.attempts = (row.attempts or 0) + 1
                row.last_error = str(exc)[:1000]
                row.status = "pending"
                row.due_at = _now() + timedelta(seconds=min(300, 5 * 2**row.attempts))
                row.updated_at = _now()
                session.commit()
            processed += 1
            continue
        with _get_session() as session:
            row = session.get(ProfileRefreshJobModel, profile_user_id)
            if row.status == "processing":
                row.status = "complete"
                row.last_error = None
                row.updated_at = _now()
            session.commit()
        processed += 1
    return processed


async def profile_refresh_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await process_due_profile_refresh_jobs(limit=5)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except TimeoutError:
            pass
