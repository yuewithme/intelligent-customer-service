from app.db.models import (
    OrchidCategoryModel,
    OrchidKnowledgeChunkModel,
    OrchidSkuModel,
    OrchidVarietyModel,
)
from app.config import get_settings
from app.orchid_products.excel_importer import OrchidImportPayload
from app.orchid_products import repository
from app.orchid_products.repository import replace_orchid_product_library


def test_replace_orchid_product_library_persists_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'orchid.db').as_posix()}")
    get_settings.cache_clear()
    repository._sessionmakers.clear()

    payload = OrchidImportPayload(
        categories=[{"category_name": "建兰", "category_description": None}],
        varieties=[
            {
                "category_id": None,
                "category_name": "建兰",
                "variety_name": "满堂红",
                "primary_alias": "红满堂",
                "aliases_text": "红满堂",
                "source_type": "下山兰",
                "origin_area": None,
                "history_background": "建兰八大红花之一。",
                "summary": None,
                "suitable_level": "L1-L2",
                "base_spec": "3-5苗",
                "base_price_text": "68-88",
                "raw_basic_info": "1.产品来源：下山兰",
            }
        ],
        traits=[],
        value_points=[],
        skus=[
            {
                "category_name": "建兰",
                "variety_name": "满堂红",
                "seedling_count": "3-5苗",
                "package_spec": "裸苗",
                "flower_bud_status": "带花剑",
                "price": 68.0,
                "price_text": "68",
            }
        ],
        common_knowledge=[],
        sales_copy=[],
        hot_breakdowns=[],
        knowledge_chunks=[
            {
                "source_table": "orchid_value_points",
                "source_id": None,
                "entity_type": "variety",
                "variety_name": "满堂红",
                "category_name": "建兰",
                "chunk_type": "产品来源",
                "chunk_title": "满堂红 - 产品来源",
                "content": "下山兰",
                "embedding_json": None,
            }
        ],
    )

    result = replace_orchid_product_library(payload)

    assert result["categories"] == 1
    with result["session_factory"]() as session:
        assert session.query(OrchidCategoryModel).count() == 1
        assert session.query(OrchidVarietyModel).one().variety_name == "满堂红"
        assert session.query(OrchidSkuModel).one().price == 68.0
        assert session.query(OrchidKnowledgeChunkModel).one().chunk_type == "产品来源"
