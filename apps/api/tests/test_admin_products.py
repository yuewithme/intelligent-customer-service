from fastapi.testclient import TestClient
import pytest
import sqlite3

from app.core.config import get_settings
from app.main import app
from app.integrations.youzan.services.youzan_product_sync_service import (
    list_products,
    reset_product_store_for_tests,
    sync_youzan_products,
    update_product_note,
    update_product_sort,
)
from app.domains.catalog.services.product_knowledge_service import import_product_knowledge


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
                "count": 4,
                "items": [
                    {
                        "item_id": 1001,
                        "title": self.title,
                        "price": 2990,
                        "quantity": 8,
                        "image": "https://cdn.example.com/1001.jpg",
                        "detail_url": "https://h5.youzan.com/goods/1001",
                        "update_time": "2026-07-20 08:30:00",
                    },
                    {
                        "item_id": 1003,
                        "title": "兰悦会员专属链接【会员专用】",
                        "price": 2880,
                        "quantity": 90,
                    },
                    {
                        "item_id": 1004,
                        "title": "【浮雕圆筒】兰花专用紫砂盆",
                        "price": 3990,
                        "quantity": 12,
                    },
                    {
                        "item_id": 1005,
                        "title": "兰花售后养护服务大礼包",
                        "price": 100,
                        "quantity": 99,
                    },
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
        if str(params["item_id"]) == "1005":
            return {
                "item": {
                    "item_id": 1005,
                    "title": "兰花售后养护服务大礼包",
                    "price": 100,
                    "quantity": 99,
                    "skus": [],
                }
            }
        if str(params["item_id"]) == "1004":
            return {
                "item": {
                    "item_id": 1004,
                    "title": "【浮雕圆筒】兰花专用紫砂盆",
                    "price": 3990,
                    "quantity": 12,
                    "skus": [],
                }
            }
        if str(params["item_id"]) == "1003":
            return {
                "item": {
                    "item_id": 1003,
                    "title": "兰悦会员专属链接【会员专用】",
                    "price": 2880,
                    "quantity": 90,
                    "skus": [],
                }
            }
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
                            "image_url": "https://cdn.example.com/1001-sku.jpg",
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

    assert first["product_count"] == 5
    assert first["sku_count"] == 1
    assert second["trigger"] == "scheduled"
    assert {item["item_id"] for item in data["items"]} == {
        "1001",
        "1002",
        "1003",
        "1004",
        "1005",
    }
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
    assert hidden_data["product_total"] == 1
    assert hidden_data["knowledge_linked_count"] == 0
    assert hidden_data["items"][0]["has_knowledge"] is False
    assert imported.status_code == 200
    assert imported.json()["data"]["newly_linked_count"] == 1
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["has_knowledge"] is True
    assert updated.status_code == 200
    assert updated.json()["data"]["sort_order"] == 7

    hidden_sold_out = client.get(
        "/api/v1/admin/products",
        params={"status": "sold_out", "knowledge_linked": "false"},
    )
    assert hidden_sold_out.status_code == 200
    assert hidden_sold_out.json()["data"]["total"] == 0


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

    from app.domains.catalog.services.product_knowledge_service import search_catalog_products

    products = search_catalog_products("适合新手的建兰")
    recommendation = search_catalog_products("推荐几款花香浓、适合新手的兰花")

    assert imported.status_code == 200
    assert updated.status_code == 200
    assert products[0]["item_id"] == "1001"
    assert products[0]["image_urls"] == [
        "https://cdn.example.com/1001.jpg",
        "https://cdn.example.com/1001-sku.jpg",
    ]
    assert "适合新手" in products[0]["knowledge"]["highlighted_features"]
    assert recommendation[0]["item_id"] == "1001"


@pytest.mark.asyncio
async def test_recommendation_respects_budget_level_and_product_preferences(
    monkeypatch,
    tmp_path,
):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeYouzanClient())
    import_product_knowledge(
        [
            {
                "item_id": "1001",
                "product_name": "建兰皇帝",
                "category": "建兰",
                "flower_color": "红花",
                "fragrance": "浓香",
                "flowering_status": "带花",
                "price_budget": "历史资料500元",
                "care_scenes": "阳台,室内",
                "bloom_period": "6月-11月",
                "audience_tag": "L2",
                "highlighted_features": "好养，性价比高",
            },
            {
                "item_id": "1003",
                "product_name": "建兰红香",
                "category": "建兰",
                "flower_color": "红花",
                "fragrance": "浓香",
                "flowering_status": "带花",
                "care_scenes": "阳台,室内",
                "bloom_period": "1月-3月",
                "audience_tag": "L4",
            },
            {
                "item_id": "1004",
                "product_name": "建兰素心",
                "category": "建兰",
                "flower_color": "素心",
                "fragrance": "清香",
                "flowering_status": "无花",
                "care_scenes": "阳台,室内",
                "audience_tag": "L2",
            },
            {
                "item_id": "1005",
                "product_name": "建兰低价红香",
                "category": "建兰",
                "flower_color": "红花",
                "fragrance": "浓香",
                "flowering_status": "无花",
                "care_scenes": "室外",
                "audience_tag": "L2",
            },
        ]
    )

    from app.domains.catalog.services.product_knowledge_service import search_catalog_products

    strict = search_catalog_products(
        "我是L2，预算30元以内，想要浓香、带花、适合阳台的建兰"
    )
    no_flower = search_catalog_products("L2想要素花，不要带花苞，放阳台")
    long_bloom = search_catalog_products("预算30元左右，想要红色、花期长的")
    good_value = search_catalog_products("L2预算30元以内，想要性价比高的建兰")
    nearby_level = search_catalog_products("L1客户，想要性价比高、适合阳台的建兰")
    exact_level = search_catalog_products("L4客户，预算50元以内，推荐带花的浓香建兰")
    direct = search_catalog_products("建兰皇帝，预算1元")

    assert [item["item_id"] for item in strict] == ["1001", "1003"]
    assert [item["item_id"] for item in no_flower] == ["1004"]
    assert [item["item_id"] for item in long_bloom] == ["1001"]
    assert [item["item_id"] for item in good_value] == ["1001"]
    assert [item["item_id"] for item in nearby_level] == ["1001"]
    assert exact_level[0]["item_id"] == "1003"
    assert direct[0]["item_id"] == "1001"


