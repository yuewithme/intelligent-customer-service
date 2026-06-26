import sys
from types import SimpleNamespace

import pytest

from app.services import embedding_service


@pytest.mark.asyncio
async def test_bge_provider_uses_sentence_transformer(monkeypatch):
    captured = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name: str):
            captured["model_name"] = model_name

        def encode(self, text: str, normalize_embeddings: bool):
            captured["text"] = text
            captured["normalize_embeddings"] = normalize_embeddings
            return [0.1, 0.2, 0.3]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(
        embedding_service,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_provider="bge",
            embedding_model="BAAI/bge-m3",
            qdrant_vector_size=3,
            embedding_api_key="",
            openai_api_key="",
            embedding_base_url="https://api.openai.com/v1",
        ),
    )

    vector = await embedding_service.embed_text("首单推进")

    assert vector == [0.1, 0.2, 0.3]
    assert captured == {
        "model_name": "BAAI/bge-m3",
        "text": "首单推进",
        "normalize_embeddings": True,
    }


@pytest.mark.asyncio
async def test_bge_provider_encodes_texts_in_batch(monkeypatch):
    captured = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name: str):
            captured["model_name"] = model_name

        def encode(self, texts: list[str], normalize_embeddings: bool):
            captured["texts"] = texts
            captured["normalize_embeddings"] = normalize_embeddings
            return [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(
        embedding_service,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_provider="bge",
            embedding_model="BAAI/bge-m3",
            qdrant_vector_size=3,
            embedding_api_key="",
            openai_api_key="",
            embedding_base_url="https://api.openai.com/v1",
        ),
    )
    embedding_service._bge_model.cache_clear()

    vectors = await embedding_service.embed_texts(["首单推进", "试成交"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert captured == {
        "model_name": "BAAI/bge-m3",
        "texts": ["首单推进", "试成交"],
        "normalize_embeddings": True,
    }
