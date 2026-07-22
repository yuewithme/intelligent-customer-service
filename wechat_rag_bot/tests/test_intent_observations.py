import json

import anyio
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.services.intent_observation_service import record_intent_observation


@pytest.fixture(autouse=True)
def _clear_settings_after_test():
    yield
    get_settings.cache_clear()


def _reset(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "intent-observations.db"
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("INTENT_OBSERVATION_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


async def _record_sample(trace_id: str = "trace-intent-1"):
    message = NormalizedMessage(
        trace_id=trace_id,
        channel="api",
        user_id="customer-1",
        session_id="session-1",
        message="我手机号13800138000，觉得有点贵，也怕养不好",
        kb_id="kb_default",
    )
    intent = IntentResult(
        route="template_then_rag",
        primary_intent="price_objection",
        secondary_intents=["care_question"],
        primary_domain="commercial_decision",
        secondary_domains=["care_service"],
        primary_goal="express_objection",
        issues=["price", "care_confidence"],
        classifier_source="llm",
        classifier_provider="mock",
        classifier_model="mock-intent",
        raw_prediction={"primary_goal": "express_objection"},
        confidence=0.91,
        need_template=True,
        need_rag=True,
        reason="价格与养护顾虑",
    )
    await record_intent_observation(
        message=message,
        intent=intent,
        candidates=[
            {"kind": "goal", "id": "express_objection", "score": 0.88}
        ],
        context=[{"role": "assistant", "content": "您比较在意哪方面？"}],
    )


def test_observation_annotation_history_and_training_export(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)
    client = TestClient(app)

    pending = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "pending"},
    ).json()["data"]
    assert pending["total"] == 1
    assert pending["items"][0]["primary_goal"] == "express_objection"
    assert pending["items"][0]["annotation_status"] == "pending"

    confirmed = client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "confirmed",
            "annotator_id": "reviewer-a",
            "note": "预测正确",
        },
    )
    assert confirmed.status_code == 200

    corrected = client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "corrected",
            "primary_domain": "commercial_decision",
            "secondary_domains": [],
            "primary_goal": "negotiate",
            "secondary_goals": [],
            "issues": ["discount"],
            "scope": "in_scope",
            "annotator_id": "reviewer-b",
            "note": "实际是在议价",
        },
    )
    assert corrected.status_code == 200

    detail = client.get(
        "/api/v1/admin/intent-observations/trace-intent-1"
    ).json()["data"]
    assert detail["annotation_status"] == "corrected"
    assert len(detail["annotation_history"]) == 2
    assert detail["raw_prediction"] == {"primary_goal": "express_objection"}
    assert detail["candidate_labels"][0]["id"] == "express_objection"

    exported = client.get("/api/v1/admin/intent-training-data").json()["data"]
    assert exported["total"] == 1
    sample = exported["items"][0]
    assert sample["labels"]["primary_goal"] == "negotiate"
    assert sample["labels"]["issues"] == ["discount"]
    assert "13800138000" not in sample["text"]
    assert "[MOBILE]" in sample["text"]

    jsonl = client.get("/api/v1/admin/intent-training-data/export")
    assert jsonl.status_code == 200
    assert json.loads(jsonl.text.strip())["sample_id"] == "trace-intent-1"


def test_invalid_corrected_label_is_rejected(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)

    response = TestClient(app).post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "corrected",
            "primary_domain": "not_a_domain",
            "primary_goal": "negotiate",
            "scope": "in_scope",
            "annotator_id": "reviewer",
        },
    )

    assert response.status_code == 422


def test_only_latest_annotation_controls_training_eligibility(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)
    client = TestClient(app)
    client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={"status": "confirmed", "annotator_id": "reviewer"},
    )
    client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "excluded",
            "annotator_id": "reviewer",
            "note": "包含无法判断的上下文",
        },
    )

    result = client.get("/api/v1/admin/intent-training-data").json()["data"]
    assert result["total"] == 0


def test_chat_pipeline_records_each_classified_user_turn(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "customer-pipeline",
            "session_id": "session-pipeline",
            "message": "这个多少钱？",
            "kb_id": "kb_default",
        },
    )

    assert response.status_code == 200
    observations = client.get("/api/v1/admin/intent-observations").json()["data"]
    assert observations["total"] == 1
    item = observations["items"][0]
    assert item["user_message"] == "这个多少钱？"
    assert item["primary_domain"] == "commercial_decision"
    assert item["primary_goal"] == "ask_information"
    assert item["issues"] == ["price"]
