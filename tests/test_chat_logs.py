from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _reset_settings(monkeypatch, tmp_path, *, enabled: bool = True, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_RETENTION_DAYS", "30")
    monkeypatch.setenv("CHAT_LOG_MAX_MESSAGE_LENGTH", "2000")
    monkeypatch.setenv("CHAT_LOG_MAX_ANSWER_LENGTH", "4000")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


async def _record_sample_log(**overrides):
    from app.services.chat_log_service import record_chat_log

    payload = {
        "trace_id": "req_001",
        "request_id": "request_001",
        "channel": "api",
        "user_id": "user_001",
        "session_id": "sess_001",
        "message_id": "msg_001",
        "kb_id": "kb_default",
        "tenant_id": "tenant_default",
        "permission": "default",
        "user_message": "这个有点贵",
        "answer": "理解的，第一次入手确实会考虑价格。",
        "route": "template_reply",
        "reply_type": "template",
        "primary_intent": "price_objection",
        "secondary_intents": ["hesitation"],
        "sales_stage": "objection_handling",
        "confidence": 0.88,
        "template_id": "tpl_price_objection_001",
        "template_score": 0.91,
        "next_action": "continue",
        "sources": [],
        "need_human": False,
        "policy_reason": "价格异议走固定模板",
        "intent_reason": "用户表达价格顾虑",
        "usage": {"prompt_tokens": 1},
        "latency_ms": 1230,
        "stage_latencies": {"intent_ms": 420},
        "status": "success",
        "error_code": None,
        "error_message": None,
        "metadata": {"token": "secret", "safe": "ok"},
        "created_at": "2026-06-29T10:20:00+08:00",
    }
    payload.update(overrides)
    await record_chat_log(payload)
    return payload


def test_admin_chat_log_apis_return_list_detail_and_stats(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    import anyio

    anyio.run(_record_sample_log)

    client = TestClient(app)
    list_response = client.get("/api/v1/admin/chat-logs")
    detail_response = client.get("/api/v1/admin/chat-logs/req_001")
    stats_response = client.get("/api/v1/admin/chat-log-stats")

    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1
    item = list_response.json()["data"]["items"][0]
    assert item["trace_id"] == "req_001"
    assert item["route"] == "template_reply"
    assert item["primary_intent"] == "price_objection"

    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["template_score"] == 0.91
    assert detail["policy_reason"] == "价格异议走固定模板"
    assert detail["stage_latencies"] == {"intent_ms": 420}
    assert detail["metadata"] == {"safe": "ok"}

    assert stats_response.status_code == 200
    stats = stats_response.json()["data"]
    assert stats["total"] == 1
    assert stats["success_count"] == 1
    assert stats["failed_count"] == 0
    assert stats["avg_latency_ms"] == 1230
    assert stats["route_counts"] == {"template_reply": 1}
    assert stats["intent_counts"] == {"price_objection": 1}
    assert stats["template_counts"] == {"tpl_price_objection_001": 1}
    assert stats["template_count"] == 1


def test_admin_chat_logs_support_filters_and_keyword(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    import anyio

    anyio.run(_record_sample_log)
    async def record_second_log():
        await _record_sample_log(
            trace_id="req_002",
            user_id="user_002",
            route="rag_answer",
            reply_type="rag",
            primary_intent="care_question",
            template_id=None,
            user_message="怎么浇水",
            answer="浇水需要见干见湿。",
        )

    anyio.run(record_second_log)

    client = TestClient(app)

    assert client.get("/api/v1/admin/chat-logs", params={"route": "rag_answer"}).json()[
        "data"
    ]["total"] == 1
    assert client.get("/api/v1/admin/chat-logs", params={"user_id": "user_001"}).json()[
        "data"
    ]["total"] == 1
    assert client.get(
        "/api/v1/admin/chat-logs", params={"keyword": "浇水"}
    ).json()["data"]["items"][0]["trace_id"] == "req_002"


def test_missing_chat_log_detail_uses_unified_response(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/admin/chat-logs/missing_trace")

    assert response.status_code == 404
    assert response.json() == {"code": 40000, "message": "日志不存在", "data": None}


def test_admin_chat_log_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/chat-logs")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
    assert response.json()["data"] is None


def test_chat_api_generates_log_without_changing_legacy_fields(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert {"answer", "session_id", "sources", "usage"}.issubset(data)

    logs = client.get("/api/v1/admin/chat-logs", params={"user_id": "user_001"})
    assert logs.status_code == 200
    assert logs.json()["data"]["total"] == 1
    logged = logs.json()["data"]["items"][0]
    assert logged["answer"] == data["answer"]
    assert logged["latency_ms"] >= 0


def test_failed_chat_request_generates_failed_log(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    assert response.status_code == 400
    logs = client.get("/api/v1/admin/chat-logs", params={"status": "failed"})
    assert logs.json()["data"]["total"] == 1
    failed = logs.json()["data"]["items"][0]
    assert failed["status"] == "failed"
    assert failed["error_code"] == 40003


def test_chat_log_disabled_keeps_chat_working_but_writes_no_logs(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, enabled=False)
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    logs = client.get("/api/v1/admin/chat-logs")
    assert logs.status_code == 200
    assert logs.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 50}
