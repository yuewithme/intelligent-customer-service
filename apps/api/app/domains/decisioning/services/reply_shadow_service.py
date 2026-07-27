from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.decisioning.schemas.reply_shadow import (
    ReplyShadowAnnotationRequest,
    ReplyShadowDecision,
)
from app.infrastructure.database.models import (
    Base,
    ReplyShadowAnnotationModel,
    ReplyShadowRunModel,
)
from app.integrations.ai.services.llm_service import generate_json


_sessionmakers: dict[str, sessionmaker] = {}
_tasks: set[asyncio.Task] = set()
logger = logging.getLogger("wechat_rag_bot.reply_shadow")
_MAX_SNAPSHOT_TEXT_CHARS = 8000
_MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:0\d{2,3}-?)?\d{7,8}(?!\d)")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_SENSITIVE_KEYS = {
    "authorization",
    "token",
    "password",
    "secret",
    "api_key",
    "address",
    "shipping_address",
    "receiver_address",
}
_HIGH_RISK_WORDS = (
    "价格",
    "优惠",
    "订单",
    "支付",
    "付款",
    "退款",
    "退货",
    "投诉",
    "物流",
    "发货",
    "人工",
    "不买",
    "别再",
)
_INTERNAL_LEAK_KEYS = (
    "sales_stage",
    "tool_state",
    "decision_trace",
    "question_slot",
    "verified_facts",
    "payment_status",
    "order_lookup",
)
_REVIEW_VERDICTS = {
    "primary_better",
    "shadow_better",
    "tie",
    "both_bad",
    "uncertain",
    "excluded",
}


