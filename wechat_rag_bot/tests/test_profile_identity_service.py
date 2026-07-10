from sqlalchemy import create_engine, inspect, select

import pytest

from app.config import get_settings


def _profile_db(monkeypatch, tmp_path):
    db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    return db_path


def test_provider_identity_contract_excludes_transient_connection_fields():
    from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey

    key = ProviderIdentityKey(
        tenant_id="tenant_default",
        provider="eyun",
        owner_external_id="wx_owner_1",
        external_user_id="wx_customer_1",
    )
    contact = ContactSnapshot(nickname="小李", remark_name="广西小李")

    assert key.model_dump() == {
        "tenant_id": "tenant_default",
        "provider": "eyun",
        "owner_external_id": "wx_owner_1",
        "external_user_id": "wx_customer_1",
    }
    assert contact.display_name == "广西小李"
    assert "w_id" not in key.model_dump()


def test_identity_and_refresh_tables_have_minimum_closed_loop_columns():
    from app.db.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    identities = {item["name"] for item in inspector.get_columns("user_identities")}
    jobs = {item["name"] for item in inspector.get_columns("profile_refresh_jobs")}

    assert {
        "profile_user_id",
        "tenant_id",
        "provider",
        "owner_external_id",
        "external_user_id",
        "latest_w_id",
        "last_seen_at",
    } <= identities
    assert {"profile_user_id", "status", "due_at", "attempts", "last_error"} <= jobs


@pytest.mark.asyncio
async def test_resolve_private_contact_reuses_identity_and_refreshes_wid(
    monkeypatch, tmp_path
):
    _profile_db(monkeypatch, tmp_path)
    from app.db.models import UserIdentityModel
    from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey
    from app.services.profile_identity_service import resolve_private_contact

    key = ProviderIdentityKey(
        tenant_id="tenant_default",
        provider="eyun",
        owner_external_id="owner_1",
        external_user_id="customer_1",
    )
    first = await resolve_private_contact(
        key=key,
        channel="wechat",
        latest_w_id="wid_1",
        source_account="sales_a",
        contact=ContactSnapshot(nickname="小李"),
    )
    second = await resolve_private_contact(
        key=key,
        channel="wechat",
        latest_w_id="wid_2",
        source_account="sales_a",
        contact=ContactSnapshot(remark_name="广西小李"),
    )

    assert first.created is True
    assert second.created is False
    assert second.profile_user_id == first.profile_user_id

    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(UserIdentityModel).where(
                UserIdentityModel.profile_user_id == first.profile_user_id
            )
        ).mappings().one()
    assert row["latest_w_id"] == "wid_2"
    assert row["remark_name"] == "广西小李"


@pytest.mark.asyncio
async def test_same_sender_under_another_owned_account_gets_another_profile(
    monkeypatch, tmp_path
):
    _profile_db(monkeypatch, tmp_path)
    from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey
    from app.services.profile_identity_service import resolve_private_contact

    common = {
        "channel": "wechat",
        "latest_w_id": "wid",
        "source_account": None,
        "contact": ContactSnapshot(nickname="小李"),
    }
    one = await resolve_private_contact(
        key=ProviderIdentityKey(
            provider="eyun",
            owner_external_id="owner_1",
            external_user_id="customer_1",
        ),
        **common,
    )
    two = await resolve_private_contact(
        key=ProviderIdentityKey(
            provider="eyun",
            owner_external_id="owner_2",
            external_user_id="customer_1",
        ),
        **common,
    )

    assert one.profile_user_id != two.profile_user_id

