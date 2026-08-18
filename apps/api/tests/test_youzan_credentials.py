import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.models import YouzanCredentialModel
from app.integrations.youzan.api import admin_youzan
from app.integrations.youzan.services import youzan_credential_service
from app.integrations.youzan.services.youzan_credential_service import (
    YouzanCredentials,
    credential_status,
    effective_youzan_credentials,
    save_youzan_credentials,
)
from app.integrations.youzan.services.youzan_token_service import YouzanTokenManager
from app.main import app


def _configure(monkeypatch, tmp_path, *, auth: bool = False) -> str:
    database_url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("API_KEY", "credential-test-api-key")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("YOUZAN_ACCESS_TOKEN", "")
    monkeypatch.setenv("YOUZAN_CLIENT_ID", "")
    monkeypatch.setenv("YOUZAN_CLIENT_SECRET", "")
    monkeypatch.setenv("YOUZAN_KDT_ID", "")
    get_settings.cache_clear()
    youzan_credential_service.reset_youzan_credential_store_for_tests()
    return database_url


def test_credentials_are_encrypted_and_reload_after_settings_restart(
    monkeypatch, tmp_path
):
    database_url = _configure(monkeypatch, tmp_path)
    credentials = YouzanCredentials(
        client_id="client-id-1234",
        client_secret="super-secret-value",
        kdt_id="9001",
    )

    saved = save_youzan_credentials(credentials)

    assert saved == {
        "configured": True,
        "client_id_masked": "clie******1234",
        "kdt_id": "9001",
    }
    with Session(create_engine(database_url)) as session:
        stored = session.scalar(select(YouzanCredentialModel))
        assert stored is not None
        assert "super-secret-value" not in stored.encrypted_payload
        assert "client-id-1234" not in stored.encrypted_payload

    get_settings.cache_clear()
    reloaded = effective_youzan_credentials()
    assert reloaded == credentials
    assert "client_secret" not in credential_status()


@pytest.mark.asyncio
async def test_token_exchange_uses_encrypted_runtime_credentials(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    save_youzan_credentials(
        YouzanCredentials(
            client_id="runtime-client",
            client_secret="runtime-secret",
            kdt_id="8123",
        )
    )
    get_settings.cache_clear()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {"access_token": "fresh-token"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await YouzanTokenManager(http_client=client).get_access_token()

    assert token == "fresh-token"
    assert json.loads(requests[0].content) == {
        "authorize_type": "silent",
        "client_id": "runtime-client",
        "client_secret": "runtime-secret",
        "grant_id": "8123",
    }


def test_admin_endpoint_requires_key_and_never_returns_secret(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, auth=True)

    class FakeTokenManager:
        async def get_access_token(self, *, force_refresh: bool = False) -> str:
            assert force_refresh is True
            return "verified-token"

    monkeypatch.setattr(admin_youzan, "get_youzan_token_manager", FakeTokenManager)
    client = TestClient(app)
    payload = {
        "client_id": "client-id-1234",
        "client_secret": "must-not-be-returned",
        "kdt_id": "9001",
    }

    assert client.put("/api/v1/admin/youzan/credentials", json=payload).status_code == 401
    response = client.put(
        "/api/v1/admin/youzan/credentials",
        json=payload,
        headers={"Authorization": "Bearer credential-test-api-key"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["configured"] is True
    assert data["token_verified"] is True
    assert "must-not-be-returned" not in response.text
