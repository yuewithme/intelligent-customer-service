import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.domains.access.accounts import Account, LoginSession, account_session, session_account
from app.domains.conversations.services.conversation_service import _get_session
from app.infrastructure.database.models import ConversationModel, ConversationMessageModel
from app.main import app


@pytest.fixture
def clients(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'accounts.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("ADMIN_GATE_PASSWORD", "initial-admin-password")
    monkeypatch.setenv("ADMIN_GATE_TEST_PASSWORD", "initial-test-password")
    monkeypatch.setenv("API_KEY", "service-only-key")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("EYUN_WC_ID", "")
    monkeypatch.setenv("EYUN_WID", "")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    with _get_session() as db:
        for owner in ("wx_allowed", "wx_denied"):
            cid = f"wechat:customer_{owner}:{owner}"
            db.add(ConversationModel(conversation_id=cid, channel="wechat", user_id=f"customer_{owner}", tenant_id=owner, owner_wc_id=owner, owner_display_name=owner, status="handoff_pending", created_at=now, updated_at=now))
            db.add(ConversationMessageModel(conversation_id=cid, sender_type="user", content=owner, created_at=now))
        db.commit()
    admin = TestClient(app)
    assert admin.post("/api/gate", json={"username": "admin", "password": "initial-admin-password"}).status_code == 200
    yield admin, TestClient(app)
    get_settings.cache_clear()


def create_employee(admin, client, pages=None, wechats=None):
    payload = {"username": "employee", "display_name": "员工甲", "pages": pages if pages is not None else ["/workbench"], "wechat_ids": wechats if wechats is not None else ["wx_allowed"]}
    result = admin.post("/api/v1/admin/accounts", json=payload)
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert client.post("/api/gate", json={"username": "employee", "password": data["password"]}).status_code == 200
    return data


def test_username_required_legacy_cookie_rejected_and_no_proxy_bypass(clients):
    admin, client = clients
    assert client.post("/api/gate", json={"password": "initial-admin-password"}).status_code == 422
    assert client.get("/api/v1/admin/conversations").status_code == 401
    client.cookies.set("admin_gate", "admin.invalid-old-cookie")
    assert client.get("/api/v1/admin/conversations", headers={"Authorization": "Bearer service-only-key"}).status_code == 401
    assert admin.get("/api/gate").json()["data"]["account"]["username"] == "admin"


def test_credentials_are_hashed_and_sessions_expire_and_logout_revokes(clients):
    admin, client = clients
    created = create_employee(admin, client)
    token = client.cookies.get("admin_gate")
    with account_session() as db:
        row = db.get(Account, created["account"]["id"])
        assert row.password_hash.startswith("scrypt$")
        assert created["password"] not in row.password_hash
        assert all(s.token_hash != token for s in db.scalars(select(LoginSession)))
    client.delete("/api/gate")
    assert session_account(token) is None
    assert client.post("/api/gate", json={"username": "employee", "password": created["password"]}).status_code == 200
    with account_session() as db:
        for row in db.scalars(select(LoginSession).where(LoginSession.account_id == created["account"]["id"])):
            row.expires_at = time.time() - 1
        db.commit()
    assert client.get("/api/v1/admin/conversations").status_code == 401


def test_employee_lists_only_assigned_wechat_and_empty_scope_is_empty(clients):
    admin, employee = clients
    created = create_employee(admin, employee)
    result = employee.get("/api/v1/admin/conversations").json()["data"]
    assert result["total"] == 1
    assert {item["owner_wc_id"] for item in result["items"]} == {"wx_allowed"}
    assert {item["wc_id"] for item in employee.get("/api/v1/admin/conversations/tenants").json()["data"]["items"]} == {"wx_allowed"}
    assert employee.get("/api/v1/admin/conversations", params={"tenant_id": "wx_denied"}).json()["data"]["total"] == 0
    payload = {**created["account"], "wechat_ids": []}
    assert admin.put(f'/api/v1/admin/accounts/{payload["id"]}', json=payload).status_code == 200
    assert employee.get("/api/v1/admin/conversations").json()["data"]["total"] == 0


def test_employee_object_scope_and_forged_source_messages(clients):
    admin, employee = clients
    create_employee(admin, employee)
    allowed = "wechat:customer_wx_allowed:wx_allowed"
    denied = "wechat:customer_wx_denied:wx_denied"
    base = "/api/v1/admin/conversations/"
    assert employee.get(base + allowed).status_code == 200
    assert employee.get(base + denied).status_code == 403
    assert employee.post(base + denied + "/claim", json={"operator_id": "admin"}).status_code == 403
    result = employee.post(base + allowed + "/claim", json={"operator_id": "forged-admin"})
    assert result.status_code == 200, result.text
    with _get_session() as db:
        assert db.scalar(select(ConversationModel.owner_id).where(ConversationModel.conversation_id == allowed)) == "employee"
        message_id = db.scalar(select(ConversationMessageModel.id).where(ConversationMessageModel.conversation_id == denied))
    assert employee.get(base + f"message-media/{message_id}").status_code == 403
    assert employee.post(base + f"messages/{message_id}/retry-delivery").status_code == 403
    assert employee.post(base + allowed + "/reply-emoji", json={"operator_id": "employee", "source_message_id": message_id}).status_code == 403
    assert employee.get("/api/v1/users/customer_wx_denied/profile").status_code == 403
    assert employee.get(base + "message-recognition-stats").status_code == 403
    assert employee.get(base, params={"test_only": True}).status_code == 403


def test_page_permissions_admin_management_and_permission_change(clients):
    admin, employee = clients
    created = create_employee(admin, employee, pages=["/operations/tags"])
    assert employee.get("/api/v1/admin/tags").status_code == 200
    assert employee.get("/api/v1/admin/products").status_code == 403
    assert employee.get("/api/v1/admin/conversations").status_code == 403
    assert employee.get("/api/v1/admin/accounts").status_code == 403
    assert employee.get("/api/v1/admin/accounts/options").status_code == 403
    assert employee.post("/api/v1/admin/accounts", json={"username": "intruder", "display_name": "x"}).status_code == 403
    assert employee.post("/api/v1/admin/tags/categories", json={}).status_code == 403
    assert employee.get("/api/v1/demo-admin/conversations").status_code == 403
    payload = {**created["account"], "pages": []}
    assert admin.put(f'/api/v1/admin/accounts/{payload["id"]}', json=payload).status_code == 200
    assert employee.get("/api/v1/admin/tags").status_code == 403
    assert employee.get("/api/gate").json()["data"]["account"]["pages"] == []


def test_password_reset_disable_and_bootstrap_never_overwrites(clients):
    admin, employee = clients
    created = create_employee(admin, employee)
    account_id = created["account"]["id"]
    reset = admin.post(f"/api/v1/admin/accounts/{account_id}/password", json={})
    assert reset.status_code == 200
    assert employee.get("/api/v1/admin/conversations").status_code == 401
    assert employee.post("/api/gate", json={"username": "employee", "password": created["password"]}).status_code == 401
    assert employee.post("/api/gate", json={"username": "employee", "password": reset.json()["data"]["password"]}).status_code == 200
    assert admin.put(f"/api/v1/admin/accounts/{account_id}", json={**created["account"], "enabled": False}).status_code == 200
    assert employee.get("/api/v1/admin/conversations").status_code == 401
    assert employee.post("/api/gate", json={"username": "employee", "password": reset.json()["data"]["password"]}).status_code == 401
    admin_id = admin.get("/api/gate").json()["data"]["account"]["id"]
    reset = admin.post(f"/api/v1/admin/accounts/{admin_id}/password", json={})
    from app.domains.access.accounts import _factory
    _factory.cache_clear()
    assert admin.post("/api/gate", json={"username": "admin", "password": "initial-admin-password"}).status_code == 401
    assert admin.post("/api/gate", json={"username": "admin", "password": reset.json()["data"]["password"]}).status_code == 200


def test_test_role_retains_demo_and_readonly_permissions(clients):
    _, tester = clients
    assert tester.post("/api/gate", json={"username": "test", "password": "initial-test-password"}).status_code == 200
    assert tester.get("/api/v1/demo-admin/conversations").status_code == 200
    assert tester.get("/api/v1/admin/conversations").status_code == 403
    assert tester.get("/api/v1/admin/activities").status_code == 200
    assert tester.post("/api/v1/admin/activities/1/archive", json={"operator_id": "test"}).status_code == 403
    assert tester.get("/api/v1/admin/accounts").status_code == 403


def test_invalid_account_permissions_duplicate_and_self_lockout(clients):
    admin, employee = clients
    created = create_employee(admin, employee)
    assert admin.post("/api/v1/admin/accounts", json={"username": "EMPLOYEE", "display_name": "重复"}).status_code == 409
    assert admin.post("/api/v1/admin/accounts", json={"username": "other", "display_name": "其他", "pages": ["/settings/accounts"]}).status_code == 422
    assert admin.post("/api/v1/admin/accounts", json={"username": "other", "display_name": "其他", "wechat_ids": ["invented"]}).status_code == 400
    me = admin.get("/api/gate").json()["data"]["account"]
    assert admin.put(f'/api/v1/admin/accounts/{me["id"]}', json={**me, "enabled": False}).status_code == 400
    assert admin.put(f'/api/v1/admin/accounts/{me["id"]}', json={**me, "role": "employee"}).status_code == 400
    assert "password_hash" not in admin.get("/api/v1/admin/accounts").text


def test_login_throttling(clients):
    _, client = clients
    for _ in range(10):
        assert client.post("/api/gate", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/gate", json={"username": "admin", "password": "initial-admin-password"}).status_code == 429


@pytest.mark.asyncio
async def test_live_events_filter_other_wechat_and_stop_after_revocation(clients):
    from fastapi import Request
    from app.domains.access.accounts import revoke_sessions
    from app.domains.conversations.api.admin_conversations import conversation_events
    from app.domains.conversations.services.conversation_event_service import conversation_event_broker

    admin, employee = clients
    created = create_employee(admin, employee)
    token = employee.cookies.get("admin_gate")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "headers": [(b"cookie", f"admin_gate={token}".encode())]}, receive)
    stream = (await conversation_events(request)).body_iterator
    assert "connected" in await anext(stream)
    conversation_event_broker.publish({"conversation_id": "wechat:customer_wx_denied:wx_denied"})
    conversation_event_broker.publish({"conversation_id": "wechat:customer_wx_allowed:wx_allowed"})
    event = await anext(stream)
    assert "wx_allowed" in event and "wx_denied" not in event
    with account_session() as db:
        revoke_sessions(db, created["account"]["id"])
        db.commit()
    conversation_event_broker.publish({"conversation_id": "wechat:customer_wx_allowed:wx_allowed"})
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert conversation_event_broker.subscriber_count == 0


