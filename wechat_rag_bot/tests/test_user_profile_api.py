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


def test_profile_analysis_prompt_wraps_message_content_for_llm():
    from app.services.user_profile_service import _build_profile_analysis_prompt

    prompt = _build_profile_analysis_prompt(
        [{"created_at": "2026-07-07T10:00:00+00:00", "content": "hello"}]
    )

    assert "读取每条记录的 `content` 字段" in prompt
    assert "{{用户消息原文}}" in prompt
    assert '"content": "{{hello}}"' in prompt


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
    assert profile["customer_tags"] == ["浙江省"]
    assert profile["product_interests"] == ["兰花养护"]
    assert profile["pain_points"] == ["兰花烂根，需要救治方案"]
    assert profile["ai_summary"] == "客户在杭州，预算约200元，关注兰花烂根，需要救治方案。"


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
    assert profile["ai_summary"] == "关注兰花烂根、黄叶，担心养死，需要救治方案。"


@pytest.mark.asyncio
async def test_profile_update_uses_only_raw_user_messages_for_llm_profile(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    captured = {}

    async def fake_generate_json(prompt: str, purpose: str = "intent") -> dict:
        captured["prompt"] = prompt
        captured["purpose"] = purpose
        return {
            "current_stage": "interest",
            "risk_level": "normal",
            "customer_tags": ["region:广西", "plant_count:100盆", "不在标签库"],
            "product_interests": ["开花类兰花"],
            "pain_points": ["广西气候下有100盆花，想获得适合当地环境的品种推荐"],
            "ai_summary": "客户在广西，养了100盆花，正在咨询适合当地气候的开花类兰花推荐。",
        }

    monkeypatch.setattr(
        "app.services.user_profile_service.generate_json",
        fake_generate_json,
    )
    from app.services.user_profile_service import append_conversation_memory

    await append_conversation_memory(
        user_id="user_003",
        tenant_id="tenant_default",
        session_id="default",
        role="user",
        content="我在广西，养了100盆花，你有什么推荐的花吗",
        intent="knowledge_question",
        route="rag_answer",
        template_id="tpl_should_not_be_in_prompt",
        trace_id="trace_old",
    )
    await append_conversation_memory(
        user_id="user_003",
        tenant_id="tenant_default",
        session_id="default",
        role="assistant",
        content="后台回复不应该作为画像输入。",
        intent="knowledge_question",
        route="rag_answer",
        trace_id="trace_assistant",
    )
    message = NormalizedMessage(
        trace_id="trace_003",
        channel="wechat",
        user_id="user_003",
        session_id="default",
        message="我在云南，养了100盆花，你有什么推荐的花吗",
        kb_id="kb_default",
        tenant_id="tenant_default",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        sales_stage="knowledge_consulting",
        confidence=0.9,
        need_rag=True,
        slots={},
        reason="care question",
    )
    reply = FinalReply(answer="推荐春兰。", reply_type="rag", route="rag_answer")

    await update_profile_after_chat(message, intent, reply)

    prompt = captured["prompt"]
    assert captured["purpose"] == "profile"
    assert "我在广西，养了100盆花" in prompt
    assert "我在云南，养了100盆花" in prompt
    assert "后台回复不应该作为画像输入" not in prompt
    assert "knowledge_question" not in prompt
    assert "rag_answer" not in prompt
    assert "tpl_should_not_be_in_prompt" not in prompt

    profile = (await get_profile_bundle("user_003"))["profile"]
    assert profile["customer_tags"] == ["广西省", "100-200盆"]
    assert profile["ai_summary"] == "客户在广西，养了100盆花，正在咨询适合当地气候的开花类兰花推荐。"


@pytest.mark.asyncio
async def test_profile_update_keeps_one_customer_tag_per_type(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)

    async def fake_generate_json(prompt: str, purpose: str = "intent") -> dict:
        return {
            "current_stage": "interest",
            "risk_level": "normal",
            "customer_tags": [
                "region:浙江",
                "region:杭州",
                "region:广西",
                "plant_count:100盆",
                "plant_count:20盆",
                "budget:100",
                "budget:200",
                "preference:建兰",
                "preference:蕙兰",
                "测试用户",
                "测试用户",
            ],
            "product_interests": ["建兰"],
            "pain_points": ["想找适合广西环境的兰花"],
            "ai_summary": "客户在广西，养了20盆花，预算约200元，偏好蕙兰。",
        }

    monkeypatch.setattr(
        "app.services.user_profile_service.generate_json",
        fake_generate_json,
    )
    message = NormalizedMessage(
        trace_id="trace_004",
        channel="wechat",
        user_id="user_004",
        session_id="default",
        message="我在广西，养了20盆花，预算200，想买蕙兰",
        kb_id="kb_default",
        tenant_id="tenant_default",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        sales_stage="knowledge_consulting",
        confidence=0.9,
        need_rag=True,
        slots={},
        reason="care question",
    )
    reply = FinalReply(
        answer="可以看看适合广西环境的蕙兰。",
        reply_type="rag",
        route="rag_answer",
        metadata={
            "tag_result": {
                "labels": ["region:浙江", "plant_count:100盆", "budget:100"],
                "risk_level": "normal",
            }
        },
    )

    await update_profile_after_chat(message, intent, reply)

    profile = (await get_profile_bundle("user_004"))["profile"]
    assert profile["customer_tags"] == ["广西省", "10-30盆", "蕙兰"]


@pytest.mark.asyncio
async def test_profile_update_filters_customer_tags_to_catalog_values(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)

    async def fake_generate_json(prompt: str, purpose: str = "intent") -> dict:
        return {
            "current_stage": "interest",
            "risk_level": "normal",
            "customer_tags": [
                "region:火星",
                "plant_count:99999盆",
                "budget:200",
                "preference:不存在的品类",
                "测试用户",
                "customer_tag:L3 黄金期",
                "customer_tag:建兰",
            ],
            "product_interests": ["建兰"],
            "pain_points": ["想买建兰"],
            "ai_summary": "客户想买建兰。",
        }

    monkeypatch.setattr(
        "app.services.user_profile_service.generate_json",
        fake_generate_json,
    )
    message = NormalizedMessage(
        trace_id="trace_005",
        channel="wechat",
        user_id="user_005",
        session_id="default",
        message="想买建兰",
        kb_id="kb_default",
        tenant_id="tenant_default",
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="knowledge_question",
        sales_stage="knowledge_consulting",
        confidence=0.9,
        need_rag=True,
        slots={},
        reason="care question",
    )
    reply = FinalReply(
        answer="可以看看建兰。",
        reply_type="rag",
        route="rag_answer",
        metadata={
            "tag_result": {
                "labels": ["region:杭州", "budget:200", "pain_point:兰花烂根"],
                "risk_level": "normal",
            }
        },
    )

    await update_profile_after_chat(message, intent, reply)

    profile = (await get_profile_bundle("user_005"))["profile"]
    assert profile["customer_tags"] == ["浙江省", "L3 黄金期", "建兰"]


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