def reply_shadow_selected(trace_id: str, customer_message: str) -> bool:
    settings = get_settings()
    if not settings.reply_shadow_enabled:
        return False
    if settings.reply_shadow_high_risk_always and any(
        word in customer_message for word in _HIGH_RISK_WORDS
    ):
        return True
    if settings.reply_shadow_sample_percent <= 0:
        return False
    bucket = int(hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < settings.reply_shadow_sample_percent


def schedule_reply_shadow_evaluation(
    *,
    message,
    user_state,
    intent,
    plan,
    reply,
) -> None:
    if not reply_shadow_selected(message.trace_id, message.message):
        return
    snapshot = build_reply_shadow_snapshot(
        message=message,
        user_state=user_state,
        intent=intent,
        plan=plan,
        reply=reply,
    )
    task = asyncio.create_task(evaluate_and_record_reply_shadow(snapshot))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def build_reply_shadow_snapshot(
    *,
    message,
    user_state,
    intent,
    plan,
    reply,
) -> dict:
    recent_turns = user_state.metadata.get("recent_turns", [])
    if not isinstance(recent_turns, list):
        recent_turns = []
    state_metadata = {
        key: user_state.metadata.get(key)
        for key in (
            "sales_action",
            "sales_stage_decision",
            "memory_v2_context",
        )
        if user_state.metadata.get(key) is not None
    }
    return _sanitize(
        {
            "trace_id": message.trace_id,
            "channel": message.channel,
            "customer_message": message.message,
            "recent_turns": recent_turns[-8:],
            "customer_state": {
                "sales_stage": user_state.sales_stage,
                "customer_tags": user_state.customer_tags,
                "interested_products": user_state.interested_products,
                "last_intent": user_state.last_intent,
                "last_route": user_state.last_route,
                "order_status": user_state.order_status,
                "risk_level": user_state.risk_level,
                "decision_context": state_metadata,
            },
            "intent": intent.model_dump(mode="json"),
            "reply_plan": plan.model_dump(mode="json"),
            "production": {
                "sales_stage": intent.sales_stage,
                "sales_action": (
                    (reply.metadata.get("sales_action") or {}).get("sales_action")
                    if isinstance(reply.metadata.get("sales_action"), dict)
                    else None
                ),
                "reply": reply.answer,
                "need_human": reply.need_human,
                "next_action": reply.next_action,
                "route": reply.route,
            },
        }
    )


async def evaluate_and_record_reply_shadow(snapshot: dict) -> None:
    settings = get_settings()
    started = time.perf_counter()
    shadow = None
    error_class = None
    try:
        raw = await generate_json(
            _shadow_prompt(snapshot),
            purpose="reply_shadow",
            provider_override=settings.reply_shadow_llm_provider,
            model_override=settings.reply_shadow_llm_model,
            shadow=True,
            prompt_version=settings.reply_shadow_prompt_version,
        )
        shadow = ReplyShadowDecision.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        error_class = type(exc).__name__
    try:
        record_reply_shadow(
            snapshot=snapshot,
            shadow=shadow,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "failed to persist reply shadow run trace_id=%s",
            snapshot.get("trace_id"),
        )


def record_reply_shadow(
    *,
    snapshot: dict,
    shadow: ReplyShadowDecision | None,
    latency_ms: int,
    error_class: str | None = None,
) -> None:
    settings = get_settings()
    primary = snapshot.get("production") or {}
    shadow_payload = shadow.model_dump(mode="json") if shadow is not None else {}
    agreement = bool(
        shadow is not None
        and primary.get("sales_stage") == shadow.sales_stage
        and primary.get("route") == shadow.route
        and primary.get("sales_action") == shadow.sales_action
        and bool(primary.get("need_human")) == shadow.need_human
        and shadow.follow_up.needed is False
    )
    auto_issues = _auto_issues(snapshot, shadow)
    priority = _review_priority(snapshot, shadow, auto_issues, agreement)
    now = datetime.now(timezone.utc)
    with _get_session() as session:
        existing = session.scalar(
            select(ReplyShadowRunModel).where(
                ReplyShadowRunModel.trace_id == str(snapshot.get("trace_id") or ""),
                ReplyShadowRunModel.experiment_id
                == settings.reply_shadow_experiment_id,
                ReplyShadowRunModel.candidate_version
                == settings.reply_shadow_candidate_version,
                ReplyShadowRunModel.prompt_version
                == settings.reply_shadow_prompt_version,
            )
        )
        if existing is None:
            existing = ReplyShadowRunModel(
                trace_id=str(snapshot.get("trace_id") or ""),
                candidate_version=settings.reply_shadow_candidate_version,
                created_at=now,
            )
            session.add(existing)
        existing.experiment_id = settings.reply_shadow_experiment_id
        existing.prompt_version = settings.reply_shadow_prompt_version
        existing.channel = str(snapshot.get("channel") or "unknown")
        existing.user_message = str(snapshot.get("customer_message") or "")[
            : settings.chat_log_max_message_length
        ]
        existing.input_snapshot_json = _json_dumps(snapshot)
        existing.primary_json = _json_dumps(primary)
        existing.shadow_json = _json_dumps(shadow_payload)
        existing.decision_agreement = agreement
        existing.auto_issues_json = _json_dumps(auto_issues)
        existing.review_priority = priority
        existing.status = "success" if shadow is not None else "failed"
        existing.error_class = (error_class or "")[:128] or None
        existing.latency_ms = max(latency_ms, 0)
        existing.updated_at = now
        session.commit()


async def list_reply_shadow_runs(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    review_status: str | None = None,
    review_priority: str | None = None,
    keyword: str | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    with _get_session() as session:
        filters = []
        if status:
            filters.append(ReplyShadowRunModel.status == status)
        if review_priority:
            filters.append(ReplyShadowRunModel.review_priority == review_priority)
        if keyword:
            filters.append(ReplyShadowRunModel.user_message.contains(keyword))
        rows = session.scalars(
            select(ReplyShadowRunModel)
            .where(*filters)
            .order_by(
                ReplyShadowRunModel.created_at.desc(),
                ReplyShadowRunModel.id.desc(),
            )
        ).all()
        annotations = _latest_annotations(session, [row.id for row in rows])
    items = [
        _run_to_item(row, annotations.get(row.id), include_snapshot=False)
        for row in rows
    ]
    if review_status == "pending":
        items = [item for item in items if item["latest_annotation"] is None]
    elif review_status == "reviewed":
        items = [item for item in items if item["latest_annotation"] is not None]
    elif review_status in _REVIEW_VERDICTS:
        items = [
            item
            for item in items
            if (item["latest_annotation"] or {}).get("verdict") == review_status
        ]
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pending_count": sum(
            1 for item in items if item["latest_annotation"] is None
        ),
        "reviewed_count": sum(
            1 for item in items if item["latest_annotation"] is not None
        ),
    }