@pytest.mark.asyncio
async def test_product_alias_drives_local_ai_catalog(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeYouzanClient())
    import_product_knowledge(
        [
            {
                "product_name": "建兰皇帝",
                "aliases": "逸红双娇，小国魂",
                "category": "建兰",
                "highlighted_features": "花香清幽",
            }
        ]
    )

    from app.domains.catalog.services.product_knowledge_service import search_catalog_products

    products = search_catalog_products("有没有逸红双娇")

    assert products[0]["item_id"] == "1001"
    assert products[0]["knowledge"]["aliases"] == "逸红双娇，小国魂"


@pytest.mark.asyncio
async def test_legacy_aliases_are_backfilled_once_into_new_catalog(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeYouzanClient())
    import_product_knowledge(
        [{"product_name": "建兰皇帝", "category": "建兰"}]
    )

    database_path = tmp_path / "products.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE orchid_varieties (
                variety_name TEXT,
                primary_alias TEXT,
                aliases_text TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO orchid_varieties VALUES (?, ?, ?)",
            ("建兰皇帝", "皇帝梅", "皇帝梅，帝王梅，无，部分文献称“皇帝兰”"),
        )

    reset_product_store_for_tests()

    from app.domains.catalog.services.product_knowledge_service import list_product_knowledge

    item = list_product_knowledge()["items"][0]

    assert item["aliases"] == "皇帝梅，帝王梅"
