from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.infrastructure.database.models import (
    YouzanProductKnowledgeModel,
    YouzanProductModel,
    YouzanProductSkuModel,
)
from app.integrations.youzan.services.youzan_product_sync_service import _session


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
ORCHID_CATEGORIES = (
    "建兰",
    "春兰",
    "蕙兰",
    "墨兰",
    "寒兰",
    "春剑",
    "莲瓣兰",
    "秋芝",
    "送春",
)
SCENE_TERMS = ("阳台", "室内", "室外", "露台", "公司办公室", "办公室")
FRAGRANCE_TERMS = ("浓香", "清香", "幽香", "甜香")
COLOR_TERMS = {
    "红": ("红色", "红花", "红素"),
    "白": ("白色", "白花"),
    "黄": ("黄色", "黄花", "黄素"),
    "紫": ("紫色", "紫花"),
    "绿": ("绿色", "绿花"),
    "素": ("素花", "素心", "素雅"),
    "复色": ("复色",),
    "艳丽": ("艳丽",),
}


@dataclass(frozen=True)
class ProductRecommendationCriteria:
    min_price_cent: int | None = None
    max_price_cent: int | None = None
    target_price_cent: int | None = None
    audience_tag: str | None = None
    category: str | None = None
    fragrance: str | None = None
    flowering_status: str | None = None
    scene: str | None = None
    color_key: str | None = None
    requires_easy_care: bool = False
    requires_value: bool = False
    requires_long_bloom: bool = False

    @property
    def active_count(self) -> int:
        return sum(
            value is not None
            for value in (
                self.min_price_cent,
                self.max_price_cent,
                self.audience_tag,
                self.category,
                self.fragrance,
                self.flowering_status,
                self.scene,
                self.color_key,
                self.requires_easy_care or None,
                self.requires_value or None,
                self.requires_long_bloom or None,
            )
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
    criteria = _parse_recommendation_criteria(keyword)
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
            direct_score = _direct_product_score(
                normalized_keyword,
                product,
                knowledge,
            )
            if not direct_score and not _matches_recommendation_criteria(
                product,
                knowledge,
                criteria,
            ):
                continue
            score = _match_score(
                normalized_keyword,
                product,
                knowledge,
                query_terms=query_terms,
            )
            score += _criteria_score(product, criteria)
            if criteria.active_count and not direct_score:
                score = max(score, criteria.active_count * 10)
            if score > 0:
                audience_distance = (
                    -1
                    if direct_score
                    else _audience_level_distance(
                        criteria.audience_tag,
                        knowledge.audience_tag,
                    )
                )
                ranked.append((audience_distance, score, product, knowledge))
        ranked.sort(
            key=lambda item: (item[0], -item[1], item[2].sort_order, item[2].id)
        )
        selected = ranked[:limit]
        item_ids = [product.item_id for _, _, product, _ in selected]
        sku_image_urls: dict[str, list[str]] = {}
        if item_ids:
            for item_id, image_url in session.execute(
                select(
                    YouzanProductSkuModel.item_id,
                    YouzanProductSkuModel.image_url,
                ).where(
                    YouzanProductSkuModel.item_id.in_(item_ids),
                    YouzanProductSkuModel.image_url.is_not(None),
                )
            ):
                value = str(image_url or "").strip()
                if value:
                    sku_image_urls.setdefault(item_id, []).append(value)
        return [
            _serialize_ai_product(
                product,
                knowledge,
                image_urls=sku_image_urls.get(product.item_id),
            )
            for _, _, product, knowledge in selected
        ]


def _parse_recommendation_criteria(keyword: str) -> ProductRecommendationCriteria:
    min_price_cent = None
    max_price_cent = None
    target_price_cent = None
    price_range = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:到|至|[-~～])\s*(\d+(?:\.\d+)?)\s*元?",
        keyword,
    )
    if price_range:
        lower, upper = sorted(float(value) for value in price_range.groups())
        min_price_cent = round(lower * 100)
        max_price_cent = round(upper * 100)
        target_price_cent = round((lower + upper) * 50)
    else:
        price = re.search(
            r"预算(?:在|是|大概|约)?\s*(\d+(?:\.\d+)?)\s*元?\s*"
            r"(以内|以下|之内|不超过|最多|左右|上下)?",
            keyword,
        )
        if price is None:
            price = re.search(
                r"(\d+(?:\.\d+)?)\s*元?\s*"
                r"(以内|以下|之内|不超过|最多|左右|上下)",
                keyword,
            )
        if price:
            amount_cent = round(float(price.group(1)) * 100)
            qualifier = price.group(2) or ""
            if qualifier in {"左右", "上下"}:
                min_price_cent = round(amount_cent * 0.8)
                max_price_cent = round(amount_cent * 1.2)
                target_price_cent = amount_cent
            else:
                max_price_cent = amount_cent

    audience_match = re.search(
        r"(?<![A-Za-z0-9])L([1-6])(?![A-Za-z0-9])",
        keyword,
        re.I,
    )
    audience_tag = f"L{audience_match.group(1)}" if audience_match else None
    category = next((value for value in ORCHID_CATEGORIES if value in keyword), None)
    fragrance = next((value for value in FRAGRANCE_TERMS if value in keyword), None)
    if fragrance is None and any(value in keyword for value in ("香味浓", "香气浓", "浓郁")):
        fragrance = "浓香"

    negative_flower = any(
        value in keyword
        for value in ("不要带花", "不带花", "不要花苞", "不带花苞", "无花")
    )
    positive_flower = any(
        value in keyword for value in ("带花", "带花苞", "要花苞", "现花")
    )
    flowering_status = "无花" if negative_flower else ("带花" if positive_flower else None)
    scene = next((value for value in SCENE_TERMS if value in keyword), None)
    if scene == "办公室":
        scene = "公司办公室"
    color_key = next(
        (
            key
            for key, markers in COLOR_TERMS.items()
            if any(marker in keyword for marker in markers)
        ),
        None,
    )
    return ProductRecommendationCriteria(
        min_price_cent=min_price_cent,
        max_price_cent=max_price_cent,
        target_price_cent=target_price_cent,
        audience_tag=audience_tag,
        category=category,
        fragrance=fragrance,
        flowering_status=flowering_status,
        scene=scene,
        color_key=color_key,
        requires_easy_care=any(
            value in keyword for value in ("好养", "易养", "易活", "新手")
        ),
        requires_value=any(
            value in keyword for value in ("性价比", "实惠", "划算", "便宜")
        ),
        requires_long_bloom=any(
            value in keyword for value in ("花期长", "开花久", "花期久")
        ),
    )


