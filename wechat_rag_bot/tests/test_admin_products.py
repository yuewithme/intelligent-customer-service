from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import app
from app.services.youzan_product_sync_service import (
    list_products,
    reset_product_store_for_tests,
    sync_youzan_products,
    update_product_note,
    update_product_sort,
)
from app.services.product_knowledge_service import import_product_knowledge


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'products.db').as_posix()}")
    monkeypatch.setenv("YOUZAN_ENABLED", "true")
    monkeypatch.setenv("YOUZAN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("YOUZAN_PRODUCT_SYNC_PAGE_SIZE", "2")
    monkeypatch.setenv("YOUZAN_PRODUCT_DETAIL_ENABLED", "true")
    get_settings.cache_clear()
    reset_product_store_for_tests()


class FakeYouzanClient:
    def __init__(self):
        self.title = "建兰皇帝"

    async def call(self, method, version, params):
        del version
        if method == "youzan.items.onsale.get":
            return {
                "count": 1,
                "items": [
                    {
                        "item_id": 1001,
                        "title": self.title,
                        "price": 2990,
                        "quantity": 8,
                        "image": "https://cdn.example.com/1001.jpg",
                        "detail_url": "https://h5.youzan.com/goods/1001",
                        "update_time": "2026-07-20 08:30:00",
                    }
                ],
            }
        if method == "youzan.items.inventory.get":
            if params.get("banner") == "sold_out":
                return {
                    "count": 1,
                    "items": [
                        {
                            "item_id": 1002,
                            "title": "春兰宋梅",
                            "price": 5880,
                            "quantity": 0,
                        }
                    ],
                }
            return {"count": 0, "items": []}
        if str(params["item_id"]) == "1001":
            return {
                "item": {
                    "item_id": 1001,
                    "title": self.title,
                    "price": 2990,
                    "quantity": 8,
                    "skus": [
                        {
                            "sku_id": 501,
                            "properties_name": "苗数:3苗;发货:带盆",
                            "price": 3290,
                            "stock_num": 5,
                            "sku_no": "JL-3-POT",
                        }
                    ],
                }
            }
        return {"item": {"item_id": 1002, "title": "春兰宋梅", "skus": []}}


@pytest.mark.asyncio
async def test_sync_persists_products_skus_and_keeps_manual_sort(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    client = FakeYouzanClient()

    first = await sync_youzan_products(client=client)
    update_product_sort("1001", 20)
    update_product_note("1001", "带花苞，带盆")
    client.title = "建兰皇帝（新）"
    second = await sync_youzan_products(client=client, trigger="scheduled")
    import_product_knowledge(
        [
            {"product_name": "建兰皇帝"},
            {"product_name": "春兰宋梅"},
        ]
    )
    data = list_products(sort_by="manual")

    assert first["product_count"] == 2
    assert first["sku_count"] == 1
    assert second["trigger"] == "scheduled"
    assert [item["item_id"] for item in data["items"]] == ["1002", "1001"]
    product = next(item for item in data["items"] if item["item_id"] == "1001")
    assert product["title"] == "建兰皇帝（新）"
    assert product["sort_order"] == 20
    assert product["internal_note"] == "带花苞，带盆"
    assert product["status"] == "on_sale"
    assert product["skus"][0]["spec_name"] == "苗数:3苗 / 发货:带盆"
    assert product["skus"][0]["stock"] == 5


@pytest.mark.asyncio
async def test_product_admin_api_lists_and_updates_sort(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeYouzanClient())
    client = TestClient(app)

    hidden = client.get("/api/v1/admin/products", params={"status": "on_sale"})
    imported = client.post(
        "/api/v1/admin/products/knowledge/import",
        json={
            "records": [
                {
                    "product_name": "建兰皇帝",
                    "category": "建兰",
                    "highlighted_features": "花香清幽，适合阳台养护",
                }
            ]
        },
    )
    response = client.get("/api/v1/admin/products", params={"status": "on_sale"})
    updated = client.put("/api/v1/admin/products/1001/sort", json={"sort_order": 7})

    hidden_data = hidden.json()["data"]
    assert hidden_data["total"] == 1
    assert hidden_data["product_total"] == 2
    assert hidden_data["knowledge_linked_count"] == 0
    assert hidden_data["items"][0]["has_knowledge"] is False
    assert imported.status_code == 200
    assert imported.json()["data"]["newly_linked_count"] == 1
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["has_knowledge"] is True
    assert updated.status_code == 200
    assert updated.json()["data"]["sort_order"] == 7

    unlinked = client.get(
        "/api/v1/admin/products",
        params={"status": "sold_out", "knowledge_linked": "false"},
    )
    assert unlinked.status_code == 200
    assert unlinked.json()["data"]["total"] == 1
    assert unlinked.json()["data"]["items"][0]["item_id"] == "1002"


@pytest.mark.asyncio
async def test_creating_knowledge_auto_links_matching_product(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    youzan = FakeYouzanClient()
    await sync_youzan_products(client=youzan)
    client = TestClient(app)

    created = client.post(
        "/api/v1/admin/products/knowledge",
        json={
            "product_name": youzan.title,
            "category": "建兰",
            "highlighted_features": "花香清幽",
        },
    )

    assert created.status_code == 200
    assert created.json()["data"]["item_id"] == "1001"
    linked = client.get(
        "/api/v1/admin/products",
        params={"knowledge_linked": "true"},
    )
    assert linked.status_code == 200
    assert linked.json()["data"]["total"] == 1
    assert linked.json()["data"]["items"][0]["has_knowledge"] is True


@pytest.mark.asyncio
async def test_knowledge_is_editable_and_drives_local_ai_catalog(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeYouzanClient())
    client = TestClient(app)
    imported = client.post(
        "/api/v1/admin/products/knowledge/import",
        json={
            "records": [
                {
                    "product_name": "建兰皇帝",
                    "category": "建兰",
                    "flower_color": "黄绿色",
                    "fragrance": "浓香",
                    "highlighted_features": "经典名品，香味浓郁",
                }
            ]
        },
    )
    record_id = client.get("/api/v1/admin/products/knowledge").json()["data"]["items"][0]["id"]
    updated = client.put(
        f"/api/v1/admin/products/knowledge/{record_id}",
        json={
            "item_id": "1001",
            "product_name": "建兰皇帝",
            "category": "建兰",
            "flower_color": "黄绿色",
            "fragrance": "浓香",
            "highlighted_features": "经典名品，花香浓郁，适合新手",
        },
    )

    from app.services.product_knowledge_service import search_catalog_products

    products = search_catalog_products("适合新手的建兰")

    assert imported.status_code == 200
    assert updated.status_code == 200
    assert products[0]["item_id"] == "1001"
    assert "适合新手" in products[0]["knowledge"]["highlighted_features"]
