from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


@lru_cache
def get_session_factory():
    engine = create_engine(get_settings().database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)

