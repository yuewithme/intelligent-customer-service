from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    and_,
    asc,
    create_engine,
    delete,
    desc,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    YouzanProductKnowledgeModel,
    YouzanProductModel,
    YouzanProductSkuModel,
    YouzanProductSyncRunModel,
)
from app.integrations.youzan.client import YouzanClient
from app.integrations.youzan.services.youzan_token_service import (
    create_managed_youzan_client,
    youzan_credentials_available,
)


logger = logging.getLogger("wechat_rag_bot.youzan_product_sync")
_TABLES = [
    YouzanProductModel.__table__,
    YouzanProductSkuModel.__table__,
    YouzanProductSyncRunModel.__table__,
    YouzanProductKnowledgeModel.__table__,
]
_STATUS_PRIORITY = {"missing": 0, "off_shelf": 1, "sold_out": 2, "on_sale": 3}
_NON_FLOWER_TITLE_KEYWORDS = (
    "会员",
    "专属",
    "链接",
    "一物一拍",
    "私域",
    "拍卖",
    "竞拍",
    "补差",
    "差价",
    "运费",
    "测试",
    "勿拍",
    "不发货",
    "赠品",
    "福袋",
    "盲盒",
    "兰画",
)
_NON_FLOWER_PRODUCT_KEYWORDS = (
    "服务",
    "用品",
    "礼包",
    "礼盒",
    "课程",
    "咨询",
    "售后",
    "补发",
    "植料",
    "紫砂盆",
    "陶瓷盆",
    "塑料盆",
    "套盆",
    "高脚盆",
    "国风盆",
    "花器",
    "盆器",
    "肥料",
    "缓释肥",
    "营养液",
    "兰架",
    "花架",
    "支架",
    "托盘",
    "喷壶",
    "剪刀",
    "杀菌剂",
    "杀虫剂",
    "铺面石",
    "覆面土",
    "陶粒",
    "水苔",
    "挂画",
    "花鸟画",
    "国画",
)


@lru_cache
def _session_factory(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=_TABLES)
    if "alias" not in {
        column["name"] for column in inspect(engine).get_columns("youzan_products")
    }:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE youzan_products ADD COLUMN alias VARCHAR(128)")
            )
    _ensure_product_knowledge_aliases(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _ensure_product_knowledge_aliases(engine) -> None:
    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("youzan_product_knowledge")
    }
    if "aliases" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE youzan_product_knowledge ADD COLUMN aliases TEXT")
            )

    if "orchid_varieties" not in set(inspector.get_table_names()):
        return

    with engine.begin() as connection:
        legacy_rows = connection.execute(
            text(
                "SELECT variety_name, primary_alias, aliases_text "
                "FROM orchid_varieties"
            )
        ).mappings()
        legacy_aliases: dict[str, list[str]] = {}
        for row in legacy_rows:
            key = _normalize_product_name(row["variety_name"])
            if not key:
                continue
            values = legacy_aliases.setdefault(key, [])
            for value in (row["primary_alias"], row["aliases_text"]):
                values.extend(_alias_values(value))

        knowledge_rows = connection.execute(
            text("SELECT id, product_name, aliases FROM youzan_product_knowledge")
        ).mappings()
        for row in knowledge_rows:
            product_name = str(row["product_name"] or "").strip()
            candidates = [
                *_alias_values(row["aliases"]),
                *legacy_aliases.get(_normalize_product_name(product_name), []),
            ]
            aliases = []
            seen = {_normalize_product_name(product_name)}
            for alias in candidates:
                normalized = _normalize_product_name(alias)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                aliases.append(alias)
            value = "，".join(aliases)
            if value != str(row["aliases"] or "").strip():
                connection.execute(
                    text(
                        "UPDATE youzan_product_knowledge "
                        "SET aliases = :aliases WHERE id = :id"
                    ),
                    {"aliases": value or None, "id": row["id"]},
                )


