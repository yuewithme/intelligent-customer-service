from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    MemoryEventModel,
    MemoryEpisodeEventModel,
    MemoryEpisodeModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemoryIdentityModel,
    MemoryJobModel,
    MemoryRolloutGateModel,
    MemoryShadowRunModel,
    MemorySubjectModel,
)


_sessionmakers: dict[str, sessionmaker] = {}
_MEMORY_TABLES = [
    MemorySubjectModel.__table__,
    MemoryIdentityModel.__table__,
    MemoryEventModel.__table__,
    MemoryFactModel.__table__,
    MemoryFactEvidenceModel.__table__,
    MemoryEpisodeModel.__table__,
    MemoryEpisodeEventModel.__table__,
    MemoryJobModel.__table__,
    MemoryShadowRunModel.__table__,
    MemoryRolloutGateModel.__table__,
]


def get_memory_session() -> Session:
    database_url = get_settings().database_url
    factory = _sessionmakers.get(database_url)
    if factory is None:
        engine = create_engine(database_url)
        Base.metadata.create_all(engine, tables=_MEMORY_TABLES)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[database_url] = factory
    return factory()


def reset_memory_repository_cache() -> None:
    for factory in _sessionmakers.values():
        factory.kw["bind"].dispose()
    _sessionmakers.clear()
