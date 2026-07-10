from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_chat_log_metadata_keeps_reply_decision_trace_without_business_secrets():
    from app.services.chat_log_service import sanitize_log_payload

    payload = {
        "user_message": "我刚付过了，怎么还显示失败？",
        "answer": "系统当前显示支付失败，请先核对是否实际扣款。",
        "metadata": {
            "decision": {
                "action": "template_reply",
                "reason": "template_intent",
                "trace": [
                    {
                        "source": "planner",
                        "proposed_action": "template_reply",
                        "reason": "template_intent",
                    }
                ],
                "business_facts": {"tool_state": {"payment_status": "failed"}},
            },
            "authorization": "Bearer secret",
        },
    }

    record = sanitize_log_payload(payload)

    assert record["metadata"]["decision"]["trace"][0]["source"] == "planner"
    assert "business_facts" not in record["metadata"]["decision"]
    assert "authorization" not in record["metadata"]


def _reset_settings(monkeypatch, tmp_path, *, enabled: bool = True, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    app_db_path = tmp_path / "rag.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{app_db_path.as_posix()}")
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


def test_admin_talk_script_match_logs_return_match_details(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    from app.talk_script.repository import record_match_log

    record_match_log(
        {
            "trace_id": "trace_talk_001",
            "customer_id": "user_001",
            "session_id": "sess_001",
            "user_message": "这个有点贵",
            "normalized_message": "这个有点贵",
            "status": "matched",
            "scene_id": "S07",
            "candidate_question_ids": ["Q07_02_001"],
            "matched_question_id": "Q07_02_001",
            "template_id": "T07_02_001",
            "confidence": 0.91,
            "need_slot_filling": False,
            "need_human": False,
            "final_answer": "价格说明话术",
            "match_reason": "命中价格异议",
        }
    )

    client = TestClient(app)
    response = client.get("/api/v1/admin/talk-script-match-logs")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["trace_id"] == "trace_talk_001"
    assert item["status"] == "matched"
    assert item["scene_id"] == "S07"
    assert item["candidate_question_ids"] == ["Q07_02_001"]
    assert item["matched_question_id"] == "Q07_02_001"
    assert item["template_id"] == "T07_02_001"
    assert item["match_reason"] == "命中价格异议"


def test_admin_talk_script_match_stats_returns_reason_and_scene_breakdown(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    from app.talk_script.repository import record_match_log

    record_match_log(
        {
            "trace_id": "trace_talk_001",
            "customer_id": "user_001",
            "session_id": "sess_001",
            "user_message": "price question",
            "normalized_message": "price question",
            "status": "matched",
            "scene_id": "S07",
            "candidate_question_ids": ["Q07_01"],
            "matched_question_id": "Q07_01",
            "template_id": "T07_01",
            "confidence": 0.92,
            "need_human": False,
            "match_reason": "price_objection",
        }
    )
    record_match_log(
        {
            "trace_id": "trace_talk_002",
            "customer_id": "user_001",
            "session_id": "sess_001",
            "user_message": "unknown",
            "normalized_message": "unknown",
            "status": "pass_through",
            "scene_id": None,
            "confidence": 0.0,
            "need_human": False,
            "match_reason": "no_scene_match",
        }
    )
    record_match_log(
        {
            "trace_id": "trace_talk_003",
            "customer_id": "user_002",
            "session_id": "sess_002",
            "user_message": "need human",
            "normalized_message": "need human",
            "status": "handoff",
            "scene_id": "S08",
            "candidate_question_ids": ["Q08_01"],
            "confidence": 0.44,
            "need_human": True,
            "match_reason": "confidence_below_threshold",
        }
    )

    client = TestClient(app)
    response = client.get("/api/v1/admin/talk-script-match-stats")

    assert response.status_code == 200
    stats = response.json()["data"]
    assert stats["total"] == 3
    assert stats["matched_count"] == 1
    assert stats["handoff_count"] == 1
    assert stats["pass_through_count"] == 1
    assert stats["human_count"] == 1
    assert stats["avg_confidence"] == 0.45
    assert stats["status_counts"] == {"matched": 1, "pass_through": 1, "handoff": 1}
    assert stats["reason_counts"] == {
        "price_objection": 1,
        "no_scene_match": 1,
        "confidence_below_threshold": 1,
    }
    assert stats["scene_counts"] == {"S07": 1, "S08": 1}
    assert stats["template_counts"] == {"T07_01": 1}
    assert stats["low_confidence_items"][0]["trace_id"] == "trace_talk_003"


def test_admin_rag_debug_search_returns_candidates_reranked_docs_and_prompt_preview(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    import anyio

    from app.services import embedding_service, qdrant_service

    async def seed_points():
        qdrant_service._memory_points.clear()
        vectors = await embedding_service.embed_texts(["orchid root rot", "price policy"])
        await qdrant_service.upsert_chunks(
            [
                {
                    "id": "chunk_root",
                    "vector": vectors[0],
                    "payload": {
                        "text": "Root rot needs ventilation and checking roots.",
                        "kb_id": "kb_care",
                        "doc_id": "doc_root",
                        "chunk_id": "chunk_root",
                        "file_name": "care.md",
                        "file_type": "md",
                        "page": None,
                        "section": "Root rot",
                        "tenant_id": "tenant_default",
                        "permission": "public",
                    },
                },
                {
                    "id": "chunk_price",
                    "vector": vectors[1],
                    "payload": {
                        "text": "Price objections should be answered gently.",
                        "kb_id": "kb_sales",
                        "doc_id": "doc_price",
                        "chunk_id": "chunk_price",
                        "file_name": "sales.md",
                        "file_type": "md",
                        "page": None,
                        "section": "Price",
                        "tenant_id": "tenant_default",
                        "permission": "public",
                    },
                },
            ]
        )

    anyio.run(seed_points)

    client = TestClient(app)
    response = client.post(
        "/api/v1/admin/rag-debug/search",
        json={
            "message": "orchid root rot",
            "kb_id": "kb_default",
            "knowledge_base_ids": ["kb_care", "kb_sales"],
            "tenant_id": "tenant_default",
            "permission": "public",
            "top_k": 2,
            "top_n": 1,
            "include_prompt": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["search_kb_ids"] == ["kb_care", "kb_sales"]
    assert data["candidate_count"] == 2
    assert len(data["candidates"]) == 2
    assert len(data["reranked_docs"]) == 1
    assert data["reranked_docs"][0]["doc_id"]
    assert data["reranked_docs"][0]["rerank_score"] is not None
    assert data["reranked_docs"][0]["rerank_reason"]["vector_score"] is not None
    assert data["prompt_preview"]
    assert data["prompt_truncated"] is False


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
    assert "decision" not in data["metadata"]

    logs = client.get("/api/v1/admin/chat-logs", params={"user_id": "user_001"})
    assert logs.status_code == 200
    assert logs.json()["data"]["total"] == 1
    logged = logs.json()["data"]["items"][0]
    assert logged["answer"] == data["answer"]
    assert logged["latency_ms"] >= 0

    detail = client.get(f"/api/v1/admin/chat-logs/{data['trace_id']}")
    assert detail.status_code == 200
    decision = detail.json()["data"]["metadata"]["decision"]
    assert decision["action"]
    assert decision["trace"][-1]["source"] == "planner"
    assert "business_facts" not in decision


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