def _alias_values(value: str | None) -> list[str]:
    aliases = []
    for item in re.split(r"[\s,，、;；/|]+|或", str(value or "")):
        alias = item.strip().strip("‘’“”\"'")
        if not alias or alias in {"无", "暂无", "未知", "不详", "待补充", "无别名"}:
            continue
        if any(marker in alias for marker in ("文献", "资料", "别名", "待补", "未确认")):
            continue
        if len(alias) > 32:
            continue
        aliases.append(alias)
    return aliases


def _normalize_product_name(value: str | None) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _session() -> Session:
    return _session_factory(get_settings().database_url)()


def reset_product_store_for_tests() -> None:
    _session_factory.cache_clear()


def list_products(
    *,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    status: str | None = None,
    sort_by: str = "manual",
    sort_direction: str = "asc",
    knowledge_linked: bool | None = None,
    knowledge_only: bool = False,
    catalog_only: bool = False,
) -> dict[str, Any]:
    with _session() as session:
        query = select(YouzanProductModel)
        count_query = select(func.count()).select_from(YouzanProductModel)
        filters = []
        knowledge_item_ids = select(YouzanProductKnowledgeModel.item_id).where(
            YouzanProductKnowledgeModel.item_id.is_not(None)
        )
        catalog_filters = _catalog_filters() if catalog_only else []
        filters.extend(catalog_filters)
        if knowledge_only or knowledge_linked is True:
            filters.append(
                YouzanProductModel.item_id.in_(knowledge_item_ids)
            )
        elif knowledge_linked is False:
            filters.append(YouzanProductModel.item_id.not_in(knowledge_item_ids))
        if keyword and keyword.strip():
            value = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    YouzanProductModel.title.ilike(value),
                    YouzanProductModel.item_id.ilike(value),
                )
            )
        if status:
            filters.append(YouzanProductModel.status == status)
        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)

        column = {
            "manual": YouzanProductModel.sort_order,
            "title": YouzanProductModel.title,
            "price": YouzanProductModel.price_cent,
            "stock": YouzanProductModel.stock,
            "updated_at": YouzanProductModel.youzan_updated_at,
        }.get(sort_by, YouzanProductModel.sort_order)
        direction = desc if sort_direction == "desc" else asc
        query = query.order_by(
            direction(column),
            asc(YouzanProductModel.id),
        ).offset((page - 1) * page_size).limit(page_size)
        rows = list(session.scalars(query))
        item_ids = [row.item_id for row in rows]
        sku_rows = (
            list(
                session.scalars(
                    select(YouzanProductSkuModel)
                    .where(YouzanProductSkuModel.item_id.in_(item_ids))
                    .order_by(
                        YouzanProductSkuModel.item_id,
                        YouzanProductSkuModel.id,
                    )
                )
            )
            if item_ids
            else []
        )
        skus_by_item: dict[str, list[dict[str, Any]]] = {}
        for sku in sku_rows:
            skus_by_item.setdefault(sku.item_id, []).append(_serialize_sku(sku))
        knowledge_by_item = {
            row.item_id: row
            for row in session.scalars(
                select(YouzanProductKnowledgeModel).where(
                    YouzanProductKnowledgeModel.item_id.in_(item_ids)
                )
            )
            if row.item_id
        } if item_ids else {}
        last_run = session.scalar(
            select(YouzanProductSyncRunModel).order_by(
                YouzanProductSyncRunModel.id.desc()
            )
        )
        return {
            "items": [
                _serialize_product(
                    row,
                    skus_by_item.get(row.item_id, []),
                    knowledge_by_item.get(row.item_id),
                )
                for row in rows
            ],
            "total": int(session.scalar(count_query) or 0),
            "product_total": int(
                session.scalar(
                    select(func.count())
                    .select_from(YouzanProductModel)
                    .where(*catalog_filters)
                )
                or 0
            ),
            "knowledge_linked_count": int(
                session.scalar(
                    select(func.count())
                    .select_from(YouzanProductModel)
                    .where(
                        *catalog_filters,
                        YouzanProductModel.item_id.in_(knowledge_item_ids),
                    )
                )
                or 0
            ),
            "page": page,
            "page_size": page_size,
            "last_sync": _serialize_sync_run(last_run),
        }


