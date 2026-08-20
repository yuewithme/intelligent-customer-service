import pytest

from app.core.config import get_settings
from app.domains.sales.services import contact_sync_service as service


@pytest.mark.asyncio
async def test_contact_sync_initializes_each_login_instance_before_address_list(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'contacts.db'}")
    monkeypatch.setenv("EYUN_WID", "wid-current")
    get_settings.cache_clear()
    service._sessionmakers.clear()
    service._initialized_wids.clear()
    calls = []

    async def initialize(*, w_id):
        calls.append(("initialize", w_id))
        return True

    async def query(w_id):
        calls.append(("query", w_id))
        return ["customer-1"]

    async def refresh(wc_ids, w_id):
        calls.append(("refresh", w_id, tuple(wc_ids)))

    monkeypatch.setattr(service, "initialize_eyun_contacts", initialize)
    monkeypatch.setattr(service, "query_eyun_friend_ids", query)
    monkeypatch.setattr(service, "_refresh_contact_details", refresh)

    first = await service.sync_eyun_contacts()
    second = await service.sync_eyun_contacts()

    assert first["new"] == 1
    assert second["new"] == 0
    assert calls == [
        ("initialize", "wid-current"),
        ("query", "wid-current"),
        ("refresh", "wid-current", ("customer-1",)),
        ("query", "wid-current"),
        ("refresh", "wid-current", ("customer-1",)),
    ]
