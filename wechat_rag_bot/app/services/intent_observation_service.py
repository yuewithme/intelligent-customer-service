from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, create_engine, func, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMessageModel,
    IntentAnnotationModel,
    IntentObservationModel,
)
from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.intent_observation import IntentAnnotationRequest
from app.services.intent_taxonomy_service import normalize_taxonomy_value


logger = logging.getLogger("wechat_rag_bot.intent_observations")
_sessionmakers: dict[str, sessionmaker] = {}
_initialized_urls: set[str] = set()
ANNOTATION_STATUSES = {"confirmed", "corrected", "uncertain", "excluded"}
TRAINING_STATUSES = {"confirmed", "corrected"}
SENSITIVE_KEYS = {"token", "password", "api_key", "secret", "authorization"}
INTERNAL_OPERATOR_TITLES = {
    "销售工作台 - 销售 Agent",
    "意图识别日志 - 销售 Agent",
    "首单销售流程 - 销售 Agent",
    "标签管理 - 销售 Agent",
    "产品信息 - 销售 Agent",
    "养护手册 - 销售 Agent",
    "销售活动 - 销售 Agent",
    "未购 SOP - 销售 Agent",
    "服务 SOP - 销售 Agent",
    "转人工设置 - 销售 Agent",
    "模型配置 - 销售 Agent",
}


def is_intent_capture_noise(message: str) -> bool:
    return str(message or "").strip() in INTERNAL_OPERATOR_TITLES


def _should_capture_message(message: Any) -> bool:
    if is_intent_capture_noise(str(getattr(message, "message", "") or "")):
        return False
    metadata = getattr(message, "metadata", None)
    if not isinstance(metadata, dict):
        return True
    if metadata.get("self") is True:
        return False
    return str(metadata.get("origin") or "") not in {
        "sales_workbench",
        "wechat_client",
    }


