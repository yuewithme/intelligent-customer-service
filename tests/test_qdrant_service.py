from types import SimpleNamespace

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
