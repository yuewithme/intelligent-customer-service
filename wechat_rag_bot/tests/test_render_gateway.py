from fastapi.testclient import TestClient

from app import render_gateway


def _reset_settings(monkeypatch, upstream: str = "http://formal-sales"):
    monkeypatch.setenv("DEMO_UPSTREAM_BASE_URL", upstream)
    monkeypatch.setenv("DEMO_UPSTREAM_TIMEOUT_SECONDS", "200")
    render_gateway.get_settings.cache_clear()


def test_gateway_only_forwards_demo_requests(monkeypatch):
    _reset_settings(monkeypatch)
    captured = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": 0, "message": "success", "data": {"reply": "ok"}}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout.read == 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, json):
            captured.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(render_gateway.httpx, "AsyncClient", FakeClient)
    client = TestClient(render_gateway.app)

    opening = client.post(
        "/api/v1/demo/opening",
        json={"customer_id": "customer-1", "customer_name": "Demo customer"},
    )
    chat = client.post(
        "/api/v1/demo/chat",
        json={"customer_id": "customer-1", "message": "hello"},
    )

    assert opening.status_code == 200
    assert chat.status_code == 200
    assert captured == [
        (
            "http://formal-sales/api/v1/demo/opening",
            {"customer_id": "customer-1", "customer_name": "Demo customer"},
        ),
        (
            "http://formal-sales/api/v1/demo/chat",
            {"customer_id": "customer-1", "message": "hello"},
        ),
    ]


def test_gateway_reports_missing_upstream(monkeypatch):
    _reset_settings(monkeypatch, upstream="")

    response = TestClient(render_gateway.app).post(
        "/api/v1/demo/opening", json={"customer_id": "customer-1"}
    )

    assert response.status_code == 503
    assert response.json()["message"] == "正式销售服务未配置"


def test_gateway_does_not_expose_formal_application_routes(monkeypatch):
    _reset_settings(monkeypatch)
    client = TestClient(render_gateway.app)

    assert client.get("/workbench").status_code == 404
    assert client.get("/gate").status_code == 404
    assert client.post("/api/v1/chat", json={}).status_code == 404
    assert client.post("/mcp", json={}).status_code == 404
