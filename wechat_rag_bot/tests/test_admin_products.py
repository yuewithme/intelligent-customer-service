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

    response = client.get("/api/v1/admin/products", params={"status": "on_sale"})
    updated = client.put("/api/v1/admin/products/1001/sort", json={"sort_order": 7})

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert updated.status_code == 200
    assert updated.json()["data"]["sort_order"] == 7
