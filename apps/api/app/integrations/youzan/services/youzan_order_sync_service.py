from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine, desc, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import (
    Base,
    ConversationModel,
    YouzanIdentityBindingModel,
    YouzanOrderModel,
    YouzanOrderSyncRunModel,
)
from app.integrations.youzan.client import YouzanClient
from app.integrations.youzan.services.youzan_order_service import ORDER_STATUS_TEXT
from app.integrations.youzan.services.youzan_token_service import (
    create_managed_youzan_client,
    notify_youzan_failure,
    notify_youzan_recovery,
    youzan_credentials_available,
)


logger = logging.getLogger("wechat_rag_bot.youzan_order_sync")
_TABLES = (
    YouzanOrderModel.__table__,
    YouzanOrderSyncRunModel.__table__,
    YouzanIdentityBindingModel.__table__,
    ConversationModel.__table__,
)
_sessionmakers: dict[str, sessionmaker] = {}


def _session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(f"unsupported chat log provider: {settings.chat_log_provider}")
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(engine, tables=list(_TABLES))
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def reset_order_store_for_tests() -> None:
    _sessionmakers.clear()


async def sync_youzan_orders(
    *,
    trigger: str = "manual",
    client: YouzanClient | Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.youzan_enabled or not youzan_credentials_available():
        raise RuntimeError("有赞订单同步未配置")
    now = _aware(now or _now())
    window_start = _next_window_start(now)
    window_end = now
    run_id = _start_run(trigger, window_start, window_end)
    client = client or create_managed_youzan_client()
    try:
        order_count = 0
        cursor = window_start
        while cursor < window_end:
            chunk_end = min(cursor + timedelta(days=7), window_end)
            trades = await _fetch_window(client, cursor, chunk_end)
            order_count += _persist_orders(trades, synced_at=now)
            cursor = chunk_end
        _finish_run(run_id, order_count=order_count)
        return {
            "status": "success",
            "trigger": trigger,
            "order_count": order_count,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
    except Exception as exc:
        _finish_run(run_id, error=exc)
        raise


async def youzan_order_sync_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    if (
        not settings.youzan_order_sync_enabled
        or not settings.youzan_enabled
        or not youzan_credentials_available()
    ):
        return
    if await _wait_or_stop(
        stop_event, settings.youzan_order_sync_startup_delay_seconds
    ):
        return
    while not stop_event.is_set():
        try:
            if _sync_is_due(settings.youzan_order_sync_interval_minutes):
                await sync_youzan_orders(trigger="scheduled")
                await notify_youzan_recovery("订单同步恢复")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduled Youzan order sync failed: %s", type(exc).__name__)
            await notify_youzan_failure("订单同步", exc)
        if await _wait_or_stop(
            stop_event,
            min(60, settings.youzan_order_sync_interval_minutes * 60),
        ):
            return


def list_conversation_orders(
    conversation_id: str,
    *,
    profile_mobile: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    settings = get_settings()
    limit = max(1, min(limit, 50))
    with _session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        if conversation is None:
            raise LookupError("会话不存在")
        binding = session.scalar(
            select(YouzanIdentityBindingModel).where(
                YouzanIdentityBindingModel.tenant_id == conversation.tenant_id,
                YouzanIdentityBindingModel.channel == conversation.channel,
                YouzanIdentityBindingModel.external_user_id == conversation.user_id,
                YouzanIdentityBindingModel.kdt_id == settings.youzan_kdt_id,
            )
        )
        conditions = []
        if binding is not None:
            for field in (
                "buyer_id",
                "yz_uid",
                "yz_open_id",
                "fans_id",
                "weixin_openid",
                "union_id",
                "mobile",
            ):
                value = str(getattr(binding, field) or "").strip()
                if value:
                    conditions.append(getattr(YouzanOrderModel, field) == value)
        mobile = profile_mobile.strip()
        if mobile:
            conditions.append(YouzanOrderModel.mobile == mobile)
        if conversation.channel == "wechat" and conversation.user_id:
            conditions.extend(
                (
                    YouzanOrderModel.weixin_openid == conversation.user_id,
                    YouzanOrderModel.yz_open_id == conversation.user_id,
                )
            )

        rows: list[YouzanOrderModel] = []
        if conditions:
            rows = list(
                session.scalars(
                    select(YouzanOrderModel)
                    .where(
                        YouzanOrderModel.kdt_id == settings.youzan_kdt_id,
                        or_(*conditions),
                    )
                    .order_by(
                        desc(YouzanOrderModel.order_created_at),
                        desc(YouzanOrderModel.id),
                    )
                    .limit(limit)
                )
            )
        last_run = session.scalar(
            select(YouzanOrderSyncRunModel)
            .where(YouzanOrderSyncRunModel.status == "success")
            .order_by(desc(YouzanOrderSyncRunModel.id))
        )
        return {
            "status": "found" if rows else ("not_found" if conditions else "unbound"),
            "items": [_serialize_order(row) for row in rows],
            "mobile_masked": (
                str(getattr(binding, "mobile_masked", "") or "")
                if binding is not None
                else _mask_mobile(mobile)
            ),
            "last_synced_at": (
                _iso(last_run.finished_at) if last_run is not None else None
            ),
        }


async def _fetch_window(
    client: Any, window_start: datetime, window_end: datetime
) -> list[dict[str, Any]]:
    settings = get_settings()
    page_no = 1
    result: list[dict[str, Any]] = []
    while True:
        data = await client.call(
            settings.youzan_order_search_method,
            settings.youzan_order_search_version,
            {
                "start_update": _provider_time(window_start),
                "end_update": _provider_time(window_end),
                "page_no": page_no,
                "page_size": settings.youzan_order_sync_page_size,
            },
        )
        items = _trade_list(data)
        result.extend(item for item in items if isinstance(item, dict))
        total = _integer(data, "count", "total", "total_count")
        has_next = _boolean(data, "has_next")
        if (
            not items
            or len(items) < settings.youzan_order_sync_page_size
            or has_next is False
            or (total is not None and len(result) >= total)
        ):
            break
        if page_no >= 100:
            raise RuntimeError("有赞订单单个同步时间窗超过10000条，请缩短同步窗口")
        page_no += 1
    return result


def _persist_orders(trades: list[dict[str, Any]], *, synced_at: datetime) -> int:
    settings = get_settings()
    normalized = [_normalize_trade(item, settings.youzan_kdt_id) for item in trades]
    normalized = [item for item in normalized if item["order_no"]]
    if not normalized:
        return 0
    with _session() as session:
        order_nos = [item["order_no"] for item in normalized]
        existing = {
            row.order_no: row
            for row in session.scalars(
                select(YouzanOrderModel).where(
                    YouzanOrderModel.kdt_id == settings.youzan_kdt_id,
                    YouzanOrderModel.order_no.in_(order_nos),
                )
            )
        }
        for data in normalized:
            row = existing.get(data["order_no"])
            if row is None:
                row = YouzanOrderModel(
                    kdt_id=settings.youzan_kdt_id,
                    order_no=data["order_no"],
                    created_at=synced_at,
                    updated_at=synced_at,
                    last_synced_at=synced_at,
                    status="",
                    status_text="状态待确认",
                )
                session.add(row)
                existing[data["order_no"]] = row
            for key, value in data.items():
                setattr(row, key, value)
            row.last_synced_at = synced_at
            row.updated_at = synced_at
        session.commit()
    return len(normalized)


def _normalize_trade(trade: dict[str, Any], kdt_id: str) -> dict[str, Any]:
    nested = trade.get("full_order_info")
    combined = {**trade, **nested} if isinstance(nested, dict) else trade
    order_info = combined.get("order_info")
    if not isinstance(order_info, dict):
        order_info = combined
    status = str(order_info.get("status") or combined.get("status") or "")
    mobile = _nested_text(
        combined,
        "mobile",
        "receiver_mobile",
        "receiver_tel",
        "phone",
    )
    items = _normalize_items(combined)
    return {
        "kdt_id": str(_nested_text(combined, "kdt_id") or kdt_id),
        "order_no": str(order_info.get("tid") or order_info.get("order_no") or ""),
        "buyer_id": _nested_text(combined, "buyer_id"),
        "yz_uid": _nested_text(combined, "yz_uid"),
        "yz_open_id": _nested_text(combined, "yz_open_id", "yz_openid"),
        "fans_id": _nested_text(combined, "fans_id"),
        "weixin_openid": _nested_text(
            combined, "weixin_openid", "wx_open_id", "openid"
        ),
        "union_id": _nested_text(combined, "union_id", "unionid"),
        "mobile": mobile or None,
        "mobile_masked": _mask_mobile(mobile) or None,
        "status": status,
        "status_text": ORDER_STATUS_TEXT.get(
            status,
            str(combined.get("status_text") or "状态待确认"),
        ),
        "item_summary": "；".join(
            f"{item['title']} × {item['quantity']}" for item in items[:3]
        ),
        "items_json": json.dumps(items, ensure_ascii=False),
        "payment_amount": _payment_amount(combined),
        "express_company": _nested_text(
            combined, "express_name", "express_company", "company_name"
        )
        or None,
        "tracking_no_masked": _mask_tracking_no(
            _nested_text(combined, "express_no", "tracking_no")
        )
        or None,
        "order_created_at": _parse_datetime(
            order_info.get("created")
            or order_info.get("created_at")
            or combined.get("created")
        ),
        "provider_updated_at": _parse_datetime(
            order_info.get("update_time")
            or order_info.get("updated")
            or order_info.get("updated_at")
            or combined.get("update_time")
        ),
    }


def _normalize_items(trade: dict[str, Any]) -> list[dict[str, Any]]:
    values = trade.get("orders") or trade.get("items") or []
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "title": str(item.get("title") or item.get("name") or "商品"),
                "quantity": int(item.get("num") or item.get("quantity") or 1),
                "image_url": str(
                    item.get("pic_path")
                    or item.get("pic_url")
                    or item.get("image_url")
                    or ""
                ),
            }
        )
    return result


def _payment_amount(data: dict[str, Any]) -> str | None:
    for source in (
        data.get("pay_info"),
        data.get("order_info"),
        data,
    ):
        if not isinstance(source, dict):
            continue
        for key in ("payment", "pay", "real_pay", "total_fee"):
            value = source.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
    return None


def _trade_list(data: dict[str, Any]) -> list[Any]:
    for key in ("full_order_info_list", "trades", "items", "orders"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _nested_text(data: dict[str, Any], *keys: str) -> str:
    queue: list[tuple[dict[str, Any], int]] = [(data, 0)]
    while queue:
        current, depth = queue.pop(0)
        for key in keys:
            value = current.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        if depth < 4:
            queue.extend(
                (value, depth + 1)
                for value in current.values()
                if isinstance(value, dict)
            )
    return ""


def _next_window_start(now: datetime) -> datetime:
    settings = get_settings()
    with _session() as session:
        last = session.scalar(
            select(YouzanOrderSyncRunModel)
            .where(YouzanOrderSyncRunModel.status == "success")
            .order_by(desc(YouzanOrderSyncRunModel.id))
        )
    if last is None:
        return now - timedelta(days=settings.youzan_order_sync_initial_lookback_days)
    return _aware(last.window_end) - timedelta(
        minutes=settings.youzan_order_sync_overlap_minutes
    )


def _sync_is_due(interval_minutes: int) -> bool:
    with _session() as session:
        recent = list(
            session.scalars(
                select(YouzanOrderSyncRunModel)
                .where(YouzanOrderSyncRunModel.finished_at.is_not(None))
                .order_by(desc(YouzanOrderSyncRunModel.id))
                .limit(20)
            )
        )
    if not recent:
        return True
    last = recent[0]
    delay_minutes = interval_minutes
    if last.status == "failed":
        consecutive_failures = 0
        for row in recent:
            if row.status != "failed":
                break
            consecutive_failures += 1
        delay_minutes = (5, 15, 30, 60)[
            min(max(consecutive_failures, 1), 4) - 1
        ]
    return _now() - _aware(last.finished_at or last.started_at) >= timedelta(
        minutes=delay_minutes
    )


def _start_run(trigger: str, window_start: datetime, window_end: datetime) -> int:
    with _session() as session:
        row = YouzanOrderSyncRunModel(
            trigger=trigger,
            status="running",
            window_start=window_start,
            window_end=window_end,
            order_count=0,
            started_at=_now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _finish_run(
    run_id: int,
    *,
    order_count: int = 0,
    error: Exception | None = None,
) -> None:
    with _session() as session:
        row = session.get(YouzanOrderSyncRunModel, run_id)
        if row is None:
            return
        row.status = "failed" if error else "success"
        row.order_count = order_count
        row.error_message = (
            f"{type(error).__name__}: {str(error)[:500]}" if error else None
        )
        row.finished_at = _now()
        session.commit()


def _serialize_order(row: YouzanOrderModel) -> dict[str, Any]:
    try:
        items = json.loads(row.items_json or "[]")
    except (TypeError, json.JSONDecodeError):
        items = []
    return {
        "order_no": row.order_no,
        "status": row.status,
        "status_text": row.status_text,
        "item_summary": row.item_summary,
        "items": items if isinstance(items, list) else [],
        "payment_amount": row.payment_amount,
        "express_company": row.express_company,
        "tracking_no_masked": row.tracking_no_masked,
        "created_at": _iso(row.order_created_at),
        "updated_at": _iso(row.provider_updated_at),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return _parse_datetime(int(text))
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed.astimezone(timezone.utc)
    return None


def _provider_time(value: datetime) -> str:
    return _aware(value).astimezone(timezone(timedelta(hours=8))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _integer(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _boolean(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _mask_mobile(value: str) -> str:
    return f"{value[:3]}****{value[-4:]}" if len(value) == 11 else ""


def _mask_tracking_no(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return value
    return f"{value[:4]}{'*' * (len(value) - 6)}{value[-2:]}"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    return _aware(value).astimezone(timezone.utc).isoformat() if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0, seconds))
    except TimeoutError:
        return False
    return True
