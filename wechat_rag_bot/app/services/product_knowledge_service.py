from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.models import YouzanProductKnowledgeModel, YouzanProductModel
from app.services.youzan_product_sync_service import _session


_FIELDS = (
    "product_name",
    "aliases",
    "category",
    "flower_color",
    "fragrance",
    "flowering_status",
    "price_budget",
    "care_scenes",
    "bloom_period",
    "audience_tag",
    "market_price",
    "highlighted_features",
    "sales_copy",
)
PREFERENCE_TERMS = (
    "好养",
    "易活",
    "新手",
    "性价比",
    "便宜",
    "浓香",
    "清香",
    "花香",
    "带花",
    "花苞",
)


def list_product_knowledge(
    *,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    linked: bool | None = None,
) -> dict[str, Any]:
    with _session() as session:
        filters = []
        if keyword and keyword.strip():
            value = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    YouzanProductKnowledgeModel.product_name.ilike(value),
                    YouzanProductKnowledgeModel.aliases.ilike(value),
                    YouzanProductKnowledgeModel.category.ilike(value),
                    YouzanProductKnowledgeModel.highlighted_features.ilike(value),
                    YouzanProductKnowledgeModel.sales_copy.ilike(value),
                )
            )
        if linked is True:
            filters.append(YouzanProductKnowledgeModel.item_id.is_not(None))
        elif linked is False:
            filters.append(YouzanProductKnowledgeModel.item_id.is_(None))
        query = select(YouzanProductKnowledgeModel)
        count_query = select(func.count()).select_from(YouzanProductKnowledgeModel)
        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)
        rows = list(
            session.scalars(
                query.order_by(
                    YouzanProductKnowledgeModel.category,
                    YouzanProductKnowledgeModel.product_name,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        products = _products_by_id(session, [row.item_id for row in rows if row.item_id])
        return {
            "items": [_serialize(row, products.get(row.item_id or "")) for row in rows],
            "total": int(session.scalar(count_query) or 0),
            "page": page,
            "page_size": page_size,
            "linked_count": int(
                session.scalar(
                    select(func.count())
                    .select_from(YouzanProductKnowledgeModel)
                    .where(YouzanProductKnowledgeModel.item_id.is_not(None))
                )
                or 0
            ),
        }


def list_product_options() -> list[dict[str, Any]]:
    with _session() as session:
        linked = {
            item_id
            for item_id in session.scalars(
                select(YouzanProductKnowledgeModel.item_id).where(
                    YouzanProductKnowledgeModel.item_id.is_not(None)
                )
            )
            if item_id
        }
        return [
            {
                "item_id": row.item_id,
                "title": row.title,
                "status": row.status,
                "image_url": row.image_url,
                "linked": row.item_id in linked,
            }
            for row in session.scalars(
                select(YouzanProductModel).order_by(
                    YouzanProductModel.title,
                    YouzanProductModel.id,
                )
            )
        ]


def create_product_knowledge(payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with _session() as session:
        _validate_link(session, payload.get("item_id"))
        name = _required_name(payload)
        if session.scalar(
            select(YouzanProductKnowledgeModel.id).where(
                YouzanProductKnowledgeModel.product_name == name
            )
        ):
            raise ValueError("产品名称已存在")
        row = YouzanProductKnowledgeModel(
            product_name=name,
            created_at=now,
            updated_at=now,
        )
        _apply_payload(row, payload)
        session.add(row)
        session.flush()
        if row.item_id is None:
            _auto_link(session)
        session.commit()
        session.refresh(row)
        product = session.scalar(
            select(YouzanProductModel).where(YouzanProductModel.item_id == row.item_id)
        ) if row.item_id else None
        return _serialize(row, product)


def update_product_knowledge(record_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with _session() as session:
        row = session.get(YouzanProductKnowledgeModel, record_id)
        if row is None:
            raise LookupError("产品知识不存在")
        _validate_link(session, payload.get("item_id"), current_id=record_id)
        name = _required_name(payload)
        duplicate = session.scalar(
            select(YouzanProductKnowledgeModel.id).where(
                YouzanProductKnowledgeModel.product_name == name,
                YouzanProductKnowledgeModel.id != record_id,
            )
        )
        if duplicate:
            raise ValueError("产品名称已存在")
        _apply_payload(row, payload)
        row.updated_at = _now()
        session.commit()
        session.refresh(row)
        product = session.scalar(
            select(YouzanProductModel).where(YouzanProductModel.item_id == row.item_id)
        ) if row.item_id else None
        return _serialize(row, product)


def delete_product_knowledge(record_id: int) -> None:
    with _session() as session:
        row = session.get(YouzanProductKnowledgeModel, record_id)
        if row is None:
            raise LookupError("产品知识不存在")
        session.delete(row)
        session.commit()


def import_product_knowledge(records: list[dict[str, Any]]) -> dict[str, int]:
    now = _now()
    with _session() as session:
        existing = {
            row.product_name: row
            for row in session.scalars(select(YouzanProductKnowledgeModel))
        }
        imported = 0
        for payload in records:
            name = _required_name(payload)
            row = existing.get(name)
            if row is None:
                row = YouzanProductKnowledgeModel(
                    product_name=name,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                existing[name] = row
            preserved_item_id = row.item_id
            _apply_payload(row, payload, keep_empty_item_id=True)
            if not payload.get("item_id"):
                row.item_id = preserved_item_id
            row.updated_at = now
            imported += 1
        session.flush()
        newly_linked = _auto_link(session)
        session.commit()
        linked_count = int(
            session.scalar(
                select(func.count())
                .select_from(YouzanProductKnowledgeModel)
                .where(YouzanProductKnowledgeModel.item_id.is_not(None))
            )
            or 0
        )
        return {
            "imported_count": imported,
            "linked_count": linked_count,
            "newly_linked_count": newly_linked,
            "unlinked_count": max(0, len(existing) - linked_count),
        }


def auto_link_knowledge_records() -> int:
    with _session() as session:
        count = _auto_link(session)
        session.commit()
        return count


def search_catalog_products(keyword: str, *, limit: int = 3) -> list[dict[str, Any]]:
    normalized_keyword = _normalize_name(keyword)
    if not normalized_keyword:
        return []
    query_terms = [
        term
        for term in (
            _normalize_name(value)
            for value in re.split(
                r"[\s,，。；;、]+|(?:适合|有没有|推荐|想要|想找|帮我|给我|几款|一款|哪款|哪种|兰花|的|和)",
                keyword,
            )
        )
        if len(term) >= 2
    ]
    with _session() as session:
        rows = session.execute(
            select(YouzanProductModel, YouzanProductKnowledgeModel)
            .join(
                YouzanProductKnowledgeModel,
                YouzanProductKnowledgeModel.item_id == YouzanProductModel.item_id,
            )
            .where(YouzanProductModel.status == "on_sale")
        ).all()
        ranked = []
        for product, knowledge in rows:
            score = _match_score(
                normalized_keyword,
                product,
                knowledge,
                query_terms=query_terms,
            )
            if score > 0:
                ranked.append((score, product, knowledge))
        ranked.sort(key=lambda item: (-item[0], item[1].sort_order, item[1].id))
        return [
            _serialize_ai_product(product, knowledge)
            for _, product, knowledge in ranked[:limit]
        ]


def get_catalog_product(item_id: str) -> dict[str, Any] | None:
    with _session() as session:
        row = session.execute(
            select(YouzanProductModel, YouzanProductKnowledgeModel)
            .join(
                YouzanProductKnowledgeModel,
                YouzanProductKnowledgeModel.item_id == YouzanProductModel.item_id,
            )
            .where(
                YouzanProductModel.item_id == item_id,
                YouzanProductModel.status == "on_sale",
            )
        ).first()
        return _serialize_ai_product(*row) if row else None


def list_catalog_products(*, limit: int = 20) -> list[dict[str, Any]]:
    with _session() as session:
        rows = session.execute(
            select(YouzanProductModel, YouzanProductKnowledgeModel)
            .join(
                YouzanProductKnowledgeModel,
                YouzanProductKnowledgeModel.item_id == YouzanProductModel.item_id,
            )
            .where(YouzanProductModel.status == "on_sale")
            .order_by(YouzanProductModel.sort_order, YouzanProductModel.id)
            .limit(limit)
        ).all()
        return [_serialize_ai_product(product, knowledge) for product, knowledge in rows]


def _auto_link(session) -> int:
    products = list(session.scalars(select(YouzanProductModel)))
    used = {
        item_id
        for item_id in session.scalars(
            select(YouzanProductKnowledgeModel.item_id).where(
                YouzanProductKnowledgeModel.item_id.is_not(None)
            )
        )
        if item_id
    }
    count = 0
    for knowledge in session.scalars(
        select(YouzanProductKnowledgeModel).where(
            YouzanProductKnowledgeModel.item_id.is_(None)
        )
    ):
        needle = _normalize_name(knowledge.product_name)
        candidates = [
            product
            for product in products
            if product.item_id not in used
            and needle
            and needle in _normalize_name(product.title)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda product: (len(_normalize_name(product.title)), product.id))
        knowledge.item_id = candidates[0].item_id
        knowledge.updated_at = _now()
        used.add(candidates[0].item_id)
        count += 1
    return count


def _validate_link(session, item_id: str | None, *, current_id: int | None = None) -> None:
    item_id = str(item_id or "").strip()
    if not item_id:
        return
    if not session.scalar(
        select(YouzanProductModel.id).where(YouzanProductModel.item_id == item_id)
    ):
        raise ValueError("关联的有赞商品不存在")
    query = select(YouzanProductKnowledgeModel.id).where(
        YouzanProductKnowledgeModel.item_id == item_id
    )
    if current_id is not None:
        query = query.where(YouzanProductKnowledgeModel.id != current_id)
    if session.scalar(query):
        raise ValueError("该有赞商品已关联其他产品知识")


def _apply_payload(
    row: YouzanProductKnowledgeModel,
    payload: dict[str, Any],
    *,
    keep_empty_item_id: bool = False,
) -> None:
    for field in _FIELDS:
        value = str(payload.get(field) or "").strip()
        setattr(row, field, value or None)
    if not keep_empty_item_id or payload.get("item_id"):
        row.item_id = str(payload.get("item_id") or "").strip() or None


def _required_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("product_name") or "").strip()
    if not name:
        raise ValueError("产品名称不能为空")
    return name


def _products_by_id(session, item_ids: list[str]) -> dict[str, YouzanProductModel]:
    if not item_ids:
        return {}
    return {
        row.item_id: row
        for row in session.scalars(
            select(YouzanProductModel).where(YouzanProductModel.item_id.in_(item_ids))
        )
    }


def _serialize(
    row: YouzanProductKnowledgeModel,
    product: YouzanProductModel | None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "item_id": row.item_id,
        **{field: getattr(row, field) for field in _FIELDS},
        "linked_product": (
            {
                "item_id": product.item_id,
                "title": product.title,
                "status": product.status,
                "image_url": product.image_url,
            }
            if product is not None
            else None
        ),
        "created_at": _isoformat(row.created_at),
        "updated_at": _isoformat(row.updated_at),
    }


def _serialize_ai_product(
    product: YouzanProductModel,
    knowledge: YouzanProductKnowledgeModel,
) -> dict[str, Any]:
    settings = get_settings()
    values = {
        "item_id": product.item_id,
        "alias": product.alias or "",
        "kdt_id": settings.youzan_kdt_id,
    }
    try:
        page_path = settings.youzan_product_page_path_template.format(**values)
    except KeyError:
        page_path = product.page_url or ""
    return {
        "item_id": product.item_id,
        "title": product.title,
        "alias": product.alias or "",
        "price_cent": product.price_cent,
        "stock": product.stock,
        "image_url": product.image_url or "",
        "page_path": page_path or product.page_url or "",
        "h5_url": product.h5_url,
        "knowledge": {field: getattr(knowledge, field) for field in _FIELDS},
    }


def _match_score(
    keyword: str,
    product: YouzanProductModel,
    knowledge: YouzanProductKnowledgeModel,
    *,
    query_terms: list[str] | None = None,
) -> int:
    name = _normalize_name(knowledge.product_name)
    title = _normalize_name(product.title)
    aliases = _normalize_name(knowledge.aliases)
    if name and name in keyword:
        return 300 + len(name)
    if any(alias in keyword for alias in _split_aliases(knowledge.aliases)):
        return 280
    if keyword in title or keyword in name:
        return 200 + min(len(keyword), len(name))
    searchable = _normalize_name(
        " ".join(
            str(getattr(knowledge, field) or "")
            for field in (
                "aliases",
                "category",
                "flower_color",
                "fragrance",
                "flowering_status",
                "price_budget",
                "care_scenes",
                "bloom_period",
                "audience_tag",
                "market_price",
                "highlighted_features",
                "sales_copy",
            )
        )
    )
    if keyword and keyword in searchable:
        return 50
    matched_terms = {
        term
        for term in (query_terms or [])
        if term in title or term in name or term in aliases or term in searchable
    }
    matched_terms.update(
        term for term in PREFERENCE_TERMS if term in keyword and term in searchable
    )
    return 20 * len(matched_terms)


def _split_aliases(value: str | None) -> list[str]:
    return [
        normalized
        for item in re.split(r"[\s,，、;；/|]+", str(value or ""))
        if (normalized := _normalize_name(item))
    ]


def _normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
