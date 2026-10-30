import asyncio
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import ConversationModel


@pytest.mark.asyncio
async def test_initialize_contacts_uses_official_endpoint(monkeypatch):
    from app.integrations.eyun.services import eyun_contact_service as service

    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "1000", "message": "success", "data": None}

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return Response()

    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)

    assert await service.initialize_eyun_contacts(w_id="wid") is True
    assert calls[1][1] == "https://eyun.example.com/initAddressList"
    assert calls[1][2]["json"] == {"wId": "wid"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_empty_contact_initializes_then_refreshes_and_backfills(
    monkeypatch, tmp_path
):
    from app.integrations.eyun.services import eyun_contact_service as service

    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    )
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}"
    )
    get_settings.cache_clear()
    assert hasattr(service, "refresh_eyun_contact")
    calls = []
    snapshot = {
        "nickname": "兰友张姐",
        "avatar_url": "https://example.com/avatar.jpg",
    }

    async def initialize(**kwargs):
        assert kwargs == {"w_id": "wid"}
        calls.append("initAddressList")
        return True

    async def sleep(delay):
        assert delay == 0
        calls.append("sleep")

    async def get_contact(**kwargs):
        assert kwargs == {"w_id": "wid", "wc_id": "customer"}
        initial_lookup = "getContact" not in calls
        calls.append("getContact")
        return {} if initial_lookup else snapshot

    async def ensure_profile(user_id, **kwargs):
        assert user_id == "customer"
        assert kwargs["basic_info"] == snapshot
        calls.append("profile")

    async def update_identity(**kwargs):
        assert kwargs["user_id"] == "customer"
        assert kwargs["metadata"] == snapshot
        calls.append("conversation")

    monkeypatch.setattr(
        service, "initialize_eyun_contacts", initialize, raising=False
    )
    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", get_contact)
    monkeypatch.setattr(service, "ensure_user_profile", ensure_profile, raising=False)
    monkeypatch.setattr(
        service, "update_customer_identity", update_identity, raising=False
    )

    await service.refresh_eyun_contact(
        w_id="wid",
        wc_id="customer",
        user_id="customer",
        tenant_id="tenant_default",
        channel="wechat",
        session_id="default",
        delay_seconds=0,
    )

    assert calls == [
        "getContact",
        "initAddressList",
        "sleep",
        "getContact",
        "profile",
        "conversation",
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["missing", "fresh", "expired"])
async def test_cache_only_contact_lookup_never_calls_provider(monkeypatch, cache_state):
    from app.integrations.eyun.services import eyun_contact_service as service

    key = ("wid_cache_only", "customer")
    snapshot = {"nickname": "兰友张姐"}
    if cache_state == "missing":
        monkeypatch.delitem(service._contact_cache, key, raising=False)
    else:
        age = 0 if cache_state == "fresh" else service._CACHE_TTL_SECONDS + 1
        monkeypatch.setitem(service._contact_cache, key, (service.time.monotonic() - age, snapshot))

    def fail_client(**kwargs):
        raise AssertionError("callback cache lookups must not call the provider")

    monkeypatch.setattr(service.httpx, "AsyncClient", fail_client)
    result = await service.get_eyun_contact_snapshot(
        w_id=key[0], wc_id=key[1], cache_only=True
    )
    assert result == (snapshot if cache_state == "fresh" else {})


