import sys
import threading
from types import SimpleNamespace

import pytest

from app.services import embedding_service


@pytest.mark.asyncio
async def test_bge_encoding_runs_outside_event_loop_thread(monkeypatch):
    event_loop_thread = threading.get_ident()
    encode_threads = []

    class FakeModel:
        def encode(self, text: str, normalize_embeddings: bool):
            del text, normalize_embeddings
            encode_threads.append(threading.get_ident())
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embedding_service, "_bge_model", lambda _: FakeModel())
    monkeypatch.setattr(
        embedding_service,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_provider="bge",
            embedding_model="BAAI/bge-m3",
            qdrant_vector_size=3,
        ),
    )

    await embedding_service.embed_text("hello")

    assert encode_threads
    assert encode_threads[0] != event_loop_thread


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


@pytest.mark.asyncio
async def test_bge_provider_splits_large_batches(monkeypatch):
    captured_batches = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str):
            pass

        def encode(self, texts: list[str], normalize_embeddings: bool):
            captured_batches.append(list(texts))
            return [[float(len(text)), 0.0, 0.0] for text in texts]

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
            embedding_batch_size=2,
            embedding_api_key="",
            openai_api_key="",
            embedding_base_url="https://api.openai.com/v1",
        ),
    )
    embedding_service._bge_model.cache_clear()

    vectors = await embedding_service.embed_texts(["one", "two", "three", "four", "five"])

    assert vectors == [
        [3.0, 0.0, 0.0],
        [3.0, 0.0, 0.0],
        [5.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
    ]
    assert captured_batches == [["one", "two"], ["three", "four"], ["five"]]
