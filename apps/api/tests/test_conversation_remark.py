import asyncio

import pytest
from sqlalchemy import func, inspect, select, text

from app.core.config import get_settings
from app.domains.conversations.services import conversation_service as service
from app.infrastructure.database.models import ConversationMessageModel, ConversationModel


@pytest.fixture(autouse=True)
def remark_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EYUN_BASE_URL", "")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def inbound(*, user="customer", owner="shop_a", session_id="one", name="微信原名称"):
    return await service.record_customer_message(
        channel="wechat", user_id=user, session_id=session_id, content="最近消息",
        status="ai_waiting", metadata={"provider": "eyun", "owner_wc_id": owner, "nickname": name},
    )


@pytest.mark.asyncio
async def test_remark_persists_across_group_refresh_and_new_sessions():
    first = await inbound()
    second = await inbound(session_id="two")
    before = await service.get_conversation_detail(first["conversation_id"])

    updated = await service.set_conversation_remark(first["conversation_id"], "  南宁张老师  ")

    assert updated["user_display_name"] == updated["customer_remark"] == "南宁张老师"
    assert updated["updated_at"] == before["conversation"]["updated_at"]
    assert updated["unread_count"] == before["conversation"]["unread_count"]
    assert updated["status"] == before["conversation"]["status"]
    detail = await service.get_conversation_detail(second["conversation_id"])
    assert detail["conversation"]["customer_remark"] == "南宁张老师"
    assert await service.update_customer_identity(
        channel="wechat", user_id="customer", session_id="one", metadata={"nickname": "微信新名称"},
    )
    assert (await service.get_conversation_detail(first["conversation_id"]))["conversation"]["user_display_name"] == "南宁张老师"
    third = await inbound(session_id="three", name="微信新名称")
    assert third["customer_remark"] == third["user_display_name"] == "南宁张老师"
    assert (await service.list_conversations(keyword="南宁张老师"))["total"] == 3

    cleared = await service.set_conversation_remark(first["conversation_id"], " \n ")
    assert cleared["customer_remark"] is None
    assert cleared["user_display_name"] == "微信新名称"
    assert (await service.list_conversations(keyword="南宁张老师"))["total"] == 0
    assert (await inbound(session_id="four"))["customer_remark"] is None


@pytest.mark.asyncio
async def test_remark_is_scoped_by_account_tenant_channel_and_customer():
    selected = await inbound()
    other_account = await inbound(owner="shop_b", session_id="other")
    other_customer = await inbound(user="another")
    other_channel = await service.record_customer_message(
        channel="api", user_id="customer", session_id="web", tenant_id="shop_a",
        content="网页消息", status="ai_waiting",
    )
    await service.set_conversation_remark(selected["conversation_id"], "本账号备注")
    for row in (other_account, other_customer, other_channel):
        detail = await service.get_conversation_detail(row["conversation_id"])
        assert detail["conversation"]["customer_remark"] is None
    with service._get_session() as session:
        row = session.query(ConversationModel).filter_by(conversation_id=selected["conversation_id"]).one()
        assert row.user_display_name == "微信原名称"


def test_remark_schema_upgrade_is_idempotent_and_preserves_existing_chat():
    original = asyncio.run(inbound())
    with service._get_session() as session:
        session.execute(text("ALTER TABLE conversations DROP COLUMN customer_remark"))
        session.commit()
    service._initialized_urls.discard(get_settings().chat_log_db_url)
    with service._get_session() as session:
        assert "customer_remark" in {column["name"] for column in inspect(session.get_bind()).get_columns("conversations")}
        assert session.query(ConversationModel).filter_by(conversation_id=original["conversation_id"]).one().user_display_name == "微信原名称"
        assert session.scalar(select(func.count()).select_from(ConversationMessageModel)) == 1
    detail = asyncio.run(service.get_conversation_detail(original["conversation_id"]))
    assert detail["conversation"]["customer_remark"] is None
    with service._get_session():
        pass


def test_remark_api_rejects_invalid_or_missing_conversations():
    from fastapi.testclient import TestClient
    from app.main import app

    first = asyncio.run(inbound(user="group@chatroom"))
    client = TestClient(app)
    url = f"/api/v1/admin/conversations/{first['conversation_id']}/remark"
    response = client.put(url, json={"remark": "陪伴养兰群"})
    assert response.status_code == 200
    assert response.json()["data"]["customer_remark"] == "陪伴养兰群"
    assert client.put(url, json={"remark": "长" * 257}).status_code == 422
    assert client.put(url, json={}).status_code == 422
    assert client.put("/api/v1/admin/conversations/missing/remark", json={"remark": "备注"}).status_code == 404
    assert client.put(url, json={"remark": ""}).json()["data"]["customer_remark"] is None


def test_employee_can_only_edit_assigned_conversations(monkeypatch):
    from fastapi.testclient import TestClient
    from app.domains.access import platform
    from app.domains.access.api import admin_gate
    from app.main import app

    first = asyncio.run(inbound())
    denied = asyncio.run(inbound(owner="shop_b", session_id="other"))
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("PLATFORM_SHARED_SECRET", "test-shared-" + "x" * 32)
    get_settings.cache_clear()
    account = {
        "id": "remark-employee", "username": "remark-employee", "display_name": "客服",
        "enabled": True, "role": "employee", "pages": ["/workbench"],
        "all_wechat": False, "wechat_ids": ["shop_a"],
    }

    async def remote(*args, **kwargs):
        return {"account": account}

    monkeypatch.setattr(platform, "platform_request", remote)
    monkeypatch.setattr(admin_gate, "platform_request", remote)
    client = TestClient(app, base_url="https://sales-agent.hzwohu.com")
    client.cookies.set(platform.COOKIE_NAME, "t" * 43, domain="sales-agent.hzwohu.com", path="/")
    url = f"/api/v1/admin/conversations/{first['conversation_id']}/remark"
    assert client.put(url, json={"remark": "张老师"}).status_code == 200
    assert client.put(f"/api/v1/admin/conversations/{denied['conversation_id']}/remark", json={"remark": "越权"}).status_code == 403
    account["wechat_ids"] = []
    assert client.put(url, json={"remark": "越权"}).status_code == 403
    account["wechat_ids"] = ["shop_a"]
    account["role"] = "test"
    assert client.put(url, json={"remark": "越权"}).status_code == 403
