from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    CareManualCardModel,
    CareManualProductLinkModel,
    CareManualSyncRunModel,
    YouzanProductModel,
)
from app.integrations.youzan.client import YouzanClient


CARE_MANUAL_TITLE_MARKER = "养护注意事项"
_PUBLISHED_STATUSES = {"published", "publish", "online", "on", "1", "true"}
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
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _session() -> Session:
    return _session_factory(get_settings().database_url)()


def reset_care_manual_store_for_tests() -> None:
    _session_factory.cache_clear()


def list_care_manuals(
    *,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    enabled: bool | None = None,
    match_status: str | None = None,
) -> dict[str, Any]:
    with _session() as session:
        base_filters = [CareManualCardModel.title.contains(CARE_MANUAL_TITLE_MARKER)]
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
        latest = session.scalar(
            select(CareManualSyncRunModel).order_by(CareManualSyncRunModel.id.desc())
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
            },
            "last_sync": _serialize_sync_run(latest),
        }


def get_care_manual(card_id: int) -> dict[str, Any]:
    with _session() as session:
        row = session.get(CareManualCardModel, card_id)
        if row is None or CARE_MANUAL_TITLE_MARKER not in row.title:
            raise LookupError("养护手册不存在")
        links = _links_by_card(session, [card_id])
        return _serialize_card(row, links.get(card_id, []))


def update_care_manual(card_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with _session() as session:
        row = session.get(CareManualCardModel, card_id)
        if row is None or CARE_MANUAL_TITLE_MARKER not in row.title:
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
                    CareManualCardModel.title.contains(CARE_MANUAL_TITLE_MARKER),
                    CareManualCardModel.enabled.is_(True),
                    func.lower(CareManualCardModel.youzan_status).in_(_PUBLISHED_STATUSES),
                )
            )
        )
        links = _links_by_card(session, [row.id for row in rows])

    candidates: list[tuple[int, CareManualCardModel, str]] = []
    for row in rows:
        row_links = links.get(row.id, [])
        if youzan_item_id and any(
            link.youzan_item_id == youzan_item_id for link in row_links
        ):
            candidates.append((0, row, "exact_product"))
            continue
        orchid = _normalize(row.orchid_name or "")
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

    candidates.sort(key=lambda item: (item[0], item[1].sort_order, item[1].id))
    if not candidates:
        return {"decision": "not_found", "auto_send_eligible": False, "matches": []}
    best_priority = candidates[0][0]
    best = [item for item in candidates if item[0] == best_priority]
    orchid_names = {_normalize(item[1].orchid_name or "") for item in best}
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
    *, trigger: str = "manual", client: YouzanClient | Any | None = None
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.youzan_enabled or not settings.youzan_access_token.strip():
        raise RuntimeError("有赞养护手册同步未配置")
    run_id = _start_sync_run(trigger)
    client = client or YouzanClient(
        access_token=settings.youzan_access_token,
        base_url=settings.youzan_base_url,
        timeout=30,
    )
    try:
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
            {"page": page, "page_size": page_size},
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
        if not items or (total is not None and len(result) >= total):
            break
        if len(items) < reported_page_size and total is None:
            break
        if page >= 1000:
            raise RuntimeError("有赞店铺笔记分页超过安全上限")
        page += 1
    return result


def _persist_notes(notes: list[dict[str, Any]]) -> dict[str, int]:
    now = _now()
    qualified_count = created_count = updated_count = disabled_count = 0
    seen: set[str] = set()
    with _session() as session:
        existing = {
            row.youzan_note_id: row
            for row in session.scalars(select(CareManualCardModel))
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
                CARE_MANUAL_TITLE_MARKER in title
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


def _start_sync_run(trigger: str) -> int:
    with _session() as session:
        row = CareManualSyncRunModel(
            trigger=trigger,
            status="running",
            started_at=_now(),
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
        and CARE_MANUAL_TITLE_MARKER in row.title
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
    match = re.search(r"[【\[]\s*([^】\]]+?)\s*[】\]]\s*养护注意事项", title)
    value = match.group(1) if match else title.split(CARE_MANUAL_TITLE_MARKER, 1)[0]
    value = value.strip("【】[]（）()：: -—_")
    return value or None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\-—_·,，。.!！?？:：;；【】\[\]（）()]", "", normalized)


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
