import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import get_settings
from app.domains.access import platform
from app.domains.access.api import admin_gate
from app.domains.access.permissions import can_access_conversation
from app.domains.conversations.services.conversation_service import _get_session
from app.infrastructure.database.models import ConversationModel, ConversationMessageModel
from app.main import app
from app.shared.schemas.common import AppError, ErrorCode


@pytest.fixture
def context(monkeypatch, tmp_path):
    for name, value in {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'accounts.db'}",
        "CHAT_LOG_DB_URL": f"sqlite:///{tmp_path / 'chat.db'}",
        "API_KEY": "service-only-key", "API_AUTH_ENABLED": "true",
        "PLATFORM_SHARED_SECRET": "test-shared-key-" + "x" * 32,
        "PLATFORM_APP_ORIGIN": "https://sales-agent.hzwohu.com", "EYUN_WC_ID": "", "EYUN_WID": "",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    with _get_session() as db:
        for owner in ("wx_allowed", "wx_denied"):
            cid = f"wechat:customer_{owner}:{owner}"
            db.add(ConversationModel(conversation_id=cid, channel="wechat", user_id=f"customer_{owner}", tenant_id=owner, owner_wc_id=owner, owner_display_name=owner, status="handoff_pending", created_at=now, updated_at=now))
            db.add(ConversationMessageModel(conversation_id=cid, sender_type="user", content=owner, created_at=now))
        db.add(ConversationModel(conversation_id="demo:customer", channel="demo", user_id="demo_customer", status="handoff_pending", created_at=now, updated_at=now))
        db.commit()
    account = {"id": "stable-platform-id", "username": "lanhua/stable-user-sub", "display_name": "员工甲", "enabled": True,
               "role": "employee", "pages": ["/workbench"], "all_wechat": False, "wechat_ids": ["wx_allowed"]}
    state = {"account": account, "revoked": False, "calls": []}

    async def remote(request, path, payload):
        state["calls"].append((path, payload))
        if path == "/service/logout":
            state["revoked"] = True
            return {"ok": True}
        if state["revoked"]:
            raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
        if path == "/service/exchange":
            return {"account": dict(account), "token": "t" * 43, "expires": time.time() + 3600}
        return {"account": dict(account)}

    monkeypatch.setattr(platform, "platform_request", remote)
    monkeypatch.setattr(admin_gate, "platform_request", remote)
    client = TestClient(app, base_url="https://sales-agent.hzwohu.com")
    client.cookies.set(platform.COOKIE_NAME, "t" * 43, domain="sales-agent.hzwohu.com", path="/")
    yield client, state
    get_settings.cache_clear()


def test_message_menu_actions_enforce_account_scope_and_operator_identity(context):
    client, state = context
    allowed = "wechat:customer_wx_allowed:wx_allowed"
    denied = "wechat:customer_wx_denied:wx_denied"
    with _get_session() as db:
        rows = db.query(ConversationMessageModel).all()
        ids = {row.conversation_id: row.id for row in rows}
    base = "/api/v1/admin/conversations/messages"
    assert client.post(f"{base}/{ids[denied]}/preference", json={"operator_id": "admin", "action": "favorite"}).status_code == 403
    assert client.post(f"{base}/{ids[denied]}/text", json={"action": "translate"}).status_code == 403
    assert client.get(f"/api/v1/admin/conversations/message-media/{ids[denied]}").status_code == 403
    assert client.post(f"{base}/{ids[allowed]}/preference", json={"operator_id": "spoofed-admin", "action": "favorite"}).status_code == 200
    message = client.get(f"/api/v1/admin/conversations/{allowed}").json()["data"]["messages"][0]
    assert message["is_favorite"] and "workbench_preferences" not in message["metadata"]
    assert client.get(f"/api/v1/admin/conversations/{denied}/favorites").status_code == 403
    favorites = client.get(f"/api/v1/admin/conversations/{allowed}/favorites", params={"operator_id": "spoofed-admin"}).json()["data"]
    assert [item["id"] for item in favorites["items"]] == [message["id"]]
    assert client.post(f"/api/v1/admin/conversations/{denied}/reply-video", data={"operator_id": "spoofed-admin"},
                       files={"file": ("video.mp4", b"denied", "video/mp4")}).status_code == 403
    with _get_session() as db:
        import json
        preferences = json.loads(db.get(ConversationMessageModel, ids[allowed]).metadata_json)["workbench_preferences"]
        assert set(preferences) == {state["account"]["username"]}


