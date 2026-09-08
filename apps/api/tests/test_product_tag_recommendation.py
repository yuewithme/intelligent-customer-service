import sqlite3

from app.core.config import get_settings
from app.domains.catalog.services.product_knowledge_service import (
    _audience_level_distance, _now, create_product_knowledge,
    list_product_knowledge, search_catalog_products, update_product_knowledge,
)
from app.domains.catalog.services.product_recommendation_import import import_recommendation_catalog
from app.infrastructure.database.models import YouzanProductModel, YouzanProductSkuModel
from app.infrastructure.database.product_store import get_product_session, reset_product_store_for_tests


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}")
    get_settings.cache_clear()
    reset_product_store_for_tests()


def test_approved_table_import_preserves_other_knowledge_and_admin_edits(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    create_product_knowledge({"product_name": "云中白鹤（小白龙）", "bloom_period": "旧花期", "sales_copy": "原话术", "category": "旧分类"})
    assert import_recommendation_catalog() == 149
    data = list_product_knowledge(page_size=200)
    assert data["total"] == 149
    rows = {row["product_name"]: row for row in data["items"]}
    white = rows["云中白鹤"]
    assert (white["category"], white["bloom_period"], white["sales_copy"]) == ("建兰", "旧花期", "原话术")
    assert rows["红玉素"]["demand_tags"] == ["红素"]
    assert rows["红玉素"]["source_demand"] == "色花·红素"
    assert rows["大果冠"]["demand_tags"] == ["色花", "荷型"]
    assert rows["蕙兰虎斑"]["category"] == "寒兰"
    assert rows["福荷素8苗"]["spec_hint"] == "8苗"
    update_product_knowledge(white["id"], {**white, "seeding_scene": "后台修改"})
    reset_product_store_for_tests()
    assert import_recommendation_catalog() == 0
    assert list_product_knowledge(keyword="云中白鹤")["items"][0]["seeding_scene"] == "后台修改"
    filtered = list_product_knowledge(keyword="云中白鹤")
    assert filtered["total"] == 1
    assert filtered["knowledge_count"] == 149
    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        connection.execute("CREATE TABLE orchid_varieties (variety_name TEXT, primary_alias TEXT, aliases_text TEXT)")
        connection.execute("INSERT INTO orchid_varieties VALUES (?, ?, ?)", ("红草红荷", "红草", "红草"))
    reset_product_store_for_tests()
    red = list_product_knowledge(keyword="红草红荷")["items"][0]
    assert "红草" not in (red["aliases"] or "").split("，")


def test_matching_requires_affordable_in_stock_spec_and_ranks_profile(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    now = _now()
    with get_product_session() as session:
        for item_id in ("lotus", "red", "shape", "expensive"):
            session.add(YouzanProductModel(item_id=item_id, title=item_id, status="on_sale", price_cent=1000, stock=10, sort_order=0, created_at=now, updated_at=now, last_synced_at=now))
        session.add_all([
            YouzanProductSkuModel(item_id="lotus", sku_id="cheap", spec_name="3苗", price_cent=3000, stock=0, last_synced_at=now),
            YouzanProductSkuModel(item_id="lotus", sku_id="available", spec_name="8苗", price_cent=8000, stock=2, last_synced_at=now),
            YouzanProductSkuModel(item_id="expensive", sku_id="over", spec_name="3苗", price_cent=15000, stock=3, last_synced_at=now),
        ])
        session.commit()
    for item_id, name, tags, level in (
        ("lotus", "测试荷瓣", ["色花", "荷瓣"], "L1-L3"),
        ("red", "测试红素", ["红素"], "L2-L4"),
        ("shape", "测试荷型", ["色花", "荷型"], "L1-L3"),
        ("expensive", "测试高价", ["色花", "荷瓣"], "L1-L3"),
    ):
        create_product_knowledge({"item_id": item_id, "product_name": name, "category": "建兰", "fragrance": "浓香", "demand_tags": tags, "audience_tag": level, "care_scenes": "阳台 / 室内"})
    results = search_catalog_products("推荐建兰荷瓣色花，预算100元以内", preference_tags=["L2", "阳台"])
    assert [row["item_id"] for row in results] == ["lotus"]
    assert results[0]["price_cent"] == 8000
    assert [sku["sku_id"] for sku in results[0]["matching_skus"]] == ["available"]
    assert "客户等级适配：L1-L3" in results[0]["match_reasons"]
    assert search_catalog_products("测试荷瓣，预算50元以内") == []
    alternatives = search_catalog_products("推荐", preference_tags=["荷瓣", "L2", "100元以内"])
    assert alternatives[0]["item_id"] == "lotus"
    assert "red" in {row["item_id"] for row in alternatives}
    assert [row["item_id"] for row in search_catalog_products("推荐红素，预算100元以内")] == ["red"]
    assert [row["item_id"] for row in search_catalog_products("推荐荷型，预算100元以内")] == ["shape"]
    assert len(search_catalog_products("推荐色花或红素，预算100元以内")) == 3
    assert _audience_level_distance("L3", "L1-L3") == 0
    assert _audience_level_distance("L5", "L1-L3") == 2
