import pytest


def test_ai_model_config_falls_back_by_purpose(monkeypatch):
    from app.config import get_settings
    from app.services.llm_service import get_model_config

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
    from app.config import get_settings
    from app.services.llm_service import get_model_config

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
    from app.config import get_settings
    from app.services.llm_service import get_model_config

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
    from app.config import get_settings
    from app.services.llm_service import get_model_config

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
    from app.config import get_settings
    from app.services.llm_service import get_model_config

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


def test_production_never_uses_mock_when_real_provider_key_exists(monkeypatch):
    from app.config import get_settings
    from app.services.llm_service import get_model_config

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("EVALUATION_MODE", "false")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RAG_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    get_settings.cache_clear()

    try:
        config = get_model_config("rag")
    finally:
        get_settings.cache_clear()

    assert config.provider == "deepseek"
    assert config.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_talk_script_classifier_uses_talk_script_model(monkeypatch):
    from app.config import get_settings
    from app.talk_script import llm_question_classifier

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
