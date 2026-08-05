import pytest


def test_ai_model_config_falls_back_by_purpose(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "doubao-default")
    monkeypatch.delenv("INTENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("TALK_SCRIPT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TALK_SCRIPT_LLM_MODEL", raising=False)
    get_settings.cache_clear()

    try:
        config = get_model_config("talk_script")
    finally:
        get_settings.cache_clear()

    assert config.provider == "volcengine"
    assert config.model == "doubao-default"


def test_talk_script_model_config_prefers_dedicated_provider(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "doubao-default")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("INTENT_LLM_MODEL", "qwen-intent")
    monkeypatch.setenv("TALK_SCRIPT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TALK_SCRIPT_LLM_MODEL", "gpt-talk-script")
    get_settings.cache_clear()

    try:
        config = get_model_config("talk_script")
    finally:
        get_settings.cache_clear()

    assert config.provider == "openai"
    assert config.model == "gpt-talk-script"


def test_rag_model_config_prefers_dedicated_provider(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "doubao-default")
    monkeypatch.setenv("RAG_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("RAG_LLM_MODEL", "deepseek-rag")
    get_settings.cache_clear()

    try:
        config = get_model_config("rag")
    finally:
        get_settings.cache_clear()

    assert config.provider == "deepseek"
    assert config.model == "deepseek-rag"


def test_business_model_config_prefers_dedicated_provider(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "doubao-default")
    monkeypatch.setenv("RAG_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("RAG_LLM_MODEL", "deepseek-rag")
    monkeypatch.setenv("BUSINESS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("BUSINESS_LLM_MODEL", "gpt-business")
    get_settings.cache_clear()

    try:
        config = get_model_config("business")
    finally:
        get_settings.cache_clear()

    assert config.provider == "openai"
    assert config.model == "gpt-business"


def test_profile_model_config_prefers_dedicated_provider(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "doubao-default")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("INTENT_LLM_MODEL", "qwen-intent")
    monkeypatch.setenv("PROFILE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("PROFILE_LLM_MODEL", "deepseek-profile")
    get_settings.cache_clear()

    try:
        config = get_model_config("profile")
    finally:
        get_settings.cache_clear()

    assert config.provider == "deepseek"
    assert config.model == "deepseek-profile"


@pytest.mark.asyncio
async def test_dashscope_json_generation_disables_thinking_and_requests_json(
    monkeypatch,
):
    from app.core.config import get_settings
    from app.integrations.ai.services import llm_service

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"confidence": 0.9}'}}],
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setenv("INTENT_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("INTENT_LLM_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)

    try:
        result = await llm_service.generate_json(
            "请以 JSON 格式输出意图。",
            purpose="intent",
        )
    finally:
        get_settings.cache_clear()

    assert result == {"confidence": 0.9}
    assert captured["body"]["enable_thinking"] is False
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "qwen3.7-plus"
    assert captured["headers"]["Authorization"] == "Bearer dashscope_test_key"


@pytest.mark.asyncio
async def test_dashscope_deepseek_shadow_uses_max_reasoning_and_json(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services import llm_service

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"confidence": 0.9}'}}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setenv("REPLY_SHADOW_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("REPLY_SHADOW_LLM_MODEL", "deepseek-v4-flash-0731")
    monkeypatch.setenv("REPLY_SHADOW_LLM_REASONING_EFFORT", "max")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)

    try:
        result = await llm_service.generate_json(
            "review this reply",
            purpose="reply_shadow",
        )
    finally:
        get_settings.cache_clear()

    assert result == {"confidence": 0.9}
    assert captured["body"]["enable_thinking"] is True
    assert captured["body"]["reasoning_effort"] == "max"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "deepseek-v4-flash-0731"


@pytest.mark.asyncio
async def test_talk_script_classifier_uses_talk_script_model(monkeypatch):
    from app.core.config import get_settings
    from app.domains.sales.talk_script import llm_question_classifier

    captured = {}

    async def fake_generate_json(prompt, purpose="intent"):
        captured["purpose"] = purpose
        return {
            "matched": True,
            "question_id": "Q04_01_001",
            "confidence": 0.9,
            "need_slot_filling": False,
            "need_human": False,
            "reason": "ok",
        }

    monkeypatch.setenv("TALK_SCRIPT_LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("TALK_SCRIPT_LLM_MODEL", "doubao-talk-script")
    get_settings.cache_clear()
    monkeypatch.setattr(
        llm_question_classifier.llm_service, "generate_json", fake_generate_json
    )

    try:
        decision = await llm_question_classifier.classify_question(
            current_message="刚收到兰花要不要换盆？",
            normalized_message="刚收到兰花要不要换盆?",
            recent_messages=[],
            customer_tags={},
            candidate_questions=[
                {
                    "question_id": "Q04_01_001",
                    "standard_question": "兰花收到后是否需要换盆？",
                }
            ],
        )
    finally:
        get_settings.cache_clear()

    assert captured["purpose"] == "talk_script"
    assert decision.question_id == "Q04_01_001"