async def record_intent_observation(
    *,
    message: Any,
    intent: IntentResult,
    candidates: list[dict] | None = None,
    context: list[dict] | None = None,
) -> None:
    if (
        not get_settings().intent_observation_enabled
        or not _should_capture_message(message)
    ):
        return
    try:
        now = _utcnow()
        payload = _observation_payload(
            message=message,
            intent=intent,
            candidates=candidates,
            context=context,
        )
        with _get_session() as session:
            row = session.scalar(
                select(IntentObservationModel).where(
                    IntentObservationModel.trace_id == payload["trace_id"]
                )
            )
            if row is None:
                row = IntentObservationModel(
                    **payload,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
                row.updated_at = now
            session.commit()
    except Exception:
        logger.exception("failed to record intent observation")


async def finalize_intent_observation(trace_id: str, intent: IntentResult) -> None:
    if not get_settings().intent_observation_enabled:
        return
    try:
        with _get_session() as session:
            row = session.scalar(
                select(IntentObservationModel).where(
                    IntentObservationModel.trace_id == trace_id
                )
            )
            if row is None:
                return
            row.final_route = intent.route
            row.sales_stage = intent.sales_stage
            row.updated_at = _utcnow()
            session.commit()
    except Exception:
        logger.exception("failed to finalize intent observation")


async def record_bypassed_intent_observation(
    *,
    trace_id: str,
    channel: str,
    user_id: str,
    session_id: str | None,
    user_message: str,
    final_route: str,
    primary_domain: str = "conversation",
    primary_goal: str = "unclear",
    issues: list[str] | None = None,
    scope: str = "ambiguous",
    reason: str = "bypassed_before_classifier",
    metadata: dict | None = None,
) -> None:
    """Persist inbound turns handled by fixed or state routes before the classifier."""

    if not get_settings().intent_observation_enabled:
        return
    message = NormalizedMessage(
        trace_id=trace_id,
        channel=channel,
        user_id=user_id,
        session_id=session_id or "default",
        message_id=(metadata or {}).get("message_id"),
        message=user_message,
        kb_id=get_settings().wechat_default_kb_id,
        metadata=metadata or {},
    )
    allowed_routes = {
        "template_reply",
        "rag_answer",
        "template_then_rag",
        "clarify",
        "human",
        "chitchat",
        "unsupported",
    }
    predicted_route = final_route if final_route in allowed_routes else "clarify"
    if primary_domain == "out_of_scope":
        predicted_route = "unsupported"
    elif primary_goal == "request_material":
        predicted_route = "rag_answer"
    elif primary_goal == "request_service":
        predicted_route = "template_reply"
    intent = IntentResult(
        route=predicted_route,
        primary_intent="unknown",
        primary_domain=primary_domain,
        primary_goal=primary_goal,
        issues=issues or [],
        scope=scope,
        classifier_source="bypass_route",
        confidence=0.0,
        reason=reason,
    )
    await record_intent_observation(
        message=message,
        intent=intent,
        candidates=[],
        context=[],
    )
    try:
        with _get_session() as session:
            row = session.scalar(
                select(IntentObservationModel).where(
                    IntentObservationModel.trace_id == trace_id
                )
            )
            if row is not None:
                row.final_route = final_route
                row.updated_at = _utcnow()
                session.commit()
    except Exception:
        logger.exception("failed to set bypass observation route")


async def list_intent_observations(
    *,
    page: int = 1,
    page_size: int = 50,
    annotation_status: str | None = None,
    primary_domain: str | None = None,
    primary_goal: str | None = None,
    scope: str | None = None,
    classifier_source: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    threshold = get_settings().intent_auto_confirm_threshold
    with _get_session() as session:
        filters = _observation_filters(
            primary_domain=primary_domain,
            primary_goal=primary_goal,
            scope=scope,
            classifier_source=classifier_source,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )
        latest_status = _latest_annotation_status_subquery()
        unannotated = latest_status.is_(None)
        auto_confirmed = and_(
            unannotated,
            IntentObservationModel.confidence.is_not(None),
            IntentObservationModel.confidence >= threshold,
        )
        needs_review = and_(
            unannotated,
            or_(
                IntentObservationModel.confidence.is_(None),
                IntentObservationModel.confidence < threshold,
            ),
        )
        if annotation_status == "pending":
            filters.append(needs_review)
        elif annotation_status == "confirmed":
            filters.append(or_(latest_status == "confirmed", auto_confirmed))
        elif annotation_status in ANNOTATION_STATUSES:
            filters.append(latest_status == annotation_status)

        total = session.scalar(
            select(func.count()).select_from(IntentObservationModel).where(*filters)
        )
        rows = session.scalars(
            select(IntentObservationModel)
            .where(*filters)
            .order_by(IntentObservationModel.created_at.desc(), IntentObservationModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        annotations = _latest_annotations(session, [row.id for row in rows])
        visibility_filters = _visible_observation_filters()
        reviewed_count = int(
            session.scalar(
                select(func.count())
                .select_from(IntentObservationModel)
                .where(*visibility_filters, latest_status.is_not(None))
            )
            or 0
        )
        pending_count = int(
            session.scalar(
                select(func.count())
                .select_from(IntentObservationModel)
                .where(*visibility_filters, needs_review)
            )
            or 0
        )
        accepted_count = int(
            session.scalar(
                select(func.count())
                .select_from(IntentObservationModel)
                .where(
                    *visibility_filters,
                    or_(latest_status == "confirmed", auto_confirmed),
                )
            )
            or 0
        )
        corrected_count = int(
            session.scalar(
                select(func.count())
                .select_from(IntentObservationModel)
                .where(*visibility_filters, latest_status == "corrected")
            )
            or 0
        )
    return {
        "items": [
            _observation_to_item(row, annotations.get(row.id)) for row in rows
        ],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "pending_count": pending_count,
        "reviewed_count": reviewed_count,
        "accepted_count": accepted_count,
        "corrected_count": corrected_count,
    }


async def get_intent_observation(trace_id: str) -> dict | None:
    with _get_session() as session:
        row = session.scalar(
            select(IntentObservationModel).where(
                IntentObservationModel.trace_id == trace_id
            )
        )
        if row is None:
            return None
        history = session.scalars(
            select(IntentAnnotationModel)
            .where(IntentAnnotationModel.observation_id == row.id)
            .order_by(IntentAnnotationModel.id.desc())
        ).all()
    latest = history[0] if history else None
    detail = _observation_to_item(row, latest)
    detail.update(
        {
            "context": _json_loads(row.context_json, []),
            "raw_prediction": _json_loads(row.raw_prediction_json, {}),
            "candidate_labels": _json_loads(row.candidate_labels_json, []),
            "annotation_history": [_annotation_to_item(item) for item in history],
        }
    )
    return detail


async def create_intent_annotation(
    trace_id: str,
    request: IntentAnnotationRequest,
) -> dict:
    _validate_annotation(request)
    with _get_session() as session:
        observation = session.scalar(
            select(IntentObservationModel).where(
                IntentObservationModel.trace_id == trace_id
            )
        )
        if observation is None:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="意图观测记录不存在",
                status_code=404,
            )
        corrected = request.status == "corrected"
        annotation = IntentAnnotationModel(
            observation_id=observation.id,
            trace_id=trace_id,
            status=request.status,
            primary_domain=request.primary_domain if corrected else None,
            secondary_domains_json=_json_dumps(
                request.secondary_domains if corrected else []
            ),
            primary_goal=request.primary_goal if corrected else None,
            secondary_goals_json=_json_dumps(
                request.secondary_goals if corrected else []
            ),
            issues_json=_json_dumps(request.issues if corrected else []),
            scope=request.scope if corrected else None,
            note=request.note,
            annotator_id=request.annotator_id,
            taxonomy_version=observation.taxonomy_version,
            created_at=_utcnow(),
        )
        session.add(annotation)
        session.commit()
        session.refresh(annotation)
    return _annotation_to_item(annotation)


async def build_training_dataset(
    *,
    limit: int | None = None,
    redact_pii: bool = True,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    settings = get_settings()
    limit = min(
        max(limit or settings.intent_training_export_max_rows, 1),
        settings.intent_training_export_max_rows,
    )
    latest_ids = (
        select(func.max(IntentAnnotationModel.id).label("id"))
        .group_by(IntentAnnotationModel.observation_id)
        .subquery()
    )
    threshold = settings.intent_auto_confirm_threshold
    filters = _visible_observation_filters()
    if start_time:
        filters.append(IntentObservationModel.created_at >= _parse_datetime(start_time))
    if end_time:
        filters.append(IntentObservationModel.created_at <= _parse_datetime(end_time))
    with _get_session() as session:
        rows = session.execute(
            select(IntentObservationModel, IntentAnnotationModel)
            .outerjoin(
                IntentAnnotationModel,
                and_(
                    IntentAnnotationModel.observation_id == IntentObservationModel.id,
                    IntentAnnotationModel.id.in_(select(latest_ids.c.id)),
                ),
            )
            .where(
                *filters,
                or_(
                    IntentAnnotationModel.status.in_(TRAINING_STATUSES),
                    and_(
                        IntentAnnotationModel.id.is_(None),
                        IntentObservationModel.confidence.is_not(None),
                        IntentObservationModel.confidence >= threshold,
                    ),
                ),
            )
            .order_by(IntentObservationModel.created_at.asc())
            .limit(limit)
        ).all()
    return [
        _training_record(observation, annotation, redact_pii=redact_pii)
        for observation, annotation in rows
    ]


def _observation_payload(
    *,
    message: Any,
    intent: IntentResult,
    candidates: list[dict] | None,
    context: list[dict] | None,
) -> dict:
    settings = get_settings()
    trace_id = str(getattr(message, "trace_id", "") or "").strip()
    if not trace_id:
        raise ValueError("trace_id is required")
    metadata = getattr(message, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    channel = str(getattr(message, "channel", "unknown") or "unknown")
    user_id = str(getattr(message, "user_id", "unknown") or "unknown")
    session_id = getattr(message, "session_id", None)
    conversation_id = str(metadata.get("conversation_id") or "").strip()
    if not conversation_id and user_id != "unknown":
        conversation_id = _make_conversation_id(channel, user_id, session_id)
    conversation_message_ids = _integer_list(
        metadata.get("conversation_message_ids")
    )
    return {
        "trace_id": trace_id,
        "request_id": trace_id,
        "channel": channel,
        "user_id": user_id,
        "session_id": session_id,
        "message_id": getattr(message, "message_id", None),
        "tenant_id": getattr(message, "tenant_id", None),
        "conversation_id": conversation_id or None,
        "conversation_message_ids_json": _json_dumps(conversation_message_ids),
        "user_message": str(getattr(message, "message", "") or "")[
            : settings.chat_log_max_message_length
        ],
        "context_json": _json_dumps(_compact_context(context)),
        "taxonomy_version": intent.taxonomy_version,
        "classifier_source": intent.classifier_source,
        "classifier_provider": intent.classifier_provider,
        "classifier_model": intent.classifier_model,
        "raw_prediction_json": _json_dumps(_sanitize(intent.raw_prediction)),
        "candidate_labels_json": _json_dumps(_compact_candidates(candidates)),
        "primary_domain": intent.primary_domain,
        "secondary_domains_json": _json_dumps(intent.secondary_domains),
        "primary_goal": intent.primary_goal,
        "secondary_goals_json": _json_dumps(intent.secondary_goals),
        "issues_json": _json_dumps(intent.issues),
        "scope": intent.scope,
        "evidence_json": _json_dumps(
            [item.model_dump() for item in intent.evidence]
        ),
        "confidence": intent.confidence,
        "intent_reason": intent.reason,
        "predicted_route": intent.route,
        "final_route": intent.route,
        "primary_intent": intent.primary_intent,
        "sales_stage": intent.sales_stage,
        "status": "observed",
    }


def _observation_to_item(
    row: IntentObservationModel,
    annotation: IntentAnnotationModel | None,
) -> dict:
    status = _effective_annotation_status(row, annotation)
    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "channel": row.channel,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "tenant_id": row.tenant_id,
        "conversation_id": row.conversation_id,
        "conversation_message_ids": _json_loads(
            row.conversation_message_ids_json, []
        ),
        "user_message": row.user_message,
        "taxonomy_version": row.taxonomy_version,
        "classifier_source": row.classifier_source,
        "classifier_provider": row.classifier_provider,
        "classifier_model": row.classifier_model,
        "primary_domain": row.primary_domain,
        "secondary_domains": _json_loads(row.secondary_domains_json, []),
        "primary_goal": row.primary_goal,
        "secondary_goals": _json_loads(row.secondary_goals_json, []),
        "issues": _json_loads(row.issues_json, []),
        "scope": row.scope,
        "evidence": _json_loads(row.evidence_json, []),
        "confidence": row.confidence,
        "intent_reason": row.intent_reason,
        "predicted_route": row.predicted_route,
        "final_route": row.final_route,
        "primary_intent": row.primary_intent,
        "sales_stage": row.sales_stage,
        "annotation_status": status,
        "annotation_origin": "human" if annotation else "automatic",
        "needs_review": status == "pending",
        "latest_annotation": _annotation_to_item(annotation) if annotation else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _annotation_to_item(row: IntentAnnotationModel) -> dict:
    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "status": row.status,
        "primary_domain": row.primary_domain,
        "secondary_domains": _json_loads(row.secondary_domains_json, []),
        "primary_goal": row.primary_goal,
        "secondary_goals": _json_loads(row.secondary_goals_json, []),
        "issues": _json_loads(row.issues_json, []),
        "scope": row.scope,
        "note": row.note,
        "annotator_id": row.annotator_id,
        "taxonomy_version": row.taxonomy_version,
        "created_at": row.created_at.isoformat(),
    }


def _training_record(
    observation: IntentObservationModel,
    annotation: IntentAnnotationModel | None,
    *,
    redact_pii: bool,
) -> dict:
    corrected = annotation is not None and annotation.status == "corrected"
    context = _json_loads(observation.context_json, [])
    message = observation.user_message
    if redact_pii:
        context = [
            {**turn, "content": _redact_pii(str(turn.get("content") or ""))}
            for turn in context
            if isinstance(turn, dict)
        ]
        message = _redact_pii(message)
    return {
        "sample_id": observation.trace_id,
        "customer_group_id": _stable_group_id(
            observation.tenant_id, observation.user_id
        ),
        "session_group_id": _stable_group_id(
            observation.tenant_id,
            observation.user_id,
            observation.session_id or "default",
        ),
        "channel": observation.channel,
        "taxonomy_version": observation.taxonomy_version,
        "context": context,
        "text": message,
        "labels": {
            "primary_domain": annotation.primary_domain if corrected else observation.primary_domain,
            "secondary_domains": (
                _json_loads(annotation.secondary_domains_json, [])
                if corrected
                else _json_loads(observation.secondary_domains_json, [])
            ),
            "primary_goal": annotation.primary_goal if corrected else observation.primary_goal,
            "secondary_goals": (
                _json_loads(annotation.secondary_goals_json, [])
                if corrected
                else _json_loads(observation.secondary_goals_json, [])
            ),
            "issues": (
                _json_loads(annotation.issues_json, [])
                if corrected
                else _json_loads(observation.issues_json, [])
            ),
            "scope": annotation.scope if corrected else observation.scope,
        },
        "annotation": {
            "status": annotation.status if annotation else "auto_confirmed",
            "origin": "human" if annotation else "confidence_threshold",
            "annotator_id": annotation.annotator_id if annotation else "system",
            "annotated_at": (
                annotation.created_at.isoformat()
                if annotation
                else observation.updated_at.isoformat()
            ),
            "note": (
                _redact_pii(annotation.note or "")
                if annotation and redact_pii and annotation.note
                else annotation.note if annotation else None
            ),
        },
    }


def _validate_annotation(request: IntentAnnotationRequest) -> None:
    if request.status != "corrected":
        return
    if normalize_taxonomy_value("domain", request.primary_domain) is None:
        raise _invalid_label("Domain", request.primary_domain)
    if normalize_taxonomy_value("goal", request.primary_goal) is None:
        raise _invalid_label("Goal", request.primary_goal)
    for value in request.secondary_domains:
        if normalize_taxonomy_value("domain", value) is None:
            raise _invalid_label("Domain", value)
    for value in request.secondary_goals:
        if normalize_taxonomy_value("goal", value) is None:
            raise _invalid_label("Goal", value)
    for value in request.issues:
        if normalize_taxonomy_value("issue", value) is None:
            raise _invalid_label("Issue", value)


def _invalid_label(kind: str, value: object) -> AppError:
    return AppError(
        ErrorCode.REQUEST_INVALID,
        message=f"无效的 {kind} 标签: {value}",
        status_code=422,
    )


def _latest_annotation_status_subquery():
    return (
        select(IntentAnnotationModel.status)
        .where(IntentAnnotationModel.observation_id == IntentObservationModel.id)
        .order_by(IntentAnnotationModel.id.desc())
        .limit(1)
        .correlate(IntentObservationModel)
        .scalar_subquery()
    )


def _latest_annotations(
    session: Session,
    observation_ids: list[int],
) -> dict[int, IntentAnnotationModel]:
    if not observation_ids:
        return {}
    latest_ids = (
        select(func.max(IntentAnnotationModel.id).label("id"))
        .where(IntentAnnotationModel.observation_id.in_(observation_ids))
        .group_by(IntentAnnotationModel.observation_id)
        .subquery()
    )
    rows = session.scalars(
        select(IntentAnnotationModel).where(
            IntentAnnotationModel.id.in_(select(latest_ids.c.id))
        )
    ).all()
    return {row.observation_id: row for row in rows}


def _observation_filters(**kwargs) -> list:
    filters = _visible_observation_filters()
    for field in ("primary_domain", "primary_goal", "scope", "classifier_source"):
        value = kwargs.get(field)
        if value:
            filters.append(getattr(IntentObservationModel, field) == value)
    if kwargs.get("min_confidence") is not None:
        filters.append(IntentObservationModel.confidence >= kwargs["min_confidence"])
    if kwargs.get("max_confidence") is not None:
        filters.append(IntentObservationModel.confidence <= kwargs["max_confidence"])
    if kwargs.get("keyword"):
        pattern = f"%{kwargs['keyword']}%"
        filters.append(
            or_(
                IntentObservationModel.user_message.like(pattern),
                IntentObservationModel.user_id.like(pattern),
            )
        )
    if kwargs.get("start_time"):
        filters.append(
            IntentObservationModel.created_at >= _parse_datetime(kwargs["start_time"])
        )
    if kwargs.get("end_time"):
        filters.append(
            IntentObservationModel.created_at <= _parse_datetime(kwargs["end_time"])
        )
    return filters


def _visible_observation_filters() -> list:
    return [
        IntentObservationModel.user_message.not_in(INTERNAL_OPERATOR_TITLES)
    ]


def _effective_annotation_status(
    row: IntentObservationModel,
    annotation: IntentAnnotationModel | None,
) -> str:
    if annotation is not None:
        return annotation.status
    confidence = row.confidence
    if (
        confidence is not None
        and confidence >= get_settings().intent_auto_confirm_threshold
    ):
        return "confirmed"
    return "pending"


def _compact_context(context: list[dict] | None) -> list[dict]:
    result = []
    for turn in (context or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "")[:1000]
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content})
    return result


def _compact_candidates(candidates: list[dict] | None) -> list[dict]:
    result = []
    for item in (candidates or [])[:50]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                key: item[key]
                for key in ("kind", "id", "name", "example_id", "score")
                if key in item
            }
        )
    return result


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _redact_pii(value: str) -> str:
    value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[MOBILE]", value)
    value = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[ID_CARD]", value)
    value = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "[EMAIL]",
        value,
    )
    return value


