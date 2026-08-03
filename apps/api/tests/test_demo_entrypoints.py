import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.domains.decisioning.services.demo_sales_agent_service import demo_user_id


@pytest.fixture(autouse=True)
def _clear_settings_after_test():
    yield
    get_settings.cache_clear()


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    database_path = tmp_path / "demo.db"
    log_path = tmp_path / "demo-logs.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{log_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "api-key")
    monkeypatch.setenv("MCP_API_KEY", "mcp-key")
    monkeypatch.setenv("EVALUATION_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "false")
    monkeypatch.setenv("STATE_PROVIDER", "memory")
    get_settings.cache_clear()


def test_demo_customer_ids_remain_distinct_for_non_ascii_names():
    assert demo_user_id("张女士") != demo_user_id("李女士")


def test_demo_chat_forces_web_demo_channel(monkeypatch, tmp_path):
    from app.routers import demo

    _reset_settings(monkeypatch, tmp_path)

    async def fake_chat(**kwargs):
        assert kwargs == {
            "channel": "web_demo",
            "customer_id": "customer-1",
            "conversation_id": None,
            "message": "我是新手",
            "customer_name": "测试客户",
        }
        return {
            "reply": "欢迎咨询",
            "customer_id": "customer-1",
            "conversation_id": "session-1",
            "sales_stage": "interest",
            "customer_tags": ["新手"],
            "product_interests": [],
            "next_action": "了解预算",
            "need_human": False,
            "route": "rag_answer",
            "trace_id": "trace-1",
        }

    monkeypatch.setattr(demo, "chat_with_demo_sales_agent", fake_chat)
    client = TestClient(app)
    response = client.post(
        "/api/v1/demo/chat",
        json={
            "customer_id": "customer-1",
            "customer_name": "测试客户",
            "message": "我是新手",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["conversation_id"] == "session-1"


def test_demo_opening_uses_wechat_opening_and_records_conversation(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("EYUN_OPENING_TEXT", "正式微信新联系人开场白")
    monkeypatch.setenv(
        "EYUN_OPENING_IMAGE_URL", "https://example.com/opening.jpg"
    )
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "customer-opening", "customer_name": "测试客户"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reply"] == "正式微信新联系人开场白"
    assert data["opening_image_url"] == "https://example.com/opening.jpg"
    assert data["route"] == "opening"
    assert data["conversation_id"] == "default"
    assert [item["type"] for item in data["outbound_messages"]] == [
        "text",
        "image",
    ]

    conversations = client.get("/api/v1/demo-admin/conversations").json()["data"]
    assert conversations["total"] == 1
    conversation_id = conversations["items"][0]["conversation_id"]
    detail = client.get(
        f"/api/v1/demo-admin/conversations/{conversation_id}"
    ).json()["data"]
    assert detail["conversation"]["channel"] == "wechat"
    assert detail["conversation"]["user_display_name"] == "测试客户"
    assert [(item["sender_type"], item["content"]) for item in detail["messages"]] == [
        ("ai", "正式微信新联系人开场白"),
        ("ai", "[图片]"),
    ]


def test_demo_session_restores_shared_history_across_clients(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    first_client = TestClient(app)
    opened = first_client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "persistent-customer", "customer_name": "Persistent"},
    )
    conversation_id = opened.json()["data"]["conversation_id"]
    sent = first_client.post(
        "/api/v1/demo/chat",
        json={
            "customer_id": "persistent-customer",
            "customer_name": "Persistent",
            "conversation_id": conversation_id,
            "message": "请记住这条消息",
        },
    )

    second_client = TestClient(app)
    restored = second_client.get("/api/v1/demo/session")
    data = restored.json()["data"]

    assert sent.status_code == 200
    assert restored.status_code == 200
    assert data["customer_id"] == "persistent-customer"
    assert data["conversation_id"] == conversation_id
    assert any(
        item["role"] == "customer" and item["content"] == "请记住这条消息"
        for item in data["messages"]
    )
    assert any(item["role"] == "agent" for item in data["messages"])


def test_demo_session_restores_opening_image_as_media(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("EYUN_OPENING_TEXT", "开场白")
    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "https://cdn.example.com/opening.jpg")
    get_settings.cache_clear()
    client = TestClient(app)

    client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "media-customer", "customer_name": "Media"},
    )
    messages = client.get("/api/v1/demo/session").json()["data"]["messages"]

    assert [item["type"] for item in messages] == ["text", "image"]
    assert messages[1]["content"] == "https://cdn.example.com/opening.jpg"
    assert messages[1]["media"] == {
        "type": "image",
        "url": "https://cdn.example.com/opening.jpg",
        "fallback": False,
    }


def test_demo_history_normalizes_cards_and_customer_media():
    from app.domains.decisioning.services.demo_sales_agent_service import (
        _demo_history_messages,
    )

    card = {
        "title": "兰花套餐",
        "url": "https://example.com/product",
        "thumb_url": "https://cdn.example.com/product.jpg",
    }
    messages = _demo_history_messages(
        [
            {
                "id": 1,
                "sender_type": "ai",
                "content": "[链接卡片] 兰花套餐",
                "metadata": {"message_type": "60001", "link_card": card},
            },
            {
                "id": 2,
                "sender_type": "customer",
                "content": "[图片]",
                "metadata": {
                    "message_type": "60002",
                    "media": {
                        "type": "image",
                        "url": "https://cdn.example.com/customer.jpg",
                    },
                },
            },
        ]
    )

    assert messages[0]["type"] == "link_card"
    assert json.loads(messages[0]["content"]) == card
    assert messages[0]["card"] == card
    assert messages[1]["role"] == "customer"
    assert messages[1]["type"] == "image"
    assert messages[1]["content"] == "https://cdn.example.com/customer.jpg"


def test_demo_session_changes_only_through_explicit_switch(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "customer-a", "customer_name": "Customer A"},
    )

    stale_send = client.post(
        "/api/v1/demo/chat",
        json={"customer_id": "customer-b", "message": "should not switch"},
    )
    still_active = client.get("/api/v1/demo/session").json()["data"]
    switched = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "customer-b", "customer_name": "Customer B"},
    )
    now_active = client.get("/api/v1/demo/session").json()["data"]

    assert stale_send.status_code == 409
    assert still_active["customer_id"] == "customer-a"
    assert switched.status_code == 200
    assert now_active["customer_id"] == "customer-b"


