import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ChatLogModel


logger = logging.getLogger("wechat_rag_bot.chat_logs")
SENSITIVE_KEYS = {"token", "password", "api_key", "secret", "authorization"}
DECISION_KEYS = {"action", "reason", "original_route", "trace"}
DECISION_TRACE_KEYS = {"source", "proposed_action", "reason", "accepted"}
_sessionmakers: dict[str, sessionmaker] = {}


def sanitize_log_payload(payload: dict) -> dict:
    settings = get_settings()
    sanitized = _sanitize_value(payload)
    if not isinstance(sanitized, dict):
        return {}
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("decision"), dict):
        metadata["decision"] = _sanitize_decision(metadata["decision"])
    if isinstance(sanitized.get("user_message"), str):
        sanitized["user_message"] = sanitized["user_message"][
            : settings.chat_log_max_message_length
        ]
    if isinstance(sanitized.get("answer"), str):
        sanitized["answer"] = sanitized["answer"][: settings.chat_log_max_answer_length]
    return sanitized


def _sanitize_decision(decision: dict) -> dict:
    reduced = {key: decision[key] for key in DECISION_KEYS if key in decision}
    trace = reduced.get("trace")
    if isinstance(trace, list):
        reduced["trace"] = [
            {key: step[key] for key in DECISION_TRACE_KEYS if key in step}
            for step in trace
            if isinstance(step, dict)
        ]
    else:
        reduced["trace"] = []
    return reduced


async def record_chat_log(log: dict) -> None:
    settings = get_settings()
    if not settings.chat_log_enabled:
        return
    try:
        payload = sanitize_log_payload(log)
        if not payload.get("trace_id"):
            return
        with _get_session() as session:
            existing = session.scalar(
                select(ChatLogModel).where(ChatLogModel.trace_id == payload["trace_id"])
            )
            model = _payload_to_model(payload)
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(model)
            session.commit()
    except Exception:
        logger.exception("failed to record chat log")


