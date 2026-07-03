from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("STATE_PROVIDER", "memory")
    get_settings.cache_clear()


def test_conversation_list_starts_empty(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 50}


def test_chat_creates_ai_owned_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    chat = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    assert chat.status_code == 200
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "api:user_001:sess_001"
    assert item["status"] in {"ai_active", "ai_waiting"}
    assert item["owner_id"] is None

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    assert detail.status_code == 200
    messages = detail.json()["data"]["messages"]
    assert [message["sender_type"] for message in messages] == ["customer", "ai"]


def test_conversation_exposes_customer_display_snapshot(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "wechat",
            "user_id": "wxid_customer",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {
                "remark_name": "Alice Remark",
                "avatar_url": "https://example.com/avatar.jpg",
            },
        },
    )

    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    item = conversations["items"][0]
    assert item["user_display_name"] == "Alice Remark"
    assert item["user_avatar_url"] == "https://example.com/avatar.jpg"

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_customer:sess_001")
    conversation = detail.json()["data"]["conversation"]
    assert conversation["user_display_name"] == "Alice Remark"
    assert conversation["user_avatar_url"] == "https://example.com/avatar.jpg"


def test_human_cannot_reply_until_conversation_is_claimed(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    response = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "人工回复"},
    )

    assert response.status_code == 409
    assert "不能人工回复" in response.json()["message"]


def test_claimed_handoff_conversation_accepts_human_reply(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )
    reply = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "您好，我来处理。"},
    )

    assert claim.status_code == 200
    assert claim.json()["data"]["status"] == "human_active"
    assert reply.status_code == 200
    assert reply.json()["data"]["status"] == "human_active"

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "您好，我来处理。"


def test_force_handoff_allows_operator_to_claim_ai_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    force = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/force-handoff",
        json={"operator_id": "lead_001", "reason": "AI 可能误答"},
    )
    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    assert force.status_code == 200
    assert force.json()["data"]["status"] == "handoff_pending"
    assert force.json()["data"]["handoff_reason"] == "AI 可能误答"
    assert claim.status_code == 200


def test_release_to_ai_and_resolve_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    release = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/release-to-ai",
        json={"operator_id": "op_001"},
    )
    resolve = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/resolve",
        json={"operator_id": "op_001", "reason": "已处理"},
    )

    assert release.status_code == 200
    assert release.json()["data"]["status"] == "ai_active"
    assert resolve.status_code == 200
    assert resolve.json()["data"]["status"] == "resolved"


def test_conversation_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