def test_shared_customer_profile_does_not_leak_other_wechat(clients):
    admin, employee = clients
    create_employee(admin, employee)
    with _get_session() as db:
        conversation = db.scalar(select(ConversationModel).where(ConversationModel.owner_wc_id == "wx_denied"))
        conversation.user_id = "customer_wx_allowed"
        db.commit()
    assert employee.get("/api/v1/users/customer_wx_allowed/profile").status_code == 403
    assert employee.get("/api/v1/admin/conversations/wechat:customer_wx_allowed:wx_allowed/orders").status_code == 403


@pytest.mark.asyncio
async def test_demo_events_stop_when_account_is_disabled(clients):
    from fastapi import Request
    from app.domains.conversations.api.demo_admin import conversation_events
    from app.domains.conversations.services.conversation_event_service import conversation_event_broker

    admin, tester = clients
    tester.post("/api/gate", json={"username": "test", "password": "initial-test-password"})
    account = tester.get("/api/gate").json()["data"]["account"]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "headers": [(b"cookie", f'admin_gate={tester.cookies.get("admin_gate")}'.encode())]}, receive)
    stream = (await conversation_events(request)).body_iterator
    assert "connected" in await anext(stream)
    assert admin.put(f'/api/v1/admin/accounts/{account["id"]}', json={**account, "enabled": False}).status_code == 200
    conversation_event_broker.publish({"conversation_id": "web_demo:demo:customer:default"})
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
