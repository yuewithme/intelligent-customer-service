import sqlite3

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.catalog.api.library_resources import router
from app.domains.catalog.services import library_export
from app.shared.schemas.common import AppError


def test_export_excludes_customer_data_and_embeddings(tmp_path, monkeypatch):
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE orchid_common_knowledge (id INTEGER PRIMARY KEY, content TEXT);
        INSERT INTO orchid_common_knowledge VALUES(7, '养护资料');
        CREATE TABLE orchid_knowledge_chunks (id INTEGER PRIMARY KEY, content TEXT, embedding_json TEXT);
        INSERT INTO orchid_knowledge_chunks VALUES(9, '养护索引', '[0.1]');
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, private_content TEXT);
        INSERT INTO conversations VALUES(1, 'private customer');
        """)
    from types import SimpleNamespace
    monkeypatch.setattr(library_export, "get_settings", lambda: SimpleNamespace(database_url=f"sqlite:///{path}", chat_log_db_url=f"sqlite:///{path}", youzan_kdt_id="", youzan_product_page_path_template=""))
    from app.domains.catalog.services import agent_media_library_service as media
    monkeypatch.setattr(media, "_load_items", lambda: ())
    result = library_export.export_content(inventory=False)
    assert result["tables"]["orchid_common_knowledge"]["rows"] == [{"id": 7, "content": "养护资料"}]
    assert "conversations" not in result["tables"]
    assert "embedding_json" not in result["tables"]["orchid_knowledge_chunks"]["rows"][0]
    inventory = library_export.export_content()
    assert "rows" not in inventory["tables"]["orchid_common_knowledge"]
    assert inventory["tables"]["orchid_common_knowledge"]["sha256"] == result["tables"]["orchid_common_knowledge"]["sha256"]


def test_export_requires_verified_service(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(AppError)
    async def handle(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "denied"}, status_code=exc.status_code)

    async def verify(request):
        assert request.headers["X-App-Id"] == "sales-review"
        return httpx.Response(403)

    app.state.platform_client = httpx.AsyncClient(transport=httpx.MockTransport(verify))
    monkeypatch.setattr(library_export, "export_content", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not read")))
    with TestClient(app) as client:
        assert client.get("/api/library-export").status_code == 401
        assert client.get("/api/library-export", headers={"X-App-Id": "sales-review", "X-App-Key": "invalid"}).status_code == 403


def test_freeze_preserves_rules_and_customer_writes(tmp_path, monkeypatch):
    import pytest
    from types import SimpleNamespace
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE orchid_common_knowledge (id INTEGER PRIMARY KEY, content TEXT);
        INSERT INTO orchid_common_knowledge VALUES(1,'original');
        CREATE TABLE prompt_blocks (content TEXT);
        CREATE TABLE conversations (content TEXT);
        """)
    monkeypatch.setattr(library_export, "get_settings", lambda: SimpleNamespace(database_url=f"sqlite:///{path}", chat_log_db_url=f"sqlite:///{path}"))
    assert library_export.freeze_content()["frozen"] == ["orchid_common_knowledge"]
    library_export.freeze_content()
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="Content moved"):
            conn.execute("UPDATE orchid_common_knowledge SET content='changed'")
        conn.execute("INSERT INTO prompt_blocks VALUES('rule remains editable')")
        conn.execute("INSERT INTO conversations VALUES('customer runtime remains writable')")
