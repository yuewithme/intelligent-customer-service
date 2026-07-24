import asyncio
import logging
from uuid import uuid4

from app.core.config import get_settings
from app.domains.customers.services.memory_consolidation_service import apply_memory_candidate
from app.domains.customers.services.memory_event_service import (
    get_memory_event_by_id,
    list_memory_event_context,
)
from app.domains.customers.services.memory_extraction_service import extract_memory_candidates
from app.domains.customers.services.memory_job_service import (
    claim_memory_job,
    complete_memory_job,
    fail_memory_job,
)
from app.domains.customers.services.memory_validation_service import MemoryValidationError
from app.domains.customers.services.memory_vector_service import index_memory_episode


logger = logging.getLogger(__name__)


async def process_memory_job(job, *, use_llm: bool, min_confidence: float) -> int:
    trigger = get_memory_event_by_id(
        tenant_id=job.tenant_id,
        subject_id=job.subject_id,
        event_id=job.trigger_event_id,
    )
    if trigger is None:
        raise ValueError("memory trigger event no longer exists")
    events = list_memory_event_context(
        tenant_id=job.tenant_id,
        subject_id=job.subject_id,
        anchor_event_id=trigger.id,
        limit=20,
    )
    candidates = await extract_memory_candidates(
        events=events,
        trigger_event_id=trigger.id,
        use_llm=use_llm,
    )
    accepted = 0
    for candidate in candidates:
        try:
            result = apply_memory_candidate(
                tenant_id=job.tenant_id,
                subject_id=job.subject_id,
                candidate=candidate,
                min_confidence=min_confidence,
            )
        except MemoryValidationError as exc:
            logger.info(
                "Rejected memory candidate for event %s: %s",
                trigger.event_uid,
                exc,
            )
            continue
        if result.memory_kind == "episode" and result.record_id is not None:
            await index_memory_episode(
                tenant_id=job.tenant_id,
                subject_id=job.subject_id,
                episode_id=result.record_id,
            )
        accepted += int(result.created)
    return accepted


async def memory_worker_tick(worker_id: str) -> bool:
    settings = get_settings()
    job = claim_memory_job(
        worker_id=worker_id,
        lease_seconds=settings.memory_v2_job_lease_seconds,
    )
    if job is None:
        return False
    try:
        await process_memory_job(
            job,
            use_llm=settings.memory_v2_llm_extraction_enabled,
            min_confidence=settings.memory_v2_min_confidence,
        )
    except Exception as exc:  # noqa: BLE001
        fail_memory_job(
            job_id=job.id,
            worker_id=worker_id,
            error=str(exc),
            max_attempts=settings.memory_v2_job_max_attempts,
            retry_base_seconds=settings.memory_v2_job_retry_base_seconds,
        )
        logger.warning("Memory worker job %s failed: %s", job.id, exc)
    else:
        complete_memory_job(job_id=job.id, worker_id=worker_id)
    return True


async def memory_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    worker_id = f"memory-{uuid4()}"
    while not stop_event.is_set():
        try:
            processed = await memory_worker_tick(worker_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Memory worker tick failed: %s", exc)
            processed = False
        if processed:
            continue
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.memory_v2_worker_poll_seconds
            )
        except asyncio.TimeoutError:
            pass