def _catalog_filters() -> list[Any]:
    title = YouzanProductModel.title
    no_generic_markers = and_(
        *(~title.ilike(f"%{keyword}%") for keyword in _NON_FLOWER_TITLE_KEYWORDS)
    )
    has_specific_bracketed_name = and_(
        title.ilike("%【%】%"),
        ~title.ilike("%【%会员%】%"),
        ~title.ilike("%【%专用%】%"),
        ~title.ilike("%【%链接%】%"),
        ~title.ilike("%【%一物一拍%】%"),
    )
    filters: list[Any] = [
        YouzanProductModel.status == "on_sale",
        YouzanProductModel.stock > 0,
        title.is_not(None),
        func.length(func.trim(title)) > 0,
        or_(no_generic_markers, has_specific_bracketed_name),
        *(~title.ilike(f"%{keyword}%") for keyword in _NON_FLOWER_PRODUCT_KEYWORDS),
    ]
    return filters


def update_product_sort(item_id: str, sort_order: int) -> dict[str, Any]:
    with _session() as session:
        row = session.scalar(
            select(YouzanProductModel).where(YouzanProductModel.item_id == item_id)
        )
        if row is None:
            raise LookupError("商品不存在")
        row.sort_order = sort_order
        row.updated_at = _now()
        session.commit()
        session.refresh(row)
        return _serialize_product(row, [])


def update_product_note(item_id: str, internal_note: str) -> dict[str, Any]:
    with _session() as session:
        row = session.scalar(
            select(YouzanProductModel).where(YouzanProductModel.item_id == item_id)
        )
        if row is None:
            raise LookupError("商品不存在")
        row.internal_note = internal_note.strip() or None
        row.updated_at = _now()
        session.commit()
        session.refresh(row)
        return _serialize_product(row, [])