def _stable_group_id(*values: str | None) -> str:
    payload = "|".join(str(value or "") for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _make_conversation_id(
    channel: str, user_id: str, session_id: str | None
) -> str:
    return f"{channel}:{user_id}:{session_id or 'default'}"


def _integer_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def _get_session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(
            f"unsupported intent observation provider: {settings.chat_log_provider}"
        )
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                IntentObservationModel.__table__,
                IntentAnnotationModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    if settings.chat_log_db_url not in _initialized_urls:
        _ensure_intent_observation_columns(factory)
        _backfill_observation_locators_and_gaps(factory)
        _initialized_urls.add(settings.chat_log_db_url)
    return factory()


def _ensure_intent_observation_columns(factory: sessionmaker) -> None:
    with factory() as session:
        bind = session.get_bind()
        columns = {
            column["name"]
            for column in inspect(bind).get_columns("intent_observations")
        }
        if "conversation_id" not in columns:
            session.execute(
                text(
                    "ALTER TABLE intent_observations "
                    "ADD COLUMN conversation_id VARCHAR(256)"
                )
            )
        if "conversation_message_ids_json" not in columns:
            session.execute(
                text(
                    "ALTER TABLE intent_observations "
                    "ADD COLUMN conversation_message_ids_json TEXT DEFAULT '[]'"
                )
            )
        session.commit()


def _backfill_observation_locators_and_gaps(factory: sessionmaker) -> None:
    """Link existing observations to workbench messages and expose historical gaps."""

    with factory() as session:
        bind = session.get_bind()
        if not inspect(bind).has_table("conversation_messages"):
            return
        observations = session.scalars(
            select(IntentObservationModel).order_by(IntentObservationModel.id.asc())
        ).all()
        if not observations:
            return

        linked_message_ids: set[int] = set()
        for row in observations:
            conversation_id = row.conversation_id or _make_conversation_id(
                row.channel, row.user_id, row.session_id
            )
            row.conversation_id = conversation_id
            existing_ids = _integer_list(
                _json_loads(row.conversation_message_ids_json, [])
            )
            if existing_ids:
                linked_message_ids.update(existing_ids)
                continue

            start = min(row.created_at, row.updated_at) - timedelta(minutes=20)
            end = max(row.created_at, row.updated_at) + timedelta(minutes=2)
            candidates = session.scalars(
                select(ConversationMessageModel)
                .where(
                    ConversationMessageModel.conversation_id == conversation_id,
                    ConversationMessageModel.sender_type == "customer",
                    ConversationMessageModel.created_at >= start,
                    ConversationMessageModel.created_at <= end,
                )
                .order_by(
                    ConversationMessageModel.created_at.asc(),
                    ConversationMessageModel.id.asc(),
                )
            ).all()
            parts = {
                part.strip()
                for part in str(row.user_message or "").splitlines()
                if part.strip()
            }
            matches = [
                message.id
                for message in candidates
                if (
                    row.message_id
                    and message.message_id == row.message_id
                )
                or str(message.content or "").strip() in parts
                or str(message.content or "").strip() == str(row.user_message or "").strip()
            ]
            row.conversation_message_ids_json = _json_dumps(matches)
            linked_message_ids.update(matches)

        earliest = min(row.created_at for row in observations) - timedelta(minutes=30)
        missing_messages = session.scalars(
            select(ConversationMessageModel)
            .where(
                ConversationMessageModel.sender_type == "customer",
                ConversationMessageModel.route == "inbound_text",
                ConversationMessageModel.created_at >= earliest,
            )
            .order_by(
                ConversationMessageModel.created_at.asc(),
                ConversationMessageModel.id.asc(),
            )
        ).all()
        for message in missing_messages:
            if (
                message.id in linked_message_ids
                or is_intent_capture_noise(message.content)
            ):
                continue
            parts = str(message.conversation_id or "").split(":", 2)
            if len(parts) != 3:
                continue
            channel, user_id, session_id = parts
            trace_id = f"capture_gap_{message.id}"
            if session.scalar(
                select(IntentObservationModel.id).where(
                    IntentObservationModel.trace_id == trace_id
                )
            ) is not None:
                continue
            session.add(
                IntentObservationModel(
                    trace_id=trace_id,
                    request_id=trace_id,
                    channel=channel,
                    user_id=user_id,
                    session_id=session_id,
                    message_id=message.message_id,
                    tenant_id="tenant_default",
                    conversation_id=message.conversation_id,
                    conversation_message_ids_json=_json_dumps([message.id]),
                    user_message=message.content,
                    context_json="[]",
                    taxonomy_version="1.0",
                    classifier_source="capture_gap",
                    raw_prediction_json="{}",
                    candidate_labels_json="[]",
                    primary_domain="conversation",
                    secondary_domains_json="[]",
                    primary_goal="unclear",
                    secondary_goals_json="[]",
                    issues_json="[]",
                    scope="ambiguous",
                    evidence_json="[]",
                    confidence=0.0,
                    intent_reason="historical_capture_gap",
                    predicted_route="clarify",
                    final_route="capture_gap",
                    primary_intent="unknown",
                    status="observed",
                    created_at=message.created_at,
                    updated_at=_utcnow(),
                )
            )
        session.commit()


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return _utcnow()


def _utcnow() -> datetime:
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
