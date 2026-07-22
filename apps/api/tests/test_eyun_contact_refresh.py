import asyncio
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import ConversationModel


@pytest.mark.asyncio
async def test_initialize_contacts_uses_official_endpoint(monkeypatch):
    from app.services import eyun_contact_service as service

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
    from app.services import eyun_contact_service as service

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
        calls.append("getContact")
        return snapshot

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
        "initAddressList",
        "sleep",
        "getContact",
        "profile",
        "conversation",
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_empty_callback_schedules_contact_refresh(monkeypatch):
    from app.services import eyun_callback_service as service

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
            "tenant_id": "tenant_default",
            "channel": "wechat",
            "session_id": "default",
        }
    ]


@pytest.mark.asyncio
async def test_contact_refresh_scheduler_deduplicates_same_contact(monkeypatch):
    from app.services import eyun_contact_service as service

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
    from app.services import conversation_service as service

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
