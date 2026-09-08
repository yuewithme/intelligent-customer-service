from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import EyunAccountSettingModel


@lru_cache
def _session_factory(database_url: str):
    engine = create_engine(database_url)
    EyunAccountSettingModel.__table__.create(engine, checkfirst=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def load_eyun_account_settings() -> None:
    settings = get_settings()
    with _session_factory(settings.database_url)() as session:
        row = session.get(EyunAccountSettingModel, 1)
        if row is not None:
            settings.eyun_wid = row.w_id
            settings.eyun_wc_id = row.wc_id


def get_eyun_account_settings() -> dict[str, str]:
    settings = get_settings()
    return {"w_id": settings.eyun_wid, "wc_id": settings.eyun_wc_id}


def save_eyun_account_settings(*, w_id: str, wc_id: str) -> dict[str, str]:
    settings = get_settings()
    with _session_factory(settings.database_url)() as session:
        row = session.get(EyunAccountSettingModel, 1)
        if row is None:
            row = EyunAccountSettingModel(id=1)
            session.add(row)
        row.w_id = w_id
        row.wc_id = wc_id
        session.commit()
    settings.eyun_wid = w_id
    settings.eyun_wc_id = wc_id
    return get_eyun_account_settings()


def refresh_eyun_account_wid(*, w_id: str, wc_id: str) -> None:
    settings = get_settings()
    with _session_factory(settings.database_url)() as session:
        row = session.get(EyunAccountSettingModel, 1)
        # Keep environment-only installations unchanged until an admin saves.
        if row is not None and row.wc_id == wc_id:
            row.w_id = w_id
            session.commit()
    settings.eyun_wid = w_id
