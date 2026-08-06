import pytest


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
    monkeypatch.setenv("PROFILE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("PROFILE_LLM_MODEL", "deepseek-profile")
    get_settings.cache_clear()

    try:
        config = get_model_config("profile")
    finally:
        get_settings.cache_clear()

    assert config.provider == "deepseek"
    assert config.model == "deepseek-profile"


@pytest.mark.parametrize(
    ("model", "enable_thinking", "reasoning_effort"),
    [
        ("qwen3.7-plus", False, None),
        ("qwen3.8-max", True, "xhigh"),
    ],
)
@pytest.mark.asyncio
async def test_dashscope_agent_json_configures_thinking_and_requests_json(
    monkeypatch, model, enable_thinking, reasoning_effort
):
    from app.core.config import get_settings
    from app.integrations.ai.services import llm_service

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"commercial_judgment":"继续了解需求",'
                                '"relationship_purpose":"建立专业信任",'
                                '"customer_signal":"none","tool_calls":[],'
                                '"final_response":{"messages":[],'
                                '"need_human":false}}'
                            )
                        }
                    }
                ],
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

    monkeypatch.setenv("BUSINESS_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("BUSINESS_LLM_MODEL", model)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)

    try:
        result = await llm_service.generate_messages_json(
            [{"role": "system", "content": "自主销售 Agent"}],
            purpose="business",
        )
    finally:
        get_settings.cache_clear()

    assert result["data"]["commercial_judgment"] == "继续了解需求"
    assert captured["body"]["enable_thinking"] is enable_thinking
    if reasoning_effort is None:
        assert "reasoning_effort" not in captured["body"]
    else:
        assert captured["body"]["reasoning_effort"] == reasoning_effort
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == model
    assert captured["headers"]["Authorization"] == "Bearer dashscope_test_key"