def test_demo_openings_use_customer_scoped_memory_ids(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("MEMORY_V2_WRITE_ENABLED", "true")
    monkeypatch.setenv("MEMORY_V2_LLM_EXTRACTION_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)

    first = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "memory-customer-a"},
    )
    second = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "memory-customer-b"},
    )

    assert first.status_code == 200
    assert second.status_code == 200


def test_demo_chat_reuses_normal_sales_flow_for_first_message(monkeypatch, tmp_path):
    from app.services import demo_sales_agent_service

    _reset_settings(monkeypatch, tmp_path)
    captured = {}

    async def fake_handle_chat(request):
        captured["request"] = request
        return {
            "answer": "正常销售链路回复",
            "session_id": "session-1",
            "sources": [],
            "usage": {},
            "reply_type": "text",
            "route": "rag_answer",
            "intent": {"sales_stage": "interest"},
            "template": {},
            "need_human": False,
            "next_action": "继续了解需求",
            "trace_id": "trace-1",
            "metadata": {},
        }

    monkeypatch.setattr(demo_sales_agent_service, "handle_chat", fake_handle_chat)
    client = TestClient(app)
    response = client.post(
        "/api/v1/demo/chat",
        json={"customer_id": "customer-1", "message": "我的兰花烂根了"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["reply"] == "正常销售链路回复"
    assert response.json()["data"]["route"] == "rag_answer"
    assert captured["request"].channel == "wechat"
    assert captured["request"].message == "我的兰花烂根了"
    assert captured["request"].session_id is None
    assert captured["request"].metadata["provider"] == "eyun"
    assert captured["request"].metadata["message_type"] == "60001"
    assert captured["request"].metadata["provider_delivery_mode"] == "simulated"
    assert captured["request"].metadata["prepend_opening"] is True
    assert "demo" not in captured["request"].metadata


def test_demo_chat_allows_question_marks_in_normal_text(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    first = client.post(
        "/api/v1/demo/chat",
        json={"customer_id": "valid-customer", "message": "这个怎么用??"},
    )
    conversation_id = first.json()["data"]["conversation_id"]
    second = client.post(
        "/api/v1/demo/chat",
        json={
            "customer_id": "valid-customer",
            "conversation_id": conversation_id,
            "message": "还有别的吗?",
        },
    )
    detail = client.get(
        f"/api/v1/demo-admin/conversations/wechat:{demo_user_id('valid-customer')}:{conversation_id}"
    ).json()["data"]
    customer_messages = [
        item["content"]
        for item in detail["messages"]
        if item["sender_type"] == "customer"
    ]

    assert first.status_code == 200
    assert second.status_code == 200
    assert customer_messages == ["这个怎么用??", "还有别的吗?"]


def test_demo_admin_only_lists_demo_conversations(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    formal = client.post(
        "/api/v1/chat",
        json={
            "channel": "wechat",
            "user_id": "real-user",
            "session_id": "default",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    demo = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "test-user", "customer_name": "Test customer"},
    )
    assert formal.status_code == 200
    assert demo.status_code == 200

    result = client.get("/api/v1/demo-admin/conversations")
    forbidden = client.get(
        "/api/v1/demo-admin/conversations/wechat:real-user:default"
    )
    demo_user = result.json()["data"]["items"][0]["user_id"]
    profile = client.get(f"/api/v1/demo-admin/users/{demo_user}/profile")
    forbidden_profile = client.get(
        "/api/v1/demo-admin/users/real-user/profile"
    )

    assert result.status_code == 200
    items = result.json()["data"]["items"]
    assert [item["channel"] for item in items] == ["wechat"]
    assert forbidden.status_code == 403
    assert profile.status_code == 200
    assert profile.json()["data"]["profile"]["user_id"] == demo_user
    assert forbidden_profile.status_code == 403

    formal_items = client.get(
        "/api/v1/admin/conversations?channel=wechat"
    ).json()["data"]["items"]
    admin_test_items = client.get(
        "/api/v1/admin/conversations?test_only=true"
    ).json()["data"]["items"]
    assert [item["user_id"] for item in formal_items] == ["real-user"]
    assert [item["user_id"] for item in admin_test_items] == [demo_user]


def test_mcp_streamable_http_lists_sales_agent_tool(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    monkeypatch.setenv("EVALUATION_MODE", "false")
    get_settings.cache_clear()
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    headers = {
        "Authorization": "Bearer mcp-key",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app) as client:
        unauthorized = client.post("/mcp", json=initialize, headers={"Accept": headers["Accept"]})
        initialized = client.post("/mcp", json=initialize, headers=headers)
        tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "Sales Agent Demo"
    assert [tool["name"] for tool in tools.json()["result"]["tools"]] == [
        "chat_with_sales_agent",
        "youzan_search_products",
        "youzan_get_product",
        "youzan_list_inventory",
        "youzan_resolve_customer",
        "youzan_search_customer_orders",
        "youzan_get_customer_order",
    ]


def test_api_does_not_serve_frontend_routes(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    assert client.get("/demo-chat").status_code == 404
    assert client.get("/workbench").status_code == 404
    assert client.get("/gate").status_code == 404