@pytest.mark.asyncio
@pytest.mark.parametrize("global_handoff", [False, True])
@pytest.mark.parametrize("existing_profile", [False, True])
async def test_new_customer_is_visible_before_slow_contact_backfill(
    monkeypatch, tmp_path, global_handoff, existing_profile
):
    from app.domains.conversations.services import conversation_service
    from app.domains.customers.services.user_profile_service import ensure_user_profile
    from app.integrations.eyun.services import eyun_callback_service as callback
    from app.integrations.eyun.services import eyun_contact_service as contacts

    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    get_settings.cache_clear()
    started = asyncio.Event()
    release = asyncio.Event()
    key = ("wid_new_customer", "wxid_new_customer")
    for wc_id in (key[1], "shop_a"):
        monkeypatch.delitem(contacts._contact_cache, (key[0], wc_id), raising=False)
    if existing_profile:
        await ensure_user_profile(key[1], tenant_id="shop_b", channel="wechat", basic_info={
            "owner_wc_id": "shop_b", "nickname": "旧昵称", "remark_name": "另一账号备注",
            "alias_name": "old_alias",
        })

    async def slow_lookup(**kwargs):
        assert kwargs == {"w_id": key[0], "wc_id": key[1]}
        started.set()
        await release.wait()
        return {"nickname": "兰友张姐", "alias_name": "orchid_new", "avatar_url": "https://example.com/avatar.jpg"}

    async def fail_initialize(**kwargs):
        raise AssertionError("available contact details must not initialize the address book")

    async def ignore_async(*args, **kwargs):
        return None

    monkeypatch.setattr(contacts, "get_eyun_contact_snapshot", slow_lookup)
    monkeypatch.setattr(contacts, "initialize_eyun_contacts", fail_initialize)
    monkeypatch.setattr(callback, "is_global_handoff_enabled", lambda: global_handoff)
    monkeypatch.setattr(callback, "enqueue_eyun_inbound", ignore_async)
    monkeypatch.setattr(conversation_service, "_notify_handoff_safely", ignore_async)

    await asyncio.wait_for(callback.handle_eyun_callback({
        "messageType": "60001", "wcId": "shop_a",
        "data": {"wId": key[0], "fromUser": key[1], "toUser": "shop_a",
                 "content": "新客问浇水", "newMsgId": 901, "self": False},
    }), timeout=1)
    task = contacts._contact_refresh_tasks[key]
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not task.done()
        before = await conversation_service.list_conversations(tenant_id="shop_a", keyword="新客问浇水")
        assert before["total"] == 1
        row = before["items"][0]
        assert row["unread_count"] == 1
        assert row["status"] == ("handoff_pending" if global_handoff else "ai_waiting")
        assert not row["user_display_name"]
        assert (await conversation_service.list_conversations(tenant_id="shop_a", keyword="另一账号备注"))["total"] == 0
    finally:
        release.set()
        await task

    for keyword in ("兰友张姐", "orchid_new"):
        after = await conversation_service.list_conversations(tenant_id="shop_a", keyword=keyword)
        assert after["total"] == 1
        assert after["items"][0]["unread_count"] == 1
        assert after["items"][0]["status"] == row["status"]
        assert after["items"][0]["user_display_name"] == "兰友张姐"
    assert (await conversation_service.list_conversations(tenant_id="shop_b", keyword="兰友张姐"))["total"] == 0
    assert (await conversation_service.list_conversations(tenant_id="shop_a", keyword="另一账号备注"))["total"] == 0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_empty_callback_schedules_contact_refresh(monkeypatch):
    from app.integrations.eyun.services import eyun_callback_service as service

    scheduled = []

    async def empty_contact(**kwargs):
        return {}

    async def ignore_async(*args, **kwargs):
        return {}

    def schedule(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(service, "get_eyun_contact_snapshot", empty_contact)
    monkeypatch.setattr(service, "ensure_user_profile", ignore_async)
    monkeypatch.setattr(service, "record_customer_message", ignore_async)
    monkeypatch.setattr(service, "enqueue_eyun_inbound", ignore_async)
    monkeypatch.setattr(
        service, "schedule_eyun_contact_refresh", schedule, raising=False
    )

    await service.handle_eyun_callback(
        {
            "messageType": "60001",
            "wcId": "owner",
            "data": {
                "wId": "wid",
                "fromUser": "customer",
                "toUser": "owner",
                "content": "你好",
                "newMsgId": 1,
                "self": False,
            },
        }
    )

    assert scheduled == [
        {
            "w_id": "wid",
            "wc_id": "customer",
            "user_id": "customer",
            "tenant_id": "owner",
            "channel": "wechat",
            "session_id": "owner",
        }
    ]


@pytest.mark.asyncio
async def test_contact_refresh_scheduler_deduplicates_same_contact(monkeypatch):
    from app.integrations.eyun.services import eyun_contact_service as service

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def refresh(**kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()

    monkeypatch.setattr(service, "refresh_eyun_contact", refresh, raising=False)

    first = service.schedule_eyun_contact_refresh(
        w_id="wid", wc_id="customer", user_id="customer"
    )
    second = service.schedule_eyun_contact_refresh(
        w_id="wid", wc_id="customer", user_id="customer"
    )
    await started.wait()

    assert first is second
    assert len(calls) == 1
    release.set()
    await first


@pytest.mark.asyncio
async def test_update_customer_identity_backfills_existing_conversation(
    monkeypatch, tmp_path
):
    from app.domains.conversations.services import conversation_service as service

    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'identity.db').as_posix()}"
    )
    get_settings.cache_clear()
    service._sessionmakers.clear()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    with service._get_session() as session:
        session.add(
            ConversationModel(
                conversation_id="wechat:customer:default",
                channel="wechat",
                user_id="customer",
                session_id="default",
                tenant_id="tenant_default",
                status="ai_waiting",
                unread_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    changed = await service.update_customer_identity(
        channel="wechat",
        user_id="customer",
        session_id="default",
        metadata={
            "nickname": "兰友张姐",
            "avatar_url": "https://example.com/avatar.jpg",
        },
    )

    assert changed is True
    with service._get_session() as session:
        row = session.query(ConversationModel).one()
        assert row.user_display_name == "兰友张姐"
        assert row.user_avatar_url == "https://example.com/avatar.jpg"

    get_settings.cache_clear()
    service._sessionmakers.clear()