def test_old_credentials_and_account_management_removed(context):
    client, _ = context
    assert client.post("/api/gate", json={"username": "admin", "password": "old-password"}).status_code in (404, 405)
    assert client.get("/api/v1/admin/accounts").status_code == 404
    assert client.get("/api/v1/admin/eyun-settings").status_code == 404
    assert client.put("/api/v1/admin/eyun-settings", json={"w_id": "new-instance", "wc_id": "new-wechat"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/admin/conversations").status_code == 401
    client.cookies.set("admin_gate", "legacy-cookie")
    assert client.get("/api/v1/admin/conversations", headers={"Authorization": "Bearer service-only-key"}).status_code == 401


def test_library_api_has_scoped_identity_and_preserves_read_only(context, monkeypatch):
    client, state = context
    from app.domains.catalog.api import admin_products
    monkeypatch.setattr(admin_products, 'list_product_knowledge', lambda **kwargs: {'items': [{'id': 42}], 'total': 1})
    state['account']['pages'] = ['/operations/products']
    path = '/api/library/product-knowledge/v1/admin/products/knowledge'
    headers = {'X-Library-Session': 'l' * 43}
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers).json()['data']['items'] == [{'id': 42}]
    assert state['calls'][-1][0] == '/service/library-session'
    assert client.delete(path + '/42', headers=headers).status_code == 403
    assert client.get('/api/library/phrase-library/v1/admin/products/knowledge', headers=headers).status_code == 403
    assert client.get('/api/library/product-knowledge/v1/admin/conversations', headers=headers).status_code == 403
    assert client.get('/api/v1/admin/products/knowledge').json()['data']['items'] == [{'id': 42}]
    assert client.delete('/api/v1/admin/products/knowledge/42').status_code == 403
    state['account']['role'] = 'admin'
    assert client.get('/api/v1/admin/products/knowledge').status_code == 200
    state['revoked'] = True
    assert client.get(path, headers=headers).status_code == 401


def test_library_manual_selector_can_read_product_options_only(context, monkeypatch):
    client, state = context
    from app.domains.catalog.api import admin_products
    monkeypatch.setattr(admin_products, 'list_product_options', lambda: [{'item_id': 'product-1'}])
    state['account']['pages'] = ['/operations/care-manuals']
    headers = {'X-Library-Session': 'l' * 43}
    assert client.get('/api/library/product-knowledge/v1/admin/products/options', headers=headers).status_code == 200
    assert client.get('/api/library/product-knowledge/v1/admin/products/knowledge', headers=headers).status_code == 403
    assert client.get('/api/v1/admin/products/options').status_code == 200
    assert client.get('/api/v1/admin/products/knowledge').status_code == 403


def test_login_callback_and_browser_binding(context):
    client, state = context
    client.cookies.clear()
    start = client.get("/api/gate/login?redirect=/operations/tags", follow_redirects=False)
    assert start.status_code == 302
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["app"] == [platform.APP_ID] and len(query["challenge"][0]) == 43
    assert "HttpOnly" in start.headers["set-cookie"] and "Secure" in start.headers["set-cookie"]
    before = len(state["calls"])
    assert client.get("/api/gate/callback", params={"code": "c" * 43, "state": "wrong"}).status_code == 401
    assert len(state["calls"]) == before
    result = client.get("/api/gate/callback", params={"code": "c" * 43, "state": query["state"][0]}, follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == "/workbench"
    assert client.cookies.get(platform.COOKIE_NAME) == "t" * 43
    assert state["calls"][-1][0] == "/service/exchange"
    assert client.get("/api/gate/callback", params={"code": "c" * 43, "state": query["state"][0]}).status_code == 401
    assert admin_gate.safe_target("//evil.example") == "/workbench"
    assert admin_gate.safe_target("/\\evil.example") == "/workbench"


def test_employee_scope_empty_and_explicit_all(context):
    client, state = context
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 1
    assert {i["wc_id"] for i in client.get("/api/v1/admin/conversations/tenants").json()["data"]["items"]} == {"wx_allowed"}
    denied = "wechat:customer_wx_denied:wx_denied"
    assert client.get("/api/v1/admin/conversations/" + denied).status_code == 403
    state["account"]["wechat_ids"] = []
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 0
    assert client.get("/api/v1/admin/conversations/tenants").json()["data"]["items"] == []
    assert not can_access_conversation(state["account"], denied)
    state["account"]["all_wechat"] = True
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 2
    assert can_access_conversation(state["account"], denied)
    assert not can_access_conversation(state["account"], "demo:customer")


def test_permissions_and_identity_refresh_without_relogin(context):
    client, state = context
    assert client.get("/api/gate").json()["data"]["account"]["username"] == "lanhua/stable-user-sub"
    state["account"]["pages"] = ["/operations/tags"]
    assert client.get("/api/v1/admin/conversations").status_code == 403
    assert client.get("/api/gate").json()["data"]["account"]["pages"] == ["/operations/tags"]
    state["revoked"] = True
    assert client.get("/api/v1/admin/conversations", headers={"Authorization": "Bearer service-only-key"}).status_code == 401


def test_object_and_customer_scope(context):
    client, _ = context
    denied = "wechat:customer_wx_denied:wx_denied"
    assert client.post("/api/v1/admin/conversations/" + denied + "/claim", json={"operator_id": "admin"}).status_code == 403
    for action in ("hide", "pin", "unpin", "unread", "mute", "unmute"):
        assert client.post(
            f"/api/v1/admin/conversations/{denied}/list-action", json={"action": action}
        ).status_code == 403
    assert client.post(
        "/api/v1/admin/conversations/wechat:customer_wx_allowed:wx_allowed/list-action",
        json={"action": "pin"},
    ).status_code == 200
    assert client.get("/api/v1/users/customer_wx_denied/profile").status_code == 403
    assert client.get("/api/v1/admin/conversations", params={"test_only": True}).status_code == 403


def test_workbench_actions_check_source_and_target_permissions(context, monkeypatch):
    from app.integrations.youzan.services import youzan_product_sync_service

    client, _ = context
    monkeypatch.setattr(youzan_product_sync_service, "list_products", lambda **kwargs: {"items": [], "total": 0})
    assert client.get("/api/v1/admin/conversations/products").status_code == 200
    assert client.get("/api/v1/admin/products").status_code == 403
    with _get_session() as db:
        source = db.query(ConversationMessageModel).filter_by(content="wx_denied").one()
        source_id = source.id
    allowed = "/api/v1/admin/conversations/wechat:customer_wx_allowed:wx_allowed"
    payload = {"operator_id": "admin", "source_message_id": source_id, "content": "回复"}
    assert client.post(allowed + "/forward", json=payload).status_code == 403
    assert client.post(allowed + "/reply", json=payload).status_code == 403
    assert client.post(f"/api/v1/admin/conversations/messages/{source_id}/revoke", json=payload).status_code == 403
    assert client.post(f"/api/v1/admin/conversations/messages/{source_id}/transcribe").status_code == 403


def test_employee_can_only_edit_authorized_customer_tags_and_note(context):
    client, state = context
    path = "/api/v1/users/customer_wx_allowed/profile"
    response = client.patch(path, json={"customer_note": "周末联系", "customer_tags": ["浙江省"]})
    assert response.status_code == 200
    assert response.json()["data"]["profile"]["customer_note"] == "周末联系"
    assert client.get(path).json()["data"]["profile"]["customer_tags"] == ["浙江省"]
    assert client.patch("/api/v1/users/customer_wx_denied/profile", json={"customer_note": "不可修改"}).status_code == 403
    assert client.patch(path, json={"customer_note": "不可修改", "risk_level": "high"}).status_code == 403
    state["account"]["role"] = "test"
    assert client.patch(path, json={"customer_note": "不可修改"}).status_code == 403


def test_csrf_logout_and_stream_revocation(context):
    client, state = context
    assert client.delete("/api/gate", headers={"Origin": "https://untrusted.hzwohu.com"}).status_code == 403
    request = Request({"type": "http", "headers": [(b"cookie", (platform.COOKIE_NAME + "=" + "t" * 43).encode())]})
    assert asyncio.run(platform.stream_account(request))["id"] == "stable-platform-id"
    assert client.delete("/api/gate").status_code == 200
    assert asyncio.run(platform.stream_account(request)) is None
    assert client.cookies.get(platform.COOKIE_NAME) is None


def test_unavailable_platform_fails_closed(context, monkeypatch):
    client, _ = context
    async def unavailable(*args, **kwargs):
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务不可用", status_code=503)
    monkeypatch.setattr(platform, "platform_request", unavailable)
    assert client.get("/api/gate").status_code == 503
    assert client.get("/api/v1/admin/conversations", headers={"Authorization": "Bearer service-only-key"}).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/service/session", "/service/library-session"])
@pytest.mark.parametrize("failure", ["disconnect", "timeout", 502, 503, 504])
async def test_session_check_recovers_transient_failure(monkeypatch, path, failure):
    monkeypatch.setattr(get_settings(), "platform_shared_secret", "x" * 32)
    requests = []
    account = {"id": "employee", "wechat_ids": ["wx_allowed"]}

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            if failure == "disconnect":
                raise httpx.RemoteProtocolError("connection closed", request=request)
            if failure == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(failure)
        return httpx.Response(200, json={"account": account})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(platform_client=client))})
        result = await platform.platform_request(request, path, {"token": "t" * 43})

    assert result == {"account": account}
    assert len(requests) == 2
    assert requests[0].content == requests[1].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status,attempts,expected_status",
    [
        ("/service/session", 503, 2, 503),
        ("/service/session", 401, 1, 401),
        ("/service/session", 403, 1, 403),
        ("/service/session", 500, 1, 503),
        ("/service/exchange", 503, 1, 503),
        ("/service/logout", 503, 1, 503),
    ],
)
async def test_platform_retry_remains_bounded_and_fails_closed(
    monkeypatch, caplog, path, status, attempts, expected_status
):
    secret, token = "private-app-key-" + "x" * 32, "private-session-token-" + "t" * 43
    monkeypatch.setattr(get_settings(), "platform_shared_secret", secret)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, text=token)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(platform_client=client))})
        with pytest.raises(AppError) as error:
            await platform.platform_request(request, path, {"token": token})

    assert error.value.status_code == expected_status
    assert len(requests) == attempts
    assert secret not in caplog.text and token not in caplog.text


@pytest.mark.asyncio
async def test_retried_permission_check_does_not_cache_account(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_shared_secret", "x" * 32)
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        if len(requests) == 2:
            return httpx.Response(200, json={"account": {"id": "employee"}})
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(platform_client=client))})
        assert await platform.platform_request(request, "/service/session", {"token": "t" * 43})
        with pytest.raises(AppError) as error:
            await platform.platform_request(request, "/service/session", {"token": "t" * 43})

    assert error.value.status_code == 401
    assert len(requests) == 3
