import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.demo_sales_agent_service import demo_user_id


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
    monkeypatch.delenv("DEMO_UPSTREAM_BASE_URL", raising=False)
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
    assert data["conversation_id"].startswith("sess_")

    conversations = client.get("/api/v1/demo-admin/conversations").json()["data"]
    assert conversations["total"] == 1
    conversation_id = conversations["items"][0]["conversation_id"]
    detail = client.get(
        f"/api/v1/demo-admin/conversations/{conversation_id}"
    ).json()["data"]
    assert detail["conversation"]["user_display_name"] == "测试客户"
    assert [(item["sender_type"], item["content"]) for item in detail["messages"]] == [
        ("ai", "正式微信新联系人开场白")
    ]


def test_demo_opening_can_proxy_to_official_backend(monkeypatch, tmp_path):
    from app.routers import demo

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("DEMO_UPSTREAM_BASE_URL", "http://official-backend")
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "reply": "正式开场白",
                    "customer_id": "customer-1",
                    "conversation_id": "session-1",
                },
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(demo.httpx, "AsyncClient", FakeClient)
    response = TestClient(app).post(
        "/api/v1/demo/opening",
        json={"customer_id": "customer-1", "customer_name": "测试客户"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["reply"] == "正式开场白"
    assert captured["url"] == "http://official-backend/api/v1/demo/opening"
    assert captured["json"] == {
        "customer_id": "customer-1",
        "customer_name": "测试客户",
    }


def test_demo_chat_can_proxy_to_official_backend(monkeypatch, tmp_path):
    from app.routers import demo

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("DEMO_UPSTREAM_BASE_URL", "http://official-backend")
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "reply": "正式后台回复",
                    "customer_id": "customer-1",
                    "conversation_id": "session-1",
                },
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(demo.httpx, "AsyncClient", FakeClient)
    response = TestClient(app).post(
        "/api/v1/demo/chat",
        json={
            "customer_id": "customer-1",
            "customer_name": "测试客户",
            "message": "我的兰花烂根了",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["reply"] == "正式后台回复"
    assert captured["url"] == "http://official-backend/api/v1/demo/chat"
    assert captured["json"] == {
        "customer_id": "customer-1",
        "customer_name": "测试客户",
        "message": "我的兰花烂根了",
    }


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
    assert captured["request"].message == "我的兰花烂根了"
    assert captured["request"].session_id is None


def test_demo_chat_allows_question_marks_in_normal_text(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/demo/chat",
        json={"customer_id": "valid-customer", "message": "这个怎么用??"},
    )

    assert response.status_code == 200


def test_demo_admin_only_lists_demo_conversations(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    for channel, user_id in (("api", "real-user"), ("web_demo", "demo:user")):
        response = client.post(
            "/api/v1/chat",
            json={
                "channel": channel,
                "user_id": user_id,
                "session_id": "session-1",
                "message": "你好",
                "kb_id": "kb_default",
                "metadata": {},
            },
        )
        assert response.status_code == 200

    result = client.get("/api/v1/demo-admin/conversations")
    forbidden = client.get(
        "/api/v1/demo-admin/conversations/api:real-user:session-1"
    )
    demo_user = result.json()["data"]["items"][0]["user_id"]
    profile = client.get(f"/api/v1/demo-admin/users/{demo_user}/profile")
    forbidden_profile = client.get(
        "/api/v1/demo-admin/users/real-user/profile"
    )

    assert result.status_code == 200
    items = result.json()["data"]["items"]
    assert [item["channel"] for item in items] == ["web_demo"]
    assert forbidden.status_code == 403
    assert profile.status_code == 200
    assert profile.json()["data"]["profile"]["user_id"] == demo_user
    assert forbidden_profile.status_code == 403


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
        "chat_with_sales_agent"
    ]


def test_render_app_only_serves_demo_frontend(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    assert client.get("/demo-chat").status_code == 200
    assert client.get("/workbench").status_code == 404
    assert client.get("/gate").status_code == 404
