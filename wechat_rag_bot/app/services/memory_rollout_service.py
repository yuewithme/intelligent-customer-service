from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import MemoryRolloutGateModel, MemoryShadowRunModel
from app.schemas.memory import MemoryContext
from app.services.memory_identity_service import get_subject_for_identity
from app.services.memory_repository import get_memory_session
from app.services.memory_retrieval_service import retrieve_memory_context


logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _owner_external_id(metadata: dict[str, Any]) -> str:
    for key in ("w_id", "owner_external_id", "wechat_to_user"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def record_memory_rollout_gate(
    *,
    tenant_id: str,
    evaluation_version: str,
    sample_count: int,
    retrieval_success_rate: float,
    retrieval_recall_at_5: float,
    temporal_accuracy: float,
    evidence_grounding: float,
    scope_violations: int,
    verified_business_violations: int,
    approved_by: str,
    approved_at: datetime | None = None,
) -> str:
    settings = get_settings()
    approved_by = approved_by.strip()
    evaluation_version = evaluation_version.strip()
    if not approved_by or not evaluation_version:
        raise ValueError("approved_by and evaluation_version are required")
    metrics = {
        "retrieval_success_rate": retrieval_success_rate,
        "retrieval_recall_at_5": retrieval_recall_at_5,
        "temporal_accuracy": temporal_accuracy,
        "evidence_grounding": evidence_grounding,
    }
    if any(value < 0 or value > 1 for value in metrics.values()):
        raise ValueError("rollout metrics must be between 0 and 1")
    if min(sample_count, scope_violations, verified_business_violations) < 0:
        raise ValueError("rollout counts must not be negative")
    passed = all(
        (
            sample_count >= settings.memory_v2_shadow_min_samples,
            retrieval_success_rate >= 0.99,
            retrieval_recall_at_5 >= 0.90,
            temporal_accuracy >= 0.85,
            evidence_grounding >= 1.0,
            scope_violations == 0,
            verified_business_violations == 0,
        )
    )
    status = "passed" if passed else "failed"
    now = _now()
    with get_memory_session() as session:
        existing = session.scalar(
            select(MemoryRolloutGateModel).where(
                MemoryRolloutGateModel.tenant_id == tenant_id,
                MemoryRolloutGateModel.evaluation_version == evaluation_version,
            )
        )
        if existing is not None:
            return existing.status
        session.add(
            MemoryRolloutGateModel(
                tenant_id=tenant_id,
                evaluation_version=evaluation_version,
                status=status,
                sample_count=sample_count,
                retrieval_success_rate=retrieval_success_rate,
                retrieval_recall_at_5=retrieval_recall_at_5,
                temporal_accuracy=temporal_accuracy,
                evidence_grounding=evidence_grounding,
                scope_violations=scope_violations,
                verified_business_violations=verified_business_violations,
                approved_by=approved_by,
                approved_at=approved_at or now,
                created_at=now,
            )
        )
        session.commit()
    return status


def has_passed_memory_rollout_gate(tenant_id: str) -> bool:
    with get_memory_session() as session:
        latest = session.scalar(
            select(MemoryRolloutGateModel)
            .where(MemoryRolloutGateModel.tenant_id == tenant_id)
            .order_by(
                MemoryRolloutGateModel.approved_at.desc(),
                MemoryRolloutGateModel.id.desc(),
            )
            .limit(1)
        )
        return latest is not None and latest.status == "passed"


def _canary_selected(tenant_id: str, subject_id: str) -> bool:
    settings = get_settings()
    if not settings.memory_v2_canary_enabled:
        return False
    digest = hashlib.sha256(f"{tenant_id}:{subject_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < settings.memory_v2_canary_percent


def _record_shadow_run(
    *,
    tenant_id: str,
    subject_id: str | None,
    trace_id: str,
    query: str,
    status: str,
    injected: bool,
    latency_ms: int,
    context: MemoryContext | None = None,
    scope_violation: bool = False,
    verified_business_violation: bool = False,
    error_code: str | None = None,
) -> None:
    with get_memory_session() as session:
        existing = session.scalar(
            select(MemoryShadowRunModel.id).where(
                MemoryShadowRunModel.tenant_id == tenant_id,
                MemoryShadowRunModel.trace_id == trace_id,
            )
        )
        if existing is not None:
            return
        session.add(
            MemoryShadowRunModel(
                tenant_id=tenant_id,
                subject_id=subject_id,
                trace_id=trace_id,
                query_hash=_query_hash(query),
                status=status,
                injected=injected,
                fact_count=(
                    len(context.current_facts)
                    + len(context.verified_business_facts)
                    + len(context.unresolved_conflicts)
                    if context
                    else 0
                ),
                episode_count=len(context.relevant_episodes) if context else 0,
                evidence_count=len(context.evidence) if context else 0,
                unknown_count=len(context.unknowns) if context else 0,
                latency_ms=max(0, latency_ms),
                scope_violation=scope_violation,
                verified_business_violation=verified_business_violation,
                error_code=error_code,
                created_at=_now(),
            )
        )
        session.commit()


def _record_shadow_run_safely(**kwargs) -> None:
    try:
        _record_shadow_run(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory shadow telemetry write failed: %s", type(exc).__name__)


async def prepare_memory_context_for_request(message) -> tuple[MemoryContext | None, dict]:
    """Run recall and return context for a gated or explicitly bypassed canary."""
    settings = get_settings()
    if not (settings.memory_v2_shadow_enabled or settings.memory_v2_canary_enabled):
        return None, {"status": "disabled", "injected": False}

    started = time.perf_counter()
    owner_external_id = _owner_external_id(message.metadata)
    try:
        subject = get_subject_for_identity(
            tenant_id=message.tenant_id,
            channel=message.channel,
            owner_external_id=owner_external_id,
            external_user_id=message.user_id,
        )
    except Exception as exc:  # noqa: BLE001
        return None, {
            "status": "error",
            "injected": False,
            "error_code": type(exc).__name__[:64],
        }
    if subject is None:
        latency_ms = round((time.perf_counter() - started) * 1000)
        _record_shadow_run_safely(
            tenant_id=message.tenant_id,
            subject_id=None,
            trace_id=message.trace_id,
            query=message.message,
            status="no_subject",
            injected=False,
            latency_ms=latency_ms,
        )
        return None, {"status": "no_subject", "injected": False}

    try:
        context = await retrieve_memory_context(
            tenant_id=message.tenant_id,
            subject_id=subject.id,
            query=message.message,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - started) * 1000)
        _record_shadow_run_safely(
            tenant_id=message.tenant_id,
            subject_id=subject.id,
            trace_id=message.trace_id,
            query=message.message,
            status="error",
            injected=False,
            latency_ms=latency_ms,
            error_code=type(exc).__name__[:64],
        )
        return None, {
            "status": "error",
            "injected": False,
            "error_code": type(exc).__name__[:64],
        }

    scope_violation = (
        context.tenant_id != message.tenant_id or context.subject_id != subject.id
    )
    verified_violation = any(
        fact.source_type != "verified_business_system"
        for fact in context.verified_business_facts
    )
    gate_bypassed = settings.memory_v2_gate_bypass_enabled
    if gate_bypassed:
        gate_passed = True
    else:
        try:
            gate_passed = has_passed_memory_rollout_gate(message.tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory rollout gate read failed: %s", type(exc).__name__)
            gate_passed = False
    injected = (
        not scope_violation
        and not verified_violation
        and gate_passed
        and _canary_selected(message.tenant_id, subject.id)
    )
    latency_ms = round((time.perf_counter() - started) * 1000)
    status = "canary" if injected else "shadow"
    _record_shadow_run_safely(
        tenant_id=message.tenant_id,
        subject_id=subject.id,
        trace_id=message.trace_id,
        query=message.message,
        status=status,
        injected=injected,
        latency_ms=latency_ms,
        context=context,
        scope_violation=scope_violation,
        verified_business_violation=verified_violation,
    )
    trace = {
        "status": status,
        "injected": injected,
        "fact_count": (
            len(context.current_facts)
            + len(context.verified_business_facts)
            + len(context.unresolved_conflicts)
        ),
        "episode_count": len(context.relevant_episodes),
        "evidence_count": len(context.evidence),
        "unknown_count": len(context.unknowns),
        "latency_ms": latency_ms,
        "gate_passed": gate_passed,
        "gate_bypassed": gate_bypassed,
    }
    return (context if injected else None), trace