async def get_reply_shadow_run(trace_id: str) -> dict | None:
    with _get_session() as session:
        row = session.scalar(
            select(ReplyShadowRunModel)
            .where(ReplyShadowRunModel.trace_id == trace_id)
            .order_by(ReplyShadowRunModel.created_at.desc())
        )
        if row is None:
            return None
        annotations = session.scalars(
            select(ReplyShadowAnnotationModel)
            .where(ReplyShadowAnnotationModel.reply_shadow_run_id == row.id)
            .order_by(
                ReplyShadowAnnotationModel.created_at.desc(),
                ReplyShadowAnnotationModel.id.desc(),
            )
        ).all()
    item = _run_to_item(
        row,
        annotations[0] if annotations else None,
        include_snapshot=True,
    )
    item["annotation_history"] = [_annotation_to_item(row) for row in annotations]
    return item


async def create_reply_shadow_annotation(
    trace_id: str,
    request: ReplyShadowAnnotationRequest,
) -> dict:
    with _get_session() as session:
        run = session.scalar(
            select(ReplyShadowRunModel)
            .where(ReplyShadowRunModel.trace_id == trace_id)
            .order_by(ReplyShadowRunModel.created_at.desc())
        )
        if run is None:
            raise LookupError("reply shadow run not found")
        row = ReplyShadowAnnotationModel(
            reply_shadow_run_id=run.id,
            trace_id=trace_id,
            verdict=request.verdict,
            error_tags_json=_json_dumps(list(dict.fromkeys(request.error_tags))),
            note=request.note,
            annotator_id=request.annotator_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return _annotation_to_item(row)


async def build_reply_shadow_dataset(
    *,
    limit: int | None = None,
    redact_pii: bool = True,
) -> list[dict]:
    settings = get_settings()
    effective_limit = min(
        limit or settings.reply_shadow_export_max_rows,
        settings.reply_shadow_export_max_rows,
    )
    with _get_session() as session:
        rows = session.scalars(
            select(ReplyShadowRunModel)
            .where(ReplyShadowRunModel.status == "success")
            .order_by(ReplyShadowRunModel.created_at.asc())
        ).all()
        annotations = _latest_annotations(session, [row.id for row in rows])
    result = []
    for row in rows:
        annotation = annotations.get(row.id)
        if annotation is None or annotation.verdict not in {
            "primary_better",
            "shadow_better",
            "tie",
        }:
            continue
        snapshot = _json_loads(row.input_snapshot_json, {})
        primary = _json_loads(row.primary_json, {})
        shadow = _json_loads(row.shadow_json, {})
        if redact_pii:
            snapshot = _sanitize(snapshot)
            primary = _sanitize(primary)
            shadow = _sanitize(shadow)
        preferred = (
            shadow if annotation.verdict == "shadow_better" else primary
        )
        result.append(
            {
                "trace_id": row.trace_id,
                "experiment_id": row.experiment_id,
                "candidate_version": row.candidate_version,
                "input": snapshot,
                "primary": primary,
                "shadow": shadow,
                "label": {
                    "verdict": annotation.verdict,
                    "error_tags": _json_loads(annotation.error_tags_json, []),
                    "note": (
                        _redact_text(annotation.note)
                        if redact_pii and annotation.note
                        else annotation.note
                    ),
                },
                "preferred_decision": preferred,
            }
        )
        if len(result) >= effective_limit:
            break
    return result


def _shadow_prompt(snapshot: dict) -> str:
    return (
        "你是私域兰花销售系统的影子决策器。你只做离线候选判断，"
        "绝不能声称已经发送消息、修改订单、安排人工或执行其他外部动作。"
        "请基于输入中的已验证事实，提出比 production 更合适的本轮销售决策。"
        "先回答客户当前问题；不得编造价格、库存、订单、物流、优惠或服务事实；"
        "最多追问一个关键问题。只有确有后续价值时才设置 follow_up.needed=true，"
        "并给出具体动作、1到720小时的 due_in_hours，以及停止打扰所需的取消条件。"
        "客户明确拒绝、已成交、退款投诉或人工接管时，不得建议普通销售跟进。"
        "只输出一个 JSON 对象，字段必须为："
        "sales_stage, route, sales_action, reply, need_human, next_action, follow_up, "
        "facts_used, confidence, reason。follow_up 必须包含 needed, action, "
        "due_in_hours, cancel_conditions。route 只能是 template_reply、rag_answer、"
        "human、chitchat、unsupported、clarify 之一；sales_action 描述本轮销售动作。"
        "\n输入："
        + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    )


def _auto_issues(
    snapshot: dict,
    shadow: ReplyShadowDecision | None,
) -> list[str]:
    if shadow is None:
        return ["shadow_failed"]
    issues = []
    primary = snapshot.get("production") or {}
    if primary.get("route") != shadow.route:
        issues.append("route_disagreement")
    if primary.get("sales_action") != shadow.sales_action:
        issues.append("sales_action_disagreement")
    if primary.get("sales_stage") != shadow.sales_stage:
        issues.append("stage_disagreement")
    if bool(primary.get("need_human")) != shadow.need_human:
        issues.append("handoff_disagreement")
    if shadow.follow_up.needed:
        issues.append("follow_up_proposed")
        if not shadow.follow_up.action or shadow.follow_up.due_in_hours is None:
            issues.append("follow_up_incomplete")
        if not shadow.follow_up.cancel_conditions:
            issues.append("missing_cancel_conditions")
    lowered_reply = shadow.reply.lower()
    if any(key.lower() in lowered_reply for key in _INTERNAL_LEAK_KEYS):
        issues.append("internal_field_leak")
    return issues


def _review_priority(
    snapshot: dict,
    shadow: ReplyShadowDecision | None,
    auto_issues: list[str],
    agreement: bool,
) -> str:
    if shadow is None or any(
        issue in {"handoff_disagreement", "internal_field_leak", "follow_up_incomplete"}
        for issue in auto_issues
    ):
        return "high"
    if any(word in str(snapshot.get("customer_message") or "") for word in _HIGH_RISK_WORDS):
        return "high"
    if not agreement or auto_issues:
        return "medium"
    return "low"


def _get_session():
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ReplyShadowRunModel.__table__,
                ReplyShadowAnnotationModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _latest_annotations(session, run_ids: list[int]) -> dict[int, Any]:
    if not run_ids:
        return {}
    rows = session.scalars(
        select(ReplyShadowAnnotationModel)
        .where(ReplyShadowAnnotationModel.reply_shadow_run_id.in_(run_ids))
        .order_by(
            ReplyShadowAnnotationModel.created_at.desc(),
            ReplyShadowAnnotationModel.id.desc(),
        )
    ).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.reply_shadow_run_id, row)
    return latest


