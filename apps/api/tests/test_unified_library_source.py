import httpx
import pytest

from app.core.config import get_settings
from app.domains.knowledge.services import unified_library
from app.domains.catalog.services import product_knowledge_service
from app.shared.schemas.common import AppError


def test_product_reads_unified_source_without_local_fallback(monkeypatch):
    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)
    monkeypatch.setattr(product_knowledge_service, "get_product_session", lambda: pytest.fail("old source used"))
    observed = []

    def post(url, **kwargs):
        observed.append((url, kwargs["json"]))
        return httpx.Response(200, json={"data": {"item_id": "item-7", "title": "来自统一库"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert product_knowledge_service.get_catalog_product("item-7")["title"] == "来自统一库"
    assert observed[0][0].endswith("/operations/products.get_catalog_product")
    assert observed[0][1]["arguments"] == {"item_id": "item-7"}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    with pytest.raises(AppError):
        product_knowledge_service.get_catalog_product("item-7")


def test_supplier_detail_failures_do_not_become_empty_sku_updates(monkeypatch):
    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)
    captured = {}

    def call(operation, **args):
        captured.update(args)
        return {"product_count": 1}

    monkeypatch.setattr(unified_library, "call", call)
    from app.integrations.youzan.services.youzan_product_sync_service import _persist_sync
    _persist_sync({"item-7": {"_status": "on_sale"}}, {"item-7": RuntimeError("upstream unavailable")})
    assert captured["detail_results"]["item-7"] is None