async def sync_youzan_products(
    *,
    trigger: str = "manual",
    client: YouzanClient | Any | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.youzan_enabled or not youzan_credentials_available():
        raise RuntimeError("有赞商品同步未配置")
    client = client or create_managed_youzan_client()
    run_id = _start_sync_run(trigger)
    try:
        collections = await asyncio.gather(
            _fetch_all_pages(
                client,
                settings.youzan_product_search_method,
                settings.youzan_product_search_version,
                {},
            ),
            _fetch_all_pages(
                client,
                settings.youzan_inventory_method,
                settings.youzan_inventory_version,
                {"banner": "for_shelved"},
            ),
            _fetch_all_pages(
                client,
                settings.youzan_inventory_method,
                settings.youzan_inventory_version,
                {"banner": "sold_out"},
            ),
        )
        merged: dict[str, dict[str, Any]] = {}
        for status, items in zip(
            ("on_sale", "off_shelf", "sold_out"), collections, strict=True
        ):
            for item in items:
                item_id = _text(item, "item_id", "num_iid", "goods_id", "id")
                if not item_id:
                    continue
                current = merged.get(item_id)
                if current is None or _STATUS_PRIORITY[status] > _STATUS_PRIORITY[current["_status"]]:
                    merged[item_id] = {**item, "_status": status}

        detail_results = await _fetch_details(client, list(merged))
        result = _persist_sync(merged, detail_results)
        from app.domains.catalog.services.product_knowledge_service import auto_link_knowledge_records

        result["knowledge_linked_count"] = auto_link_knowledge_records()
        result["detail_error_count"] = sum(
            1 for value in detail_results.values() if isinstance(value, Exception)
        )
        _finish_sync_run(run_id, result=result)
        return {**result, "trigger": trigger, "status": "success"}
    except Exception as exc:
        _finish_sync_run(run_id, error=exc)
        raise


async def youzan_product_sync_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    if (
        not settings.youzan_product_sync_enabled
        or not settings.youzan_enabled
        or not youzan_credentials_available()
    ):
        return
    if await _wait_or_stop(stop_event, settings.youzan_product_sync_startup_delay_seconds):
        return
    while not stop_event.is_set():
        try:
            if _sync_is_due(settings.youzan_product_sync_interval_hours):
                await sync_youzan_products(trigger="scheduled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduled Youzan product sync failed: %s", type(exc).__name__)
        if await _wait_or_stop(stop_event, min(300, settings.youzan_product_sync_interval_hours * 3600)):
            return


async def _fetch_all_pages(
    client: Any,
    method: str,
    version: str,
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    page_size = get_settings().youzan_product_sync_page_size
    page_no = 1
    result: list[dict[str, Any]] = []
    while True:
        data = await client.call(
            method,
            version,
            {**extra, "page_no": page_no, "page_size": page_size},
        )
        items = _list_value(data, "items", "products", "goods_list")
        result.extend(item for item in items if isinstance(item, dict))
        total = _integer(data, "count", "total", "total_count")
        if not items or len(items) < page_size or (total is not None and len(result) >= total):
            break
        if page_no * page_size >= 4000:
            raise RuntimeError("有赞商品超过4000条，需要按更新时间分段同步")
        page_no += 1
    return result


async def _fetch_details(client: Any, item_ids: list[str]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.youzan_product_detail_enabled:
        return {}
    semaphore = asyncio.Semaphore(settings.youzan_product_sync_detail_concurrency)

    async def fetch(item_id: str) -> tuple[str, Any]:
        async with semaphore:
            try:
                data = await client.call(
                    settings.youzan_product_detail_method,
                    settings.youzan_product_detail_version,
                    {"item_id": item_id},
                )
                return item_id, data.get("item") if isinstance(data.get("item"), dict) else data
            except Exception as exc:  # noqa: BLE001
                return item_id, exc

    return dict(await asyncio.gather(*(fetch(item_id) for item_id in item_ids)))


def _persist_sync(
    items: dict[str, dict[str, Any]], detail_results: dict[str, Any]
) -> dict[str, int]:
    now = _now()
    sku_count = 0
    with _session() as session:
        existing = {
            row.item_id: row
            for row in session.scalars(select(YouzanProductModel))
        }
        for row in existing.values():
            if row.item_id not in items:
                row.status = "missing"
                row.last_synced_at = now
                row.updated_at = now
        for item_id, item in items.items():
            row = existing.get(item_id)
            if row is None:
                row = YouzanProductModel(
                    item_id=item_id,
                    title="",
                    status=item["_status"],
                    sort_order=0,
                    created_at=now,
                    updated_at=now,
                    last_synced_at=now,
                )
                session.add(row)
            detail = detail_results.get(item_id)
            source = {**item, **(detail if isinstance(detail, dict) else {})}
            row.title = _text(source, "title", "name") or row.title
            row.alias = _text(source, "alias", "handle") or row.alias
            row.image_url = _text(source, "image", "pic_url", "image_url") or None
            row.status = item["_status"]
            row.price_cent = _integer(source, "price", "price_cent")
            row.stock = _integer(source, "actual_quantity", "quantity", "stock_num", "stock")
            row.h5_url = _text(source, "detail_url", "h5_url") or None
            row.page_url = _text(source, "page_url") or None
            row.youzan_updated_at = _parse_datetime(
                _text(source, "update_time", "updated_at", "modified")
            )
            row.last_synced_at = now
            row.updated_at = now

            if isinstance(detail, dict):
                session.execute(
                    delete(YouzanProductSkuModel).where(
                        YouzanProductSkuModel.item_id == item_id
                    )
                )
                for index, sku in enumerate(_extract_skus(detail), start=1):
                    session.add(
                        YouzanProductSkuModel(
                            item_id=item_id,
                            sku_id=_text(sku, "sku_id", "item_sku_id", "id") or f"default-{index}",
                            spec_name=_sku_spec_name(sku),
                            price_cent=_integer(sku, "price", "price_cent"),
                            stock=_integer(sku, "stock_num", "quantity", "stock"),
                            sku_code=_text(sku, "sku_no", "code", "sku_code") or None,
                            image_url=_text(sku, "image_url", "pic_url", "image") or None,
                            last_synced_at=now,
                        )
                    )
                    sku_count += 1
        session.commit()
    return {"product_count": len(items), "sku_count": sku_count}


def _extract_skus(detail: dict[str, Any]) -> list[dict[str, Any]]:
    for container in (detail, detail.get("item") if isinstance(detail.get("item"), dict) else {}):
        values = _list_value(container, "skus", "sku_info_list", "sku_list", "variants")
        if values:
            return [value for value in values if isinstance(value, dict)]
    return []


def _sku_spec_name(sku: dict[str, Any]) -> str:
    direct = _text(sku, "properties_name", "spec_name", "sku_name", "title", "name")
    if direct:
        return direct.replace(";", " / ")
    specs = _list_value(sku, "sku_specs", "properties", "specs", "name_list")
    parts = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        name = _text(spec, "spec_name", "kname", "name")
        value = _text(spec, "spec_value_name", "vname", "value")
        parts.append(f"{name}：{value}" if name and value else value or name)
    return " / ".join(part for part in parts if part) or "默认规格"


def _start_sync_run(trigger: str) -> int:
    now = _now()
    with _session() as session:
        row = YouzanProductSyncRunModel(
            trigger=trigger,
            status="running",
            started_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _finish_sync_run(
    run_id: int,
    *,
    result: dict[str, int] | None = None,
    error: Exception | None = None,
) -> None:
    with _session() as session:
        row = session.get(YouzanProductSyncRunModel, run_id)
        if row is None:
            return
        row.finished_at = _now()
        if error is None:
            row.status = "success"
            row.product_count = int((result or {}).get("product_count", 0))
            row.sku_count = int((result or {}).get("sku_count", 0))
            row.detail_error_count = int((result or {}).get("detail_error_count", 0))
        else:
            row.status = "failed"
            row.error_message = str(error)[:1000]
        session.commit()


def _sync_is_due(interval_hours: int) -> bool:
    with _session() as session:
        latest = session.scalar(
            select(YouzanProductSyncRunModel)
            .where(YouzanProductSyncRunModel.status == "success")
            .order_by(YouzanProductSyncRunModel.finished_at.desc())
        )
        return latest is None or latest.finished_at is None or _as_utc(latest.finished_at) <= _now() - timedelta(hours=interval_hours)


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0, seconds))
        return True
    except TimeoutError:
        return False


def _serialize_product(
    row: YouzanProductModel,
    skus: list[dict[str, Any]],
    knowledge: YouzanProductKnowledgeModel | None = None,
) -> dict[str, Any]:
    return {
        "item_id": row.item_id,
        "alias": row.alias,
        "title": row.title,
        "image_url": row.image_url,
        "status": row.status,
        "price_cent": row.price_cent,
        "stock": row.stock,
        "h5_url": row.h5_url,
        "page_url": row.page_url,
        "sort_order": row.sort_order,
        "internal_note": row.internal_note,
        "youzan_updated_at": _isoformat(row.youzan_updated_at),
        "last_synced_at": _isoformat(row.last_synced_at),
        "skus": skus,
        "sku_count": len(skus),
        "has_knowledge": knowledge is not None,
        "knowledge_id": knowledge.id if knowledge is not None else None,
        "knowledge_name": knowledge.product_name if knowledge is not None else None,
    }


def _serialize_sku(row: YouzanProductSkuModel) -> dict[str, Any]:
    return {
        "sku_id": row.sku_id,
        "spec_name": row.spec_name,
        "price_cent": row.price_cent,
        "stock": row.stock,
        "sku_code": row.sku_code,
        "image_url": row.image_url,
    }


def _serialize_sync_run(row: YouzanProductSyncRunModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "product_count": row.product_count,
        "sku_count": row.sku_count,
        "detail_error_count": row.detail_error_count,
        "error_message": row.error_message,
        "started_at": _isoformat(row.started_at),
        "finished_at": _isoformat(row.finished_at),
    }


def _list_value(data: dict[str, Any], *keys: str) -> list:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _integer(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None
    return None


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return _as_utc(parsed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None
