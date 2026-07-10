from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    OrchidCategoryModel,
    OrchidCommonKnowledgeModel,
    OrchidHotBreakdownModel,
    OrchidKnowledgeChunkModel,
    OrchidSalesCopyModel,
    OrchidSkuModel,
    OrchidValuePointModel,
    OrchidVarietyModel,
    OrchidVarietyTraitModel,
)
from app.orchid_products.excel_importer import OrchidImportPayload


_sessionmakers: dict[str, sessionmaker] = {}
_TABLES = [
    OrchidCategoryModel.__table__,
    OrchidVarietyModel.__table__,
    OrchidVarietyTraitModel.__table__,
    OrchidValuePointModel.__table__,
    OrchidSkuModel.__table__,
    OrchidCommonKnowledgeModel.__table__,
    OrchidSalesCopyModel.__table__,
    OrchidHotBreakdownModel.__table__,
    OrchidKnowledgeChunkModel.__table__,
]


def get_session_factory() -> sessionmaker:
    db_url = get_settings().database_url
    factory = _sessionmakers.get(db_url)
    if factory is None:
        engine = create_engine(db_url)
        Base.metadata.create_all(engine, tables=_TABLES)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[db_url] = factory
    return factory


def replace_orchid_product_library(payload: OrchidImportPayload) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        _clear(session)
        session.add_all(OrchidCategoryModel(**row) for row in payload.categories)
        session.flush()
        category_ids = {
            row.category_name: row.id
            for row in session.query(OrchidCategoryModel).all()
        }

        variety_models: list[OrchidVarietyModel] = []
        for row in payload.varieties:
            data = dict(row)
            data["category_id"] = category_ids.get(str(data.get("category_name") or ""))
            variety_models.append(OrchidVarietyModel(**data))
        session.add_all(variety_models)
        session.flush()
        variety_ids = {
            (row.category_name, row.variety_name): row.id
            for row in session.query(OrchidVarietyModel).all()
        }

        session.add_all(
            OrchidVarietyTraitModel(
                **_with_variety_id(row, variety_ids)
            )
            for row in payload.traits
        )
        session.add_all(
            OrchidValuePointModel(
                **_with_variety_id(row, variety_ids)
            )
            for row in payload.value_points
        )
        session.add_all(OrchidSkuModel(**row) for row in payload.skus)
        session.add_all(OrchidCommonKnowledgeModel(**row) for row in payload.common_knowledge)
        session.add_all(OrchidSalesCopyModel(**row) for row in payload.sales_copy)
        session.add_all(OrchidHotBreakdownModel(**row) for row in payload.hot_breakdowns)
        session.add_all(OrchidKnowledgeChunkModel(**row) for row in payload.knowledge_chunks)
        session.commit()

    result: dict[str, Any] = dict(payload.counts)
    result["session_factory"] = factory
    return result


def list_orchid_skus(
    *,
    variety_names: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    with get_session_factory()() as session:
        query = session.query(OrchidSkuModel)
        if variety_names:
            query = query.filter(OrchidSkuModel.variety_name.in_(variety_names))
        rows = query.order_by(OrchidSkuModel.id).limit(max(1, min(limit, 100))).all()
        return [
            {
                "variety_name": row.variety_name,
                "category_name": row.category_name,
                "seedling_count": row.seedling_count,
                "package_spec": row.package_spec,
                "flower_bud_status": row.flower_bud_status,
                "price": row.price,
                "price_text": row.price_text,
            }
            for row in rows
        ]


def _clear(session: Session) -> None:
    for model in [
        OrchidKnowledgeChunkModel,
        OrchidHotBreakdownModel,
        OrchidSalesCopyModel,
        OrchidCommonKnowledgeModel,
        OrchidSkuModel,
        OrchidValuePointModel,
        OrchidVarietyTraitModel,
        OrchidVarietyModel,
        OrchidCategoryModel,
    ]:
        session.query(model).delete()


def _with_variety_id(row: dict[str, Any], variety_ids: dict[tuple[str, str], int]) -> dict[str, Any]:
    data = dict(row)
    key = (str(data.get("category_name") or ""), str(data.get("variety_name") or ""))
    data["variety_id"] = variety_ids.get(key)
    return data
