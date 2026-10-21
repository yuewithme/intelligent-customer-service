from __future__ import annotations

from app.domains.knowledge.services.unified_library import remote

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, delete, func, inspect, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    CareManualCardModel,
    CareManualProductLinkModel,
    CareManualSyncRunModel,
    YouzanProductModel,
)
from app.integrations.youzan.client import YouzanClient
from app.integrations.youzan.services.youzan_token_service import (
    create_managed_youzan_client,
    youzan_credentials_available,
)


logger = logging.getLogger("wechat_rag_bot.care_manual_sync")
CARE_MANUAL_TITLE_MARKERS = ("养护要点", "养护注意事项")
_PUBLISHED_STATUSES = {"published", "publish", "online", "on", "1", "true"}
_ORCHID_CATEGORIES = ("莲瓣兰", "建兰", "春兰", "蕙兰", "墨兰", "寒兰", "春剑", "秋芝", "送春", "兰花")
_TABLES = [
    CareManualCardModel.__table__,
    CareManualProductLinkModel.__table__,
    CareManualSyncRunModel.__table__,
    YouzanProductModel.__table__,
]


@lru_cache
def _session_factory(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=_TABLES)
    with engine.begin() as connection:
        for table, additions in {
            "care_manual_cards": {"source_type": "VARCHAR(16) NOT NULL DEFAULT 'manual'", "price_cent": "INTEGER", "content_count": "INTEGER"},
            "care_manual_sync_runs": {"source_type": "VARCHAR(16) NOT NULL DEFAULT 'manual'"},
        }.items():
            columns = {column["name"] for column in inspect(connection).get_columns(table)}
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _session() -> Session:
    if get_settings().unified_library_enabled:
        raise RuntimeError("养护手册已归统一资料库管理")
    return _session_factory(get_settings().database_url)()


def reset_care_manual_store_for_tests() -> None:
    _session_factory.cache_clear()