def _matches_recommendation_criteria(
    product: YouzanProductModel,
    knowledge: YouzanProductKnowledgeModel,
    criteria: ProductRecommendationCriteria,
) -> bool:
    price_cent = product.price_cent
    if criteria.min_price_cent is not None and (
        price_cent is None or price_cent < criteria.min_price_cent
    ):
        return False
    if criteria.max_price_cent is not None and (
        price_cent is None or price_cent > criteria.max_price_cent
    ):
        return False
    if criteria.category and _normalize_name(knowledge.category) != _normalize_name(
        criteria.category
    ):
        return False
    if criteria.fragrance and _normalize_name(knowledge.fragrance) != _normalize_name(
        criteria.fragrance
    ):
        return False
    if criteria.flowering_status and _normalize_name(
        knowledge.flowering_status
    ) != _normalize_name(criteria.flowering_status):
        return False
    if criteria.scene and _normalize_name(criteria.scene) not in _normalize_name(
        knowledge.care_scenes
    ):
        return False
    if criteria.color_key and criteria.color_key not in _normalize_name(
        knowledge.flower_color
    ):
        return False
    knowledge_text = " ".join(
        str(getattr(knowledge, field) or "")
        for field in ("highlighted_features", "sales_copy", "care_scenes")
    )
    if criteria.requires_easy_care and not any(
        marker in knowledge_text for marker in ("好养", "易养", "易活", "皮实", "新手")
    ):
        return False
    if criteria.requires_value and not any(
        marker in knowledge_text
        for marker in ("性价比", "实惠", "划算", "亲民", "入门")
    ):
        return False
    if criteria.requires_long_bloom and not _has_long_bloom(knowledge):
        return False
    return True


def _audience_level_distance(requested: str | None, actual: str | None) -> int:
    """Treat L1-L6 as a ranking preference, never as a recommendation gate."""
    if not requested:
        return 0
    requested_match = re.fullmatch(r"L([1-6])", str(requested).strip(), re.I)
    actual_match = re.fullmatch(r"L([1-6])", str(actual or "").strip(), re.I)
    if not requested_match or not actual_match:
        return 99
    return abs(int(requested_match.group(1)) - int(actual_match.group(1)))


def _has_long_bloom(knowledge: YouzanProductKnowledgeModel) -> bool:
    text = " ".join(
        str(getattr(knowledge, field) or "")
        for field in ("bloom_period", "highlighted_features", "sales_copy")
    )
    if any(marker in text for marker in ("花期长", "花期较长", "开花时间久", "花期可达")):
        return True
    month_range = re.search(
        r"(\d{1,2})(?:月)?\s*[-至到]\s*(?:次年)?(\d{1,2})月",
        str(knowledge.bloom_period or ""),
    )
    if not month_range:
        return False
    start_month, end_month = (int(value) for value in month_range.groups())
    span = (end_month - start_month) % 12 + 1
    return span >= 4


def _criteria_score(
    product: YouzanProductModel,
    criteria: ProductRecommendationCriteria,
) -> int:
    score = criteria.active_count * 40
    if criteria.target_price_cent and product.price_cent is not None:
        difference_ratio = (
            abs(product.price_cent - criteria.target_price_cent)
            / criteria.target_price_cent
        )
        score += max(0, round(30 * (1 - difference_ratio)))
    return score


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
    *,
    image_urls: list[str] | None = None,
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
    gallery = []
    for image_url in [product.image_url, *(image_urls or [])]:
        value = str(image_url or "").strip()
        if value and value not in gallery:
            gallery.append(value)
    return {
        "item_id": product.item_id,
        "title": product.title,
        "alias": product.alias or "",
        "price_cent": product.price_cent,
        "stock": product.stock,
        "image_url": product.image_url or "",
        "image_urls": gallery,
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
    direct_score = _direct_product_score(keyword, product, knowledge)
    if direct_score:
        return direct_score
    name = _normalize_name(knowledge.product_name)
    title = _normalize_name(product.title)
    aliases = _normalize_name(knowledge.aliases)
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


def _direct_product_score(
    keyword: str,
    product: YouzanProductModel,
    knowledge: YouzanProductKnowledgeModel,
) -> int:
    name = _normalize_name(knowledge.product_name)
    title = _normalize_name(product.title)
    if name and name in keyword:
        return 300 + len(name)
    if any(alias in keyword for alias in _split_aliases(knowledge.aliases)):
        return 280
    if keyword and (keyword in title or keyword in name):
        return 200 + min(len(keyword), len(name))
    return 0


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