async def list_chat_logs(
    *,
    page: int = 1,
    page_size: int = 50,
    user_id: str | None = None,
    session_id: str | None = None,
    route: str | None = None,
    primary_intent: str | None = None,
    template_id: str | None = None,
    status: str | None = None,
    need_human: bool | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    if not get_settings().chat_log_enabled:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    with _get_session() as session:
        filters = _build_filters(
            user_id=user_id,
            session_id=session_id,
            route=route,
            primary_intent=primary_intent,
            template_id=template_id,
            status=status,
            need_human=need_human,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )
        total = session.scalar(select(func.count()).select_from(ChatLogModel).where(*filters))
        rows = session.scalars(
            select(ChatLogModel)
            .where(*filters)
            .order_by(ChatLogModel.created_at.desc(), ChatLogModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {
        "items": [_model_to_item(row) for row in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


async def get_chat_log(trace_id: str) -> dict | None:
    if not get_settings().chat_log_enabled:
        return None
    with _get_session() as session:
        row = session.scalar(
            select(ChatLogModel).where(ChatLogModel.trace_id == trace_id)
        )
    if row is None:
        return None
    return _model_to_detail(row)


async def get_chat_log_stats(
    *,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    if not get_settings().chat_log_enabled:
        return _empty_stats()
    filters = _build_filters(start_time=start_time, end_time=end_time)
    with _get_session() as session:
        rows = session.scalars(select(ChatLogModel).where(*filters)).all()
    if not rows:
        return _empty_stats()

    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    route_counts = Counter(row.route for row in rows if row.route)
    intent_counts = Counter(row.primary_intent for row in rows if row.primary_intent)
    template_counts = Counter(row.template_id for row in rows if row.template_id)
    return {
        "total": len(rows),
        "success_count": sum(1 for row in rows if row.status == "success"),
        "failed_count": sum(1 for row in rows if row.status == "failed"),
        "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "route_counts": dict(route_counts),
        "intent_counts": dict(intent_counts),
        "template_counts": dict(template_counts),
        "human_count": sum(1 for row in rows if row.need_human or row.route == "human"),
        "rag_count": sum(1 for row in rows if row.route == "rag_answer"),
        "template_count": sum(1 for row in rows if row.route == "template_reply"),
    }


def _get_session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(f"unsupported chat log provider: {settings.chat_log_provider}")
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(engine, tables=[ChatLogModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _payload_to_model(payload: dict) -> ChatLogModel:
    created_at = _parse_datetime(payload.get("created_at"))
    return ChatLogModel(
        trace_id=str(payload["trace_id"]),
        request_id=payload.get("request_id"),
        channel=str(payload.get("channel") or "unknown"),
        user_id=str(payload.get("user_id") or "unknown"),
        session_id=payload.get("session_id"),
        message_id=payload.get("message_id"),
        kb_id=payload.get("kb_id"),
        tenant_id=payload.get("tenant_id"),
        permission=payload.get("permission"),
        user_message=str(payload.get("user_message") or ""),
        answer=payload.get("answer"),
        route=payload.get("route"),
        reply_type=payload.get("reply_type"),
        primary_intent=payload.get("primary_intent"),
        secondary_intents_json=_json_dumps(payload.get("secondary_intents", [])),
        sales_stage=payload.get("sales_stage"),
        confidence=payload.get("confidence"),
        template_id=payload.get("template_id"),
        template_score=payload.get("template_score"),
        next_action=payload.get("next_action"),
        sources_json=_json_dumps(payload.get("sources", [])),
        usage_json=_json_dumps(payload.get("usage", {})),
        stage_latencies_json=_json_dumps(payload.get("stage_latencies", {})),
        metadata_json=_json_dumps(payload.get("metadata", {})),
        need_human=bool(payload.get("need_human", False)),
        policy_reason=payload.get("policy_reason"),
        intent_reason=payload.get("intent_reason"),
        latency_ms=payload.get("latency_ms"),
        status=str(payload.get("status") or "success"),
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
        created_at=created_at,
    )


def _model_to_item(row: ChatLogModel) -> dict:
    return {
        "trace_id": row.trace_id,
        "channel": row.channel,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "kb_id": row.kb_id,
        "tenant_id": row.tenant_id,
        "user_message": row.user_message,
        "answer": row.answer,
        "route": row.route,
        "reply_type": row.reply_type,
        "primary_intent": row.primary_intent,
        "secondary_intents": _json_loads(row.secondary_intents_json, []),
        "sales_stage": row.sales_stage,
        "confidence": row.confidence,
        "template_id": row.template_id,
        "next_action": row.next_action,
        "need_human": row.need_human,
        "sources": _json_loads(row.sources_json, []),
        "usage": _json_loads(row.usage_json, {}),
        "latency_ms": row.latency_ms,
        "status": row.status,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat(),
    }


def _model_to_detail(row: ChatLogModel) -> dict:
    detail = _model_to_item(row)
    detail.update(
        {
            "template_score": row.template_score,
            "policy_reason": row.policy_reason,
            "intent_reason": row.intent_reason,
            "stage_latencies": _json_loads(row.stage_latencies_json, {}),
            "metadata": _json_loads(row.metadata_json, {}),
        }
    )
    return detail


def _build_filters(**kwargs):
    filters = []
    for field in (
        "user_id",
        "session_id",
        "route",
        "primary_intent",
        "template_id",
        "status",
    ):
        value = kwargs.get(field)
        if value is not None:
            filters.append(getattr(ChatLogModel, field) == value)
    if kwargs.get("need_human") is not None:
        filters.append(ChatLogModel.need_human == kwargs["need_human"])
    if kwargs.get("keyword"):
        pattern = f"%{kwargs['keyword']}%"
        filters.append(
            or_(ChatLogModel.user_message.like(pattern), ChatLogModel.answer.like(pattern))
        )
    if kwargs.get("start_time"):
        filters.append(ChatLogModel.created_at >= _parse_datetime(kwargs["start_time"]))
    if kwargs.get("end_time"):
        filters.append(ChatLogModel.created_at <= _parse_datetime(kwargs["end_time"]))
    return filters


def _empty_stats() -> dict:
    return {
        "total": 0,
        "success_count": 0,
        "failed_count": 0,
        "avg_latency_ms": None,
        "route_counts": {},
        "intent_counts": {},
        "template_counts": {},
        "human_count": 0,
        "rag_count": 0,
        "template_count": 0,
    }


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                continue
            cleaned[key] = _sanitize_value(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            logger.warning("invalid chat log datetime: %s", value)
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
