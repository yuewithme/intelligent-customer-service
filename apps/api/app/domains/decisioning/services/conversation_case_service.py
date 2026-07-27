from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.decisioning.schemas.reply_shadow import ReplyShadowDecision
from app.infrastructure.database.models import (
    Base,
    ConversationCaseShadowRunModel,
)
from app.integrations.ai.services.llm_service import generate_json


logger = logging.getLogger("wechat_rag_bot.conversation_case")
_CASE_DIR = Path(__file__).resolve().parents[1] / "data" / "intent_labeling_cases"
_sessionmakers: dict[str, sessionmaker] = {}
_tasks: set[asyncio.Task] = set()


def load_conversation_cases() -> list[dict[str, Any]]:
    cases = []
    for path in sorted(_CASE_DIR.glob("*.json"), key=_case_sort_key):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases.append(_normalize_case(payload, path.name))
    return cases


def list_conversation_cases(
    *,
    keyword: str | None = None,
    source_group: str | None = None,
) -> dict[str, Any]:
    cases = load_conversation_cases()
    if keyword:
        normalized = keyword.strip().lower()
        cases = [
            case
            for case in cases
            if normalized in case["case_id"].lower()
            or normalized in case["preview"].lower()
        ]
    if source_group:
        cases = [
            case for case in cases if case["source_group"] == source_group
        ]
    return {
        "items": [_case_summary(case) for case in cases],
        "total": len(cases),
        "source_counts": _source_counts(cases),
    }


def get_conversation_case(case_id: str) -> dict[str, Any] | None:
    return next(
        (case for case in load_conversation_cases() if case["case_id"] == case_id),
        None,
    )


def export_conversation_cases() -> list[dict[str, Any]]:
    return load_conversation_cases()


