import json

from sqlalchemy import create_engine, inspect

import pytest

from app.core.config import get_settings


def _profile_db(monkeypatch, tmp_path):
    db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    return db_path


def test_minimal_profile_schema_keeps_basic_info_without_identity_platform():
    from app.infrastructure.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    profile_columns = {
        item["name"] for item in inspector.get_columns("user_profiles")
    }

    assert "basic_info_json" in profile_columns
    assert "user_identities" not in table_names
    assert "user_memory_facts" not in table_names
    assert "sales_memory_episodes" not in table_names
    assert "profile_refresh_jobs" not in table_names


@pytest.mark.asyncio
async def test_ensure_user_profile_persists_basic_info_under_external_user_id(
    monkeypatch, tmp_path
):
    _profile_db(monkeypatch, tmp_path)
    from app.services import user_profile_service

    user_profile_service._sessionmakers.clear()
    profile = await user_profile_service.ensure_user_profile(
        "wxid_customer",
        tenant_id="tenant_default",
        channel="wechat",
        basic_info={
            "nickname": "小李",
            "remark_name": "广西小李",
            "alias_name": "orchid_friend",
            "avatar_url": "https://example.invalid/avatar.jpg",
            "label_ids": ["12", "18"],
            "unexpected": "must not persist",
        },
    )

    assert profile["user_id"] == "wxid_customer"
    assert profile["basic_info"] == {
        "nickname": "小李",
        "remark_name": "广西小李",
        "alias_name": "orchid_friend",
        "avatar_url": "https://example.invalid/avatar.jpg",
        "label_ids": ["12", "18"],
    }

    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        raw = connection.exec_driver_sql(
            "SELECT basic_info_json FROM user_profiles WHERE user_id = ?",
            ("wxid_customer",),
        ).scalar_one()
    assert json.loads(raw)["remark_name"] == "广西小李"


@pytest.mark.asyncio
async def test_basic_info_refresh_preserves_existing_values_when_provider_returns_empty(
    monkeypatch, tmp_path
):
    _profile_db(monkeypatch, tmp_path)
    from app.services import user_profile_service

    user_profile_service._sessionmakers.clear()
    await user_profile_service.ensure_user_profile(
        "wxid_customer",
        channel="wechat",
        basic_info={"nickname": "小李", "alias_name": "orchid_friend"},
    )
    profile = await user_profile_service.ensure_user_profile(
        "wxid_customer",
        channel="wechat",
        basic_info={"nickname": "", "remark_name": "南宁兰友"},
    )

    assert profile["basic_info"] == {
        "nickname": "小李",
        "alias_name": "orchid_friend",
        "remark_name": "南宁兰友",
    }