def _run_to_item(row, annotation, *, include_snapshot: bool) -> dict:
    item = {
        "id": row.id,
        "trace_id": row.trace_id,
        "experiment_id": row.experiment_id,
        "candidate_version": row.candidate_version,
        "prompt_version": row.prompt_version,
        "channel": row.channel,
        "user_message": row.user_message,
        "primary": _json_loads(row.primary_json, {}),
        "shadow": _json_loads(row.shadow_json, {}),
        "decision_agreement": row.decision_agreement,
        "auto_issues": _json_loads(row.auto_issues_json, []),
        "review_priority": row.review_priority,
        "status": row.status,
        "error_class": row.error_class,
        "latency_ms": row.latency_ms,
        "latest_annotation": (
            _annotation_to_item(annotation) if annotation is not None else None
        ),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if include_snapshot:
        item["input_snapshot"] = _json_loads(row.input_snapshot_json, {})
    return item


def _annotation_to_item(row) -> dict:
    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "verdict": row.verdict,
        "error_tags": _json_loads(row.error_tags_json, []),
        "note": row.note,
        "annotator_id": row.annotator_id,
        "created_at": row.created_at.isoformat(),
    }


def _sanitize(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if any(sensitive in normalized_key for sensitive in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item, key) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str | None) -> str:
    text = str(value or "")
    text = _MOBILE_PATTERN.sub("[手机号]", text)
    text = _PHONE_PATTERN.sub("[电话]", text)
    return _EMAIL_PATTERN.sub("[邮箱]", text)[:_MAX_SNAPSHOT_TEXT_CHARS]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