def start_case_shadow_run(case_id: str) -> dict[str, Any]:
    case = get_conversation_case(case_id)
    if case is None:
        raise LookupError("conversation case not found")
    settings = get_settings()
    now = datetime.now(timezone.utc)
    run_id = uuid4().hex
    with _get_session() as session:
        active = session.scalar(
            select(ConversationCaseShadowRunModel)
            .where(
                ConversationCaseShadowRunModel.case_id == case_id,
                ConversationCaseShadowRunModel.status.in_(("pending", "running")),
            )
            .order_by(ConversationCaseShadowRunModel.created_at.desc())
        )
        if active is not None:
            return _run_to_item(active, include_results=True)
        row = ConversationCaseShadowRunModel(
            run_id=run_id,
            case_id=case_id,
            experiment_id=settings.reply_shadow_experiment_id,
            candidate_version=settings.reply_shadow_candidate_version,
            prompt_version=settings.reply_shadow_prompt_version,
            status="pending",
            total_checkpoints=len(case["checkpoints"]),
            completed_checkpoints=0,
            failed_checkpoints=0,
            result_json=_json_dumps(
                {
                    "case_id": case_id,
                    "mode": "historical_reference_vs_independent_shadow",
                    "turn_results": [],
                }
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
    task = asyncio.create_task(_execute_case_shadow_run(run_id, case))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return get_case_shadow_run(run_id) or {"run_id": run_id, "status": "pending"}


def list_case_shadow_runs(
    *,
    case_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with _get_session() as session:
        query = select(ConversationCaseShadowRunModel)
        if case_id:
            query = query.where(
                ConversationCaseShadowRunModel.case_id == case_id
            )
        rows = session.scalars(
            query.order_by(
                ConversationCaseShadowRunModel.created_at.desc(),
                ConversationCaseShadowRunModel.id.desc(),
            ).limit(max(1, min(limit, 200)))
        ).all()
    return [_run_to_item(row, include_results=False) for row in rows]


def get_case_shadow_run(run_id: str) -> dict[str, Any] | None:
    with _get_session() as session:
        row = session.scalar(
            select(ConversationCaseShadowRunModel).where(
                ConversationCaseShadowRunModel.run_id == run_id
            )
        )
    return _run_to_item(row, include_results=True) if row is not None else None


async def _execute_case_shadow_run(
    run_id: str,
    case: dict[str, Any],
) -> None:
    _update_run(run_id, status="running")
    candidate_history: list[dict[str, str]] = []
    turn_results: list[dict[str, Any]] = []
    failures = 0
    try:
        for checkpoint in case["checkpoints"]:
            result: dict[str, Any] = {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "customer_message": checkpoint["customer_message"],
                "reference_reply": checkpoint["reference_reply"],
                "reference_is_gold": False,
            }
            try:
                raw = await generate_json(
                    _case_shadow_prompt(
                        case=case,
                        checkpoint=checkpoint,
                        candidate_history=candidate_history,
                    ),
                    purpose="reply_shadow",
                    provider_override=get_settings().reply_shadow_llm_provider,
                    model_override=get_settings().reply_shadow_llm_model,
                    shadow=True,
                    prompt_version=(
                        f"conversation_case_{get_settings().reply_shadow_prompt_version}"
                    ),
                )
                decision = ReplyShadowDecision.model_validate(raw)
                result["shadow"] = decision.model_dump(mode="json")
                result["status"] = "success"
                candidate_history.extend(
                    [
                        {
                            "role": "customer",
                            "content": checkpoint["customer_message"],
                        },
                        {"role": "assistant", "content": decision.reply},
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                result["status"] = "failed"
                result["error_class"] = type(exc).__name__
                logger.exception(
                    "conversation case shadow checkpoint failed run_id=%s checkpoint=%s",
                    run_id,
                    checkpoint["checkpoint_id"],
                )
            turn_results.append(result)
            _update_run(
                run_id,
                status="running",
                completed_checkpoints=len(turn_results),
                failed_checkpoints=failures,
                result={
                    "case_id": case["case_id"],
                    "mode": "historical_reference_vs_independent_shadow",
                    "turn_results": turn_results,
                },
            )
        _update_run(
            run_id,
            status="completed" if failures == 0 else "completed_with_errors",
            completed_checkpoints=len(turn_results),
            failed_checkpoints=failures,
            result={
                "case_id": case["case_id"],
                "mode": "historical_reference_vs_independent_shadow",
                "turn_results": turn_results,
            },
            completed=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("conversation case shadow run failed run_id=%s", run_id)
        _update_run(
            run_id,
            status="failed",
            error_class=type(exc).__name__,
            completed=True,
        )


def _normalize_case(payload: dict[str, Any], file_name: str) -> dict[str, Any]:
    case_id = str(payload["case_id"])
    turns = []
    for index, raw_turn in enumerate(payload.get("turns") or [], start=1):
        role = str(raw_turn.get("role") or "")
        if role not in {"customer", "merchant"}:
            raise ValueError(f"{file_name}: unsupported role {role!r}")
        messages = [
            str(message).strip()
            for message in raw_turn.get("messages") or []
            if str(message).strip()
        ]
        if not messages:
            continue
        turns.append(
            {
                "turn_id": f"{case_id}:turn:{index:03d}",
                "role": role,
                "messages": messages,
                "reference_only": role == "merchant",
            }
        )
    checkpoints = _build_checkpoints(case_id, turns)
    source_group = _source_group(case_id)
    quality = str(
        payload.get("content_quality")
        or ("reconstructed_from_summary" if case_id == "case10" else "cleaned_transcript")
    )
    preview = next(
        (
            message
            for turn in turns
            if turn["role"] == "customer"
            for message in turn["messages"]
        ),
        "",
    )
    return {
        "schema_version": "conversation_case.v1",
        "case_id": case_id,
        "source_group": source_group,
        "source_file": str(payload.get("source_file") or file_name),
        "content_quality": quality,
        "preview": preview,
        "turn_count": len(turns),
        "message_count": sum(len(turn["messages"]) for turn in turns),
        "customer_turn_count": sum(
            turn["role"] == "customer" for turn in turns
        ),
        "reference_turn_count": sum(
            turn["role"] == "merchant" for turn in turns
        ),
        "checkpoint_count": len(checkpoints),
        "reference_policy": (
            "Historical merchant replies are comparison references, not gold answers."
        ),
        "turns": turns,
        "checkpoints": checkpoints,
    }


def _build_checkpoints(
    case_id: str,
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checkpoints = []
    for index, turn in enumerate(turns):
        if turn["role"] != "customer":
            continue
        references = []
        for following in turns[index + 1 :]:
            if following["role"] == "customer":
                break
            references.extend(following["messages"])
        checkpoints.append(
            {
                "checkpoint_id": f"{case_id}:checkpoint:{len(checkpoints) + 1:03d}",
                "turn_id": turn["turn_id"],
                "customer_message": "\n".join(turn["messages"]),
                "reference_reply": "\n".join(references),
            }
        )
    return checkpoints


def _case_shadow_prompt(
    *,
    case: dict[str, Any],
    checkpoint: dict[str, Any],
    candidate_history: list[dict[str, str]],
) -> str:
    candidate_input = {
        "case_id": case["case_id"],
        "content_quality": case["content_quality"],
        "candidate_history": candidate_history[-16:],
        "customer_message": checkpoint["customer_message"],
        "business_fact_policy": (
            "This replay provides no verified price, inventory, promotion, order, "
            "logistics, gift, entitlement or service-promise facts. Candidate "
            "history is conversation context, never evidence that such a fact is true."
        ),
    }
    return (
        "你是私域兰花销售系统的离线影子决策器。当前任务是整段历史会话回放。"
        "你只能生成候选判断，不能发送消息、写入客户状态、修改订单、安排人工或执行外部动作。"
        "历史客服回复不会提供给你，也不是标准答案；请只依据候选方案自己的历史和本轮客户消息独立决策。"
        "先回答客户当前问题，不得编造价格、库存、订单、物流、优惠、赠品或服务承诺。"
        "最多追问一个关键问题。明确拒绝、已成交、退款投诉或人工接管时，不得继续普通销售跟进。"
        "只输出一个 JSON 对象，字段必须为：sales_stage, route, sales_action, reply, "
        "need_human, next_action, follow_up, facts_used, confidence, reason。"
        "follow_up 必须包含 needed, action, due_in_hours, cancel_conditions。"
        "route 只能是 template_reply、rag_answer、human、chitchat、unsupported、clarify 之一。"
        "\n输入："
        + json.dumps(candidate_input, ensure_ascii=False, sort_keys=True)
    )


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case[key]
        for key in (
            "case_id",
            "source_group",
            "source_file",
            "content_quality",
            "preview",
            "turn_count",
            "message_count",
            "customer_turn_count",
            "reference_turn_count",
            "checkpoint_count",
        )
    }


def _source_group(case_id: str) -> str:
    if case_id.startswith("case2_"):
        return "case_library_2"
    number = int(case_id.removeprefix("case").split("_", 1)[0])
    return "case_01_10" if number <= 10 else "first_order_cases"


def _source_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["source_group"]] = counts.get(case["source_group"], 0) + 1
    return counts


def _case_sort_key(path: Path) -> tuple[int, int, int]:
    stem = path.stem.removeprefix("case")
    if stem.startswith("2_"):
        parts = stem.split("_")
        return (2, int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    parts = stem.split("_")
    return (1, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _get_session():
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[ConversationCaseShadowRunModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _update_run(
    run_id: str,
    *,
    status: str,
    completed_checkpoints: int | None = None,
    failed_checkpoints: int | None = None,
    result: dict[str, Any] | None = None,
    error_class: str | None = None,
    completed: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    with _get_session() as session:
        row = session.scalar(
            select(ConversationCaseShadowRunModel).where(
                ConversationCaseShadowRunModel.run_id == run_id
            )
        )
        if row is None:
            return
        row.status = status
        if completed_checkpoints is not None:
            row.completed_checkpoints = completed_checkpoints
        if failed_checkpoints is not None:
            row.failed_checkpoints = failed_checkpoints
        if result is not None:
            row.result_json = _json_dumps(result)
        row.error_class = error_class
        row.updated_at = now
        if completed:
            row.completed_at = now
        session.commit()


def _run_to_item(
    row: ConversationCaseShadowRunModel,
    *,
    include_results: bool,
) -> dict[str, Any]:
    item = {
        "run_id": row.run_id,
        "case_id": row.case_id,
        "experiment_id": row.experiment_id,
        "candidate_version": row.candidate_version,
        "prompt_version": row.prompt_version,
        "status": row.status,
        "total_checkpoints": row.total_checkpoints,
        "completed_checkpoints": row.completed_checkpoints,
        "failed_checkpoints": row.failed_checkpoints,
        "error_class": row.error_class,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "completed_at": (
            row.completed_at.isoformat() if row.completed_at is not None else None
        ),
    }
    if include_results:
        item["result"] = _json_loads(row.result_json, {})
    return item


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
