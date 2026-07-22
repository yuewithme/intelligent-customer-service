from types import SimpleNamespace

import pytest

from app.services import qdrant_service


def test_client_forwards_proxy_setting(monkeypatch):
    captured = {}

    class FakeAsyncQdrantClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "qdrant_client.AsyncQdrantClient", FakeAsyncQdrantClient
    )
    monkeypatch.setattr(
        qdrant_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_url="https://example.qdrant.io",
            qdrant_api_key="secret",
            qdrant_trust_env=False,
        ),
    )

    qdrant_service._client()

    assert captured["trust_env"] is False


@pytest.mark.asyncio
async def test_upsert_chunks_splits_batches(monkeypatch):
    batch_sizes = []

    class FakeClient:
        async def upsert(self, collection_name, points, wait):
            batch_sizes.append(len(points))

    async def fake_ensure_collection():
        return None

    monkeypatch.setattr(qdrant_service, "ensure_collection", fake_ensure_collection)
    monkeypatch.setattr(qdrant_service, "_client", lambda: FakeClient())
    monkeypatch.setattr(qdrant_service, "_is_memory_mode", lambda: False)
    monkeypatch.setattr(
        qdrant_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_collection="knowledge_chunks",
            qdrant_upsert_batch_size=2,
        ),
    )

    await qdrant_service.upsert_chunks(
        [
            {"id": "chunk_1", "vector": [0.1], "payload": {"text": "one"}},
            {"id": "chunk_2", "vector": [0.2], "payload": {"text": "two"}},
            {"id": "chunk_3", "vector": [0.3], "payload": {"text": "three"}},
            {"id": "chunk_4", "vector": [0.4], "payload": {"text": "four"}},
            {"id": "chunk_5", "vector": [0.5], "payload": {"text": "five"}},
        ]
    )

    assert batch_sizes == [2, 2, 1]