@remote("manuals.list_care_manuals")
def list_care_manuals(
    *,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    enabled: bool | None = None,
    match_status: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    with _session() as session:
        base_filters = [or_(
            (CareManualCardModel.source_type == "manual") & _manual_title_filter(),
            CareManualCardModel.source_type == "course",
        )]
        if source_type:
            base_filters.append(CareManualCardModel.source_type == source_type)
        filters = list(base_filters)
        if keyword and keyword.strip():
            value = f"%{keyword.strip()}%"
            linked_cards = select(CareManualProductLinkModel.care_manual_card_id).where(
                CareManualProductLinkModel.product_name_snapshot.ilike(value)
            )
            filters.append(
                or_(
                    CareManualCardModel.title.ilike(value),
                    CareManualCardModel.orchid_name.ilike(value),
                    CareManualCardModel.aliases_json.ilike(value),
                    CareManualCardModel.match_keywords_json.ilike(value),
                    CareManualCardModel.id.in_(linked_cards),
                )
            )
        if enabled is not None:
            filters.append(CareManualCardModel.enabled.is_(enabled))
        if match_status == "bound":
            filters.extend(
                [
                    CareManualCardModel.orchid_name.is_not(None),
                    CareManualCardModel.orchid_name != "",
                ]
            )
        elif match_status == "unbound":
            filters.append(
                or_(
                    CareManualCardModel.orchid_name.is_(None),
                    CareManualCardModel.orchid_name == "",
                )
            )
        elif match_status in {"product_bound", "product_unbound"}:
            has_products = CareManualCardModel.id.in_(
                select(CareManualProductLinkModel.care_manual_card_id)
            )
            filters.append(has_products if match_status == "product_bound" else ~has_products)

        query = (
            select(CareManualCardModel)
            .where(*filters)
            .order_by(
                CareManualCardModel.sort_order.asc(),
                CareManualCardModel.published_at.desc(),
                CareManualCardModel.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list(session.scalars(query))
        links = _links_by_card(session, [row.id for row in rows])
        total = int(
            session.scalar(
                select(func.count()).select_from(CareManualCardModel).where(*filters)
            )
            or 0
        )
        all_rows = list(session.scalars(select(CareManualCardModel).where(*base_filters)))
        linked_card_ids = set(session.scalars(select(CareManualProductLinkModel.care_manual_card_id)))
        latest = session.scalar(
            select(CareManualSyncRunModel)
            .where(CareManualSyncRunModel.source_type == (source_type or "manual"))
            .order_by(CareManualSyncRunModel.id.desc())
        )
        return {
            "items": [_serialize_card(row, links.get(row.id, [])) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "stats": {
                "active": sum(_is_available(row) for row in all_rows),
                "disabled": sum(not _is_available(row) for row in all_rows),
                "unbound": sum(not (row.orchid_name or "").strip() for row in all_rows),
                "product_unbound": sum(row.id not in linked_card_ids for row in all_rows),
            },
            "last_sync": _serialize_sync_run(latest),
        }


@remote("manuals.get_care_manual")
def get_care_manual(card_id: int) -> dict[str, Any]:
    with _session() as session:
        row = session.get(CareManualCardModel, card_id)
        if row is None or (row.source_type != "course" and not any(marker in row.title for marker in CARE_MANUAL_TITLE_MARKERS)):
            raise LookupError("养护手册不存在")
        links = _links_by_card(session, [card_id])
        return _serialize_card(row, links.get(card_id, []))


@remote("manuals.update_care_manual")
def update_care_manual(card_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with _session() as session:
        row = session.get(CareManualCardModel, card_id)
        if row is None or (row.source_type != "course" and not any(marker in row.title for marker in CARE_MANUAL_TITLE_MARKERS)):
            raise LookupError("养护手册不存在")
        item_ids = list(dict.fromkeys(payload.get("youzan_item_ids") or []))
        products = {
            product.item_id: product
            for product in session.scalars(
                select(YouzanProductModel).where(YouzanProductModel.item_id.in_(item_ids))
            )
        }
        missing = [item_id for item_id in item_ids if item_id not in products]
        if missing:
            raise ValueError(f"关联商品不存在：{', '.join(missing[:5])}")

        row.orchid_name = str(payload.get("orchid_name") or "").strip() or None
        row.aliases_json = _dump_list(payload.get("aliases"))
        row.card_description = (
            str(payload.get("card_description") or "").strip() or None
        )
        row.sort_order = int(payload.get("sort_order") or 0)
        row.enabled = bool(payload.get("enabled"))
        row.match_keywords_json = _dump_list(payload.get("match_keywords"))
        row.updated_at = now

        session.execute(
            delete(CareManualProductLinkModel).where(
                CareManualProductLinkModel.care_manual_card_id == card_id
            )
        )
        for item_id in item_ids:
            product = products[item_id]
            session.add(
                CareManualProductLinkModel(
                    care_manual_card_id=card_id,
                    youzan_item_id=item_id,
                    product_name_snapshot=product.title,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        session.refresh(row)
        return _serialize_card(row, _links_by_card(session, [card_id]).get(card_id, []))


def _product_variety_names(title: str) -> tuple[str, set[str]]:
    title = unicodedata.normalize("NFKC", title).replace("连瓣兰", "莲瓣兰")
    bracket = re.search(r"【([^】]+)】|\[([^\]]+)\]", title)
    if bracket:
        label = (bracket.group(1) or bracket.group(2)).strip()
        prefix = title[:bracket.start()].strip()
        suffix = title[bracket.end():].strip()
        if label in _ORCHID_CATEGORIES:
            sources = [suffix.split()[0]] if suffix else []
            if suffix:
                sources.append(re.split(r"金黄花瓣|浓香|清香|经典|矮种|叶型|花色|花香|绿壳|正格", suffix, maxsplit=1)[0])
        elif any(marker in label for marker in ("会员", "专用", "专属", "团购", "组合装")):
            sources = [prefix, suffix.split()[0] if suffix else ""]
        else:
            sources = [label, prefix]
        category_source = prefix + label
    else:
        sources = [re.split(r"\d", title, maxsplit=1)[0]] if title.strip() else []
        category_source = sources[0] if sources else ""
    category = next((name for name in _ORCHID_CATEGORIES if name != "兰花" and name in category_source), "")
    names = set()
    for source in sources:
        for name in [source, *re.split(r"[\s+＋/\\、，,]+", source)]:
            name = re.split(r"\d", name, maxsplit=1)[0]
            name = _normalize(name)
            name = next((name[len(prefix):] for prefix in _ORCHID_CATEGORIES if name.startswith(prefix)), name)
            if len(_normalize(name)) >= 2:
                names.add(_normalize(name))
    return category, names


@remote("manuals.suggest_care_manual_product_links")
def suggest_care_manual_product_links() -> dict[str, Any]:
    with _session() as session:
        cards = [
            row for row in session.scalars(
                select(CareManualCardModel).order_by(CareManualCardModel.id)
            ) if _is_available(row)
        ]
        links = _links_by_card(session, [card.id for card in cards])
        existing = {
            (card_id, link.youzan_item_id)
            for card_id, card_links in links.items() for link in card_links
        }
        terms: dict[int, list[str]] = {}
        for card in cards:
            names = [
                name.strip()
                for value in [card.orchid_name or "", _orchid_name_from_title(card.title) or "", *_load_list(card.aliases_json)]
                for name in re.split(r"[/\\、]+", value)
            ]
            names += [
                name[len(prefix):] for name in names for prefix in _ORCHID_CATEGORIES
                if name.startswith(prefix)
            ]
            terms[card.id] = sorted(
                {name for name in names if len(_normalize(name)) >= 2 and name not in _ORCHID_CATEGORIES},
                key=lambda name: (-len(_normalize(name)), name),
            )
        suggestions = []
        for product in session.scalars(
            select(YouzanProductModel)
            .where(YouzanProductModel.status != "missing")
            .order_by(YouzanProductModel.title, YouzanProductModel.item_id)
        ):
            category, product_names = _product_variety_names(product.title)
            matched = [
                (card, next((name for name in terms[card.id] if _normalize(name) in product_names), ""))
                for card in cards
                if not category or not any(
                    (card.orchid_name or "").startswith(prefix) and prefix != category
                    for prefix in _ORCHID_CATEGORIES if prefix != "兰花"
                )
            ]
            matched = [(card, name) for card, name in matched if name]
            related_cards = {card.id for card, _ in matched} | {
                card_id for card_id, item_id in existing if item_id == product.item_id
            }
            for card, name in matched:
                if (card.id, product.item_id) in existing:
                    continue
                suggestions.append({
                    "card_id": card.id,
                    "title": card.title,
                    "youzan_item_id": product.item_id,
                    "product_name": product.title,
                    "matched_name": name,
                    "ambiguous": len({_manual_identity(card) for card in cards if card.id in related_cards}) > 1,
                })
        return {"items": suggestions}


@remote("manuals.auto_link_care_manual_products")
def auto_link_care_manual_products() -> dict[str, int]:
    suggestions = suggest_care_manual_product_links()["items"]
    return add_care_manual_product_links([item for item in suggestions if not item["ambiguous"]])


@remote("manuals.add_care_manual_product_links")
def add_care_manual_product_links(links: list[dict[str, Any]]) -> dict[str, int]:
    pairs = {(link["card_id"], link["youzan_item_id"]) for link in links}
    card_ids = {card_id for card_id, _ in pairs}
    item_ids = {item_id for _, item_id in pairs}
    now = _now()
    with _session() as session:
        cards = {
            row.id: row for row in session.scalars(
                select(CareManualCardModel).where(CareManualCardModel.id.in_(card_ids))
            )
        }
        products = {
            row.item_id: row for row in session.scalars(
                select(YouzanProductModel).where(YouzanProductModel.item_id.in_(item_ids))
            )
        }
        if any(card_id not in cards or not _is_available(cards[card_id]) for card_id in card_ids):
            raise ValueError("手册已停用或不存在，请刷新候选后重试")
        if any(item_id not in products or products[item_id].status == "missing" for item_id in item_ids):
            raise ValueError("商品已失效或不存在，请刷新候选后重试")
        existing = {
            (card_id, link.youzan_item_id)
            for card_id, card_links in _links_by_card(session, list(card_ids)).items()
            for link in card_links
        }
        new_pairs = pairs - existing
        for card_id, item_id in sorted(new_pairs):
            session.add(CareManualProductLinkModel(
                care_manual_card_id=card_id,
                youzan_item_id=item_id,
                product_name_snapshot=products[item_id].title,
                created_at=now,
                updated_at=now,
            ))
            cards[card_id].updated_at = now
        try:
            session.commit()
        except IntegrityError as exc:
            raise ValueError("商品关联已发生变化，请刷新后重试") from exc
        return {"created_count": len(new_pairs), "manual_count": len({card_id for card_id, _ in new_pairs})}


@remote("manuals.test_match_care_manuals")
def test_match_care_manuals(
    *,
    query: str = "",
    product_name: str = "",
    youzan_item_id: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    normalized_values = {
        value for value in (_normalize(query), _normalize(product_name)) if value
    }
    with _session() as session:
        rows = list(
            session.scalars(
                select(CareManualCardModel).where(
                    or_(
                        (CareManualCardModel.source_type == "manual") & _manual_title_filter(),
                        (CareManualCardModel.source_type == "course") & (CareManualCardModel.price_cent == 0),
                    ),
                    CareManualCardModel.enabled.is_(True),
                    func.lower(CareManualCardModel.youzan_status).in_(_PUBLISHED_STATUSES),
                )
            )
        )
        links = _links_by_card(session, [row.id for row in rows])

    candidates: list[tuple[int, CareManualCardModel, str]] = []
    for row in rows:
        if not _is_available(row):
            continue
        row_links = links.get(row.id, [])
        if youzan_item_id and any(
            link.youzan_item_id == youzan_item_id for link in row_links
        ):
            candidates.append((0, row, "exact_product"))
            continue
        orchid = _normalize(row.orchid_name or (row.title if row.source_type == "course" else ""))
        if orchid and orchid in normalized_values:
            candidates.append((1, row, "exact_orchid"))
            continue
        aliases = {_normalize(value) for value in _load_list(row.aliases_json)}
        if normalized_values & aliases:
            candidates.append((2, row, "exact_alias"))
            continue
        keywords = [_normalize(value) for value in _load_list(row.match_keywords_json)]
        if any(
            keyword and keyword in value
            for keyword in keywords
            for value in normalized_values
        ):
            candidates.append((3, row, "keyword"))
            continue
        if orchid and any(
            len(value) >= 2 and (value in orchid or orchid in value)
            for value in normalized_values
        ):
            candidates.append((4, row, "candidate"))

    identity_priority: dict[str, int] = {}
    for priority, row, _ in candidates:
        identity = _manual_identity(row)
        identity_priority[identity] = min(priority, identity_priority.get(identity, priority))
    candidates.sort(key=lambda item: (identity_priority[_manual_identity(item[1])], _manual_preference(item[1]), item[0], item[1].sort_order, item[1].id))
    if not candidates:
        return {"decision": "not_found", "auto_send_eligible": False, "matches": []}
    best_priority = identity_priority[_manual_identity(candidates[0][1])]
    best = [item for item in candidates if identity_priority[_manual_identity(item[1])] == best_priority]
    orchid_names = {_manual_identity(item[1]) for item in best}
    orchid_names.discard("")
    deterministic = best_priority < 4 and (
        len(orchid_names) == 1 or (best_priority == 0 and len(best) == 1)
    )
    decision = "unique" if deterministic else "ambiguous"
    matches = []
    for priority, row, match_type in candidates[:limit]:
        data = _serialize_card(row, links.get(row.id, []))
        matches.append(
            {
                "card_id": row.id,
                "source_type": row.source_type,
                "price_cent": row.price_cent,
                "title": row.title,
                "orchid_name": row.orchid_name,
                "note_url": row.note_url,
                "cover_url": row.cover_url,
                "sort_order": row.sort_order,
                "match_type": match_type,
                "priority": priority,
                "selected": deterministic and row.id == best[0][1].id,
                "product_links": data["product_links"],
            }
        )
    return {
        "decision": decision,
        "auto_send_eligible": deterministic,
        "matches": matches,
    }


async def sync_care_manuals(
    *, trigger: str = "manual", client: YouzanClient | Any | None = None,
    source_type: str = "manual", course_urls: list[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if source_type not in {"manual", "course"}:
        raise ValueError("未知资料类型")
    if source_type == "manual" and (not settings.youzan_enabled or not youzan_credentials_available()):
        raise RuntimeError("有赞养护手册同步未配置")
    run_id = _start_sync_run(trigger, source_type=source_type)
    try:
        if source_type == "course":
            from app.integrations.youzan.services.youzan_course_service import fetch_courses

            urls = list(course_urls or [])
            loaded = 0
            page = 1
            while True:
                data = list_care_manuals(page=page, page_size=200, source_type="course")
                urls.extend(item["note_url"] for item in data["items"])
                loaded += len(data["items"])
                if loaded >= data["total"]:
                    break
                if not data["items"]:
                    raise RuntimeError("课程分页数据不完整，已保留原数据")
                page += 1
            result = _persist_courses(await fetch_courses(urls, client=client))
        else:
            client = client or create_managed_youzan_client(timeout=30)
            notes = await _fetch_all_notes(
                client,
                method=settings.youzan_care_manual_method,
                version=settings.youzan_care_manual_version,
                page_size=settings.youzan_care_manual_page_size,
            )
            result = _persist_notes(notes)
        _finish_sync_run(run_id, result=result)
        return {**result, "trigger": trigger, "status": "success"}
    except Exception as exc:
        _finish_sync_run(run_id, error=exc)
        raise


async def care_manual_sync_worker(stop_event: asyncio.Event, *, source_type: str = "manual") -> None:
    settings = get_settings()
    if (
        not settings.youzan_care_manual_sync_enabled
        or (source_type == "manual" and (not settings.youzan_enabled or not youzan_credentials_available()))
    ):
        return
    delay = settings.youzan_care_manual_sync_startup_delay_seconds
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        try:
            if source_type == "course":
                await sync_care_manuals(trigger="scheduled", source_type="course")
            else:
                await sync_care_manuals(trigger="scheduled")
            delay = settings.youzan_care_manual_sync_interval_hours * 3600
        except Exception as exc:
            logger.warning("Scheduled Youzan %s sync failed: %s", source_type, type(exc).__name__)
            delay = 300


async def _fetch_all_notes(
    client: Any, *, method: str, version: str, page_size: int
) -> list[dict[str, Any]]:
    page = 1
    result: list[dict[str, Any]] = []
    first_fingerprint: tuple[str, ...] | None = None
    while True:
        data = await client.call(
            method,
            version,
            {"request": {"page": page, "page_size": page_size}},
        )
        items = _note_items(data)
        reported_page = _integer(data.get("page"))
        reported_page_size = _integer(data.get("page_size")) or page_size
        total = _integer(data.get("count") or data.get("total"))
        fingerprint = tuple(_note_id(item) for item in items)
        if page == 1:
            first_fingerprint = fingerprint
        elif (reported_page is not None and reported_page != page) or (
            fingerprint and fingerprint == first_fingerprint
        ):
            raise RuntimeError(
                "有赞店铺笔记分页参数未生效：第2页仍返回第1页；"
                "已取消本次同步，旧卡片数据未被覆盖"
            )
        result.extend(item for item in items if isinstance(item, dict))
        if not items and total is not None and len(result) < total:
            raise RuntimeError(
                "有赞店铺笔记分页数据不完整；已取消本次同步，旧卡片数据未被覆盖"
            )
        if not items or (total is not None and len(result) >= total):
            break
        if len(items) < reported_page_size and total is None:
            break
        if page >= 1000:
            raise RuntimeError("有赞店铺笔记分页超过安全上限")
        page += 1
    return result


@remote("manuals._persist_notes")
def _persist_notes(notes: list[dict[str, Any]]) -> dict[str, int]:
    now = _now()
    qualified_count = created_count = updated_count = disabled_count = 0
    seen: set[str] = set()
    with _session() as session:
        existing = {
            row.youzan_note_id: row
            for row in session.scalars(select(CareManualCardModel).where(CareManualCardModel.source_type == "manual"))
        }
        for note in notes:
            note_id = _note_id(note)
            if not note_id:
                continue
            seen.add(note_id)
            row = existing.get(note_id)
            title = str(note.get("title") or "").strip()
            status = str(note.get("note_status") or "").strip().lower()
            note_url = str(note.get("note_url") or "").strip()
            qualified = (
                any(marker in title for marker in CARE_MANUAL_TITLE_MARKERS)
                and _status_is_published(status)
                and bool(note_url)
            )
            if not qualified:
                if row is not None:
                    row.title = title or row.title
                    row.youzan_status = status or "unavailable"
                    row.last_synced_at = now
                    row.updated_at = now
                    disabled_count += 1
                continue
            qualified_count += 1
            external = {
                "note_alias": str(note.get("note_alias") or "").strip() or None,
                "title": title,
                "note_url": note_url,
                "cover_url": _cover_url(note.get("cover_photos")),
                "youzan_status": status,
                "published_at": _parse_datetime(note.get("publish_time")),
            }
            if row is None:
                row = CareManualCardModel(
                    youzan_note_id=note_id,
                    orchid_name=_orchid_name_from_title(title),
                    aliases_json="[]",
                    card_description=None,
                    enabled=True,
                    sort_order=0,
                    match_keywords_json="[]",
                    last_synced_at=now,
                    created_at=now,
                    updated_at=now,
                    **external,
                )
                session.add(row)
                existing[note_id] = row
                created_count += 1
            else:
                changed = any(getattr(row, key) != value for key, value in external.items())
                previous_name = re.split("|".join(CARE_MANUAL_TITLE_MARKERS), row.title, maxsplit=1)[0].strip("【】[]（）()：: -—_")
                if not row.orchid_name or row.orchid_name in {_orchid_name_from_title(row.title), previous_name}:
                    row.orchid_name = _orchid_name_from_title(title)
                for key, value in external.items():
                    setattr(row, key, value)
                row.last_synced_at = now
                if changed:
                    row.updated_at = now
                    updated_count += 1
        for note_id, row in existing.items():
            if note_id not in seen:
                if _status_is_published(row.youzan_status):
                    disabled_count += 1
                row.youzan_status = "missing"
                row.last_synced_at = now
                row.updated_at = now
        session.commit()
    return {
        "scanned_count": len(notes),
        "qualified_count": qualified_count,
        "created_count": created_count,
        "updated_count": updated_count,
        "disabled_count": disabled_count,
    }


@remote("manuals._start_sync_run")
def _start_sync_run(trigger: str, source_type: str = "manual") -> int:
    with _session() as session:
        row = CareManualSyncRunModel(
            source_type=source_type,
            trigger=trigger,
            status="running",
            started_at=_now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


@remote("manuals._finish_sync_run")
def _finish_sync_run(
    run_id: int,
    *,
    result: dict[str, int] | None = None,
    error: Exception | None = None,
) -> None:
    with _session() as session:
        row = session.get(CareManualSyncRunModel, run_id)
        if row is None:
            return
        row.finished_at = _now()
        if error is not None:
            row.status = "failed"
            row.error_message = str(error)[:2000]
        else:
            row.status = "success"
            row.scanned_count = int((result or {}).get("scanned_count", 0))
            row.qualified_count = int((result or {}).get("qualified_count", 0))
            row.created_count = int((result or {}).get("created_count", 0))
            row.updated_count = int((result or {}).get("updated_count", 0))
            row.disabled_count = int((result or {}).get("disabled_count", 0))
        session.commit()


@remote("manuals._persist_courses")
def _persist_courses(courses: list[dict[str, Any]]) -> dict[str, int]:
    now = _now()
    created_count = updated_count = 0
    with _session() as session:
        for course in courses:
            note_id = "course:" + str(course["note_id"])
            row = session.scalar(select(CareManualCardModel).where(CareManualCardModel.youzan_note_id == note_id))
            if row is None:
                row = CareManualCardModel(
                    youzan_note_id=note_id, source_type="course", orchid_name=None,
                    aliases_json="[]", match_keywords_json="[]", enabled=True, sort_order=0,
                    created_at=now,
                )
                session.add(row)
                created_count += 1
            else:
                updated_count += 1
            for field in ("title", "note_alias", "note_url", "cover_url", "price_cent", "content_count"):
                setattr(row, field, course.get(field))
            row.youzan_status = course["note_status"]
            row.published_at = _parse_datetime(course.get("publish_time"))
            row.last_synced_at = row.updated_at = now
        session.commit()
    qualified = sum(course["note_status"] == "published" for course in courses)
    return {"scanned_count": len(courses), "qualified_count": qualified,
            "created_count": created_count, "updated_count": updated_count,
            "disabled_count": len(courses) - qualified}


def _links_by_card(
    session: Session, card_ids: list[int]
) -> dict[int, list[CareManualProductLinkModel]]:
    result: dict[int, list[CareManualProductLinkModel]] = {}
    if not card_ids:
        return result
    rows = session.scalars(
        select(CareManualProductLinkModel)
        .where(CareManualProductLinkModel.care_manual_card_id.in_(card_ids))
        .order_by(CareManualProductLinkModel.id)
    )
    for row in rows:
        result.setdefault(row.care_manual_card_id, []).append(row)
    return result


def _serialize_card(
    row: CareManualCardModel, links: list[CareManualProductLinkModel]
) -> dict[str, Any]:
    return {
        "id": row.id,
        "youzan_note_id": row.youzan_note_id,
        "source_type": row.source_type,
        "price_cent": row.price_cent,
        "content_count": row.content_count,
        "auto_send_allowed": _is_available(row) and (row.source_type != "course" or row.price_cent == 0),
        "note_alias": row.note_alias,
        "title": row.title,
        "orchid_name": row.orchid_name,
        "aliases": _load_list(row.aliases_json),
        "note_url": row.note_url,
        "cover_url": row.cover_url,
        "card_description": row.card_description,
        "youzan_status": row.youzan_status,
        "enabled": row.enabled,
        "available": _is_available(row),
        "sort_order": row.sort_order,
        "match_keywords": _load_list(row.match_keywords_json),
        "published_at": _isoformat(row.published_at),
        "last_synced_at": _isoformat(row.last_synced_at),
        "created_at": _isoformat(row.created_at),
        "updated_at": _isoformat(row.updated_at),
        "product_links": [
            {
                "youzan_item_id": link.youzan_item_id,
                "product_name": link.product_name_snapshot,
            }
            for link in links
        ],
    }


def _serialize_sync_run(row: CareManualSyncRunModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "trigger": row.trigger,
        "source_type": row.source_type,
        "status": row.status,
        "scanned_count": row.scanned_count,
        "qualified_count": row.qualified_count,
        "created_count": row.created_count,
        "updated_count": row.updated_count,
        "disabled_count": row.disabled_count,
        "error_message": row.error_message,
        "started_at": _isoformat(row.started_at),
        "finished_at": _isoformat(row.finished_at),
    }


def _is_available(row: CareManualCardModel) -> bool:
    return (
        row.enabled
        and (row.source_type == "course" or any(marker in row.title for marker in CARE_MANUAL_TITLE_MARKERS))
        and _status_is_published(row.youzan_status)
        and bool(row.note_url)
    )


def _status_is_published(status: str) -> bool:
    return str(status or "").strip().lower() in _PUBLISHED_STATUSES


def _note_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("data", "items", "notes", "list"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _note_id(note: dict[str, Any]) -> str:
    return str(note.get("note_id") or note.get("id") or "").strip()


def _cover_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if isinstance(first, str):
        return first.strip() or None
    if isinstance(first, dict):
        for key in ("url", "image_url", "src", "photo_url"):
            if first.get(key):
                return str(first[key]).strip() or None
    return None


def _orchid_name_from_title(title: str) -> str | None:
    value = re.split("|".join(CARE_MANUAL_TITLE_MARKERS), title, maxsplit=1)[0]
    bracket = re.fullmatch(r"\s*[【\[]\s*([^】\]]+?)\s*[】\]]\s*(?:单品)?\s*", value)
    if bracket:
        value = bracket.group(1)
    value = value.strip("【】[]（）()：: -—_")
    return value or None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("连瓣兰", "莲瓣兰")
    return "".join(char for char in normalized if char.isalnum())


def _manual_title_filter():
    return or_(*(CareManualCardModel.title.contains(marker) for marker in CARE_MANUAL_TITLE_MARKERS))


def _manual_identity(row: CareManualCardModel) -> str:
    value = row.orchid_name or (_orchid_name_from_title(row.title) if row.source_type == "manual" else row.title)
    return _normalize(value or "")


def _manual_preference(row: CareManualCardModel) -> int:
    return 0 if row.source_type == "manual" and "养护要点" in row.title else 1


def _dump_list(values: Any) -> str:
    result: list[str] = []
    for value in values or []:
        cleaned = str(value).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return json.dumps(result, ensure_ascii=False)


def _load_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
