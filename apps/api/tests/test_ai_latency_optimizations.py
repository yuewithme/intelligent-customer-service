import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_flash_models_are_selected_for_latency_sensitive_purposes(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.ai.services.llm_service import get_model_config

    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    monkeypatch.delenv("INTENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("PERSONA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PERSONA_LLM_MODEL", raising=False)
    monkeypatch.delenv("RAG_FAST_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_FAST_LLM_MODEL", raising=False)
    get_settings.cache_clear()

    try:
        assert get_model_config("intent").model == "qwen3.6-flash"
        assert get_model_config("persona").model == "qwen3.6-flash"
        assert get_model_config("rag_fast").model == "qwen3.6-flash"
        assert get_model_config("rag").model == "qwen3.7-plus"
    finally:
        get_settings.cache_clear()


def test_model_call_log_stores_metrics_but_not_prompt_body(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.infrastructure.database.models import AiModelCallLogModel
    from app.integrations.ai.services import model_call_log_service

    database_url = f"sqlite:///{(tmp_path / 'model-calls.db').as_posix()}"
    prompt = "customer private message"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_DB_URL", database_url)
    get_settings.cache_clear()
    model_call_log_service._sessionmakers.clear()

    model_call_log_service.record_model_call(
        trace_id="trace-1",
        purpose="intent",
        provider="dashscope",
        model="qwen3.6-flash",
        prompt=prompt,
        prompt_version="v2",
        duration_ms=321,
        input_tokens=42,
        output_tokens=8,
        attempt=1,
        status="success",
    )

    with Session(create_engine(database_url)) as session:
        row = session.scalar(select(AiModelCallLogModel))

    assert row is not None
    assert row.trace_id == "trace-1"
    assert row.prompt_chars == len(prompt)
    assert row.prompt_hash != prompt
    assert row.duration_ms == 321
    assert row.input_tokens == 42
    assert not hasattr(row, "prompt")

    get_settings.cache_clear()
    model_call_log_service._sessionmakers.clear()


@pytest.mark.asyncio
async def test_high_confidence_rule_bypasses_intent_llm(monkeypatch):
    from app.core.config import get_settings
    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.customers.schemas.state import UserState
    from app.domains.decisioning.services import intent_service

    async def fail_llm(*args, **kwargs):
        del args, kwargs
        raise AssertionError("high-confidence rule must bypass the LLM")

    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    monkeypatch.setenv("INTENT_FAST_RULES_ENABLED", "true")
    monkeypatch.setenv("INTENT_FAST_RULE_THRESHOLD", "0.85")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", fail_llm)

    message = NormalizedMessage(
        trace_id="trace-fast-rule",
        channel="api",
        user_id="user-1",
        session_id="session-1",
        message="不要再给我推荐产品了",
        kb_id="kb_default",
    )
    try:
        result = await intent_service.classify_intent(
            message, UserState(user_id="user-1")
        )
    finally:
        get_settings.cache_clear()

    assert result.primary_intent == "purchase_rejection"
    assert result.confidence >= 0.85


@pytest.mark.asyncio
async def test_intent_taxonomy_embeddings_are_reused_after_memory_reset(
    monkeypatch, tmp_path
):
    from app.core.config import get_settings
    from app.domains.decisioning.services import intent_example_service
    from app.domains.decisioning.services.intent_taxonomy_service import (
        load_intent_taxonomy,
    )

    calls = 0

    async def fake_embed_texts(texts):
        nonlocal calls
        calls += 1
        return [[1.0, 0.0] for _ in texts]

    cache_path = tmp_path / "intent-vectors.json"
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-bge")
    monkeypatch.setenv("QDRANT_VECTOR_SIZE", "2")
    monkeypatch.setenv("INTENT_EMBEDDING_CACHE_PATH", str(cache_path))
    get_settings.cache_clear()
    intent_example_service._catalog_embedding_cache.clear()
    monkeypatch.setattr(intent_example_service, "embed_texts", fake_embed_texts)

    catalog = load_intent_taxonomy()
    first = await intent_example_service._catalog_vectors(
        catalog["labels"], catalog["version"]
    )
    intent_example_service._catalog_embedding_cache.clear()
    second = await intent_example_service._catalog_vectors(
        catalog["labels"], catalog["version"]
    )

    assert calls == 1
    assert first == second
    assert cache_path.exists()

    get_settings.cache_clear()
    intent_example_service._catalog_embedding_cache.clear()
