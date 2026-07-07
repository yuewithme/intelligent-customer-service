from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import app
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.services.user_profile_service import get_profile_bundle, update_profile_after_chat


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat_logs.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


def test_get_profile_creates_empty_profile_and_legacy_state_still_works(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/users/user_001/profile")
    legacy_response = client.get("/api/v1/users/user_001/state")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 0
    profile = body["data"]["profile"]
    assert profile["user_id"] == "user_001"
    assert profile["tenant_id"] == "tenant_default"
    assert profile["current_stage"] == "unknown"
    assert profile["risk_level"] == "normal"
    assert profile["customer_tags"] == []
    assert body["data"]["recent_memories"] == []
    assert body["data"]["events"] == []

    assert legacy_response.status_code == 200
    assert legacy_response.json()["data"]["user_id"] == "user_001"


def test_patch_profile_updates_allowed_fields_and_writes_event(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.patch(
        "/api/v1/users/user_001/profile",
        json={
            "customer_tags": ["vip", "price_sensitive"],
            "tenant_id": "evil",
            "metadata": {"reason": "operator_update"},
        },
    )
    events_response = client.get("/api/v1/users/user_001/profile/events")

    assert response.status_code == 200
    profile = response.json()["data"]["profile"]
    assert profile["tenant_id"] == "tenant_default"
    assert profile["customer_tags"] == ["vip", "price_sensitive"]

    assert events_response.status_code == 200
    event = events_response.json()["data"]["items"][0]
    assert event["event_type"] == "profile_patched"
    assert event["before"]["customer_tags"] == []
    assert event["after"]["customer_tags"] == ["vip", "price_sensitive"]
    assert event["reason"] == "operator_update"


def test_memories_returns_recent_chat_messages(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    chat_response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {"tenant_id": "tenant_default"},
        },
    )
    memories_response = client.get("/api/v1/users/user_001/memories")

    assert chat_response.status_code == 200
    assert memories_response.status_code == 200
    body = memories_response.json()
    assert set(body) == {"code", "message", "data"}
    assert body["data"]["limit"] == 10
    assert [item["role"] for item in body["data"]["items"]] == ["user", "assistant"]
    assert body["data"]["items"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_profile_update_persists_tag_result_and_overall_memory(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    message = NormalizedMessage(
        trace_id="trace_001",
        channel="wechat",
        user_id="user_001",
        session_id="default",
        message="我的预算在200这样，在杭州这边，兰花烂根了咋办",
        kb_id="kb_default",
        tenant_id="tenant_default",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        secondary_intents=["root_rot"],
        sales_stage="knowledge_consulting",
        confidence=0.92,
        need_rag=True,
        slots={"budget": "200", "city": "杭州", "plant_issue": "兰花烂根"},
        reason="care question",
    )
    reply = FinalReply(
        answer="先检查根系并控水通风。",
        reply_type="rag",
        route="rag_answer",
        metadata={
            "tag_result": {
                "labels": [
                    "budget:200",
                    "region:杭州",
                    "pain_point:兰花烂根",
                    "product_interest:兰花养护",
                ],
                "risk_level": "normal",
            }
        },
    )

    await update_profile_after_chat(message, intent, reply)

    profile = (await get_profile_bundle("user_001"))["profile"]
    assert profile["customer_tags"] == ["budget:200", "region:杭州", "pain_point:兰花烂根"]
    assert profile["product_interests"] == ["兰花养护"]
    assert profile["pain_points"] == ["兰花烂根，需要救治方案"]
    assert profile["ai_summary"] == (
        "客户在杭州，预算约200元，正在咨询兰花烂根，需要救治方案处理；"
        "整体看更关注兰花养护问题，适合给出分步骤、可执行的养护建议。"
    )


@pytest.mark.asyncio
async def test_profile_update_expands_pain_points_from_chat_record(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    message = NormalizedMessage(
        trace_id="trace_002",
        channel="wechat",
        user_id="user_002",
        session_id="default",
        message="兰花烂根还黄叶，我怕自己养死了，想知道怎么救回来",
        kb_id="kb_default",
        tenant_id="tenant_default",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="care_question",
        secondary_intents=["root_rot", "yellow_leaf"],
        sales_stage="knowledge_consulting",
        confidence=0.9,
        need_rag=True,
        slots={},
        reason="care question",
    )
    reply = FinalReply(
        answer="先脱盆检查根系，修剪烂根并控水通风。",
        reply_type="rag",
        route="rag_answer",
        metadata={"tag_result": {"labels": ["pain_point:兰花烂根"], "risk_level": "normal"}},
    )

    await update_profile_after_chat(message, intent, reply)

    profile = (await get_profile_bundle("user_002"))["profile"]
    assert profile["pain_points"] == ["兰花烂根、黄叶，担心养死，需要救治方案"]
    assert "正在咨询兰花烂根、黄叶，担心养死，需要救治方案处理" in profile["ai_summary"]


def test_new_profile_apis_require_bearer_authentication(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    missing = client.get("/api/v1/users/user_001/profile")
    authorized = client.get(
        "/api/v1/users/user_001/profile",
        headers={"Authorization": "Bearer test-key"},
    )

    assert missing.status_code == 401
    assert missing.json()["data"] is None
    assert authorized.status_code == 200
