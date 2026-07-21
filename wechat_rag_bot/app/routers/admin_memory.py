from fastapi import APIRouter, Depends, Query

from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.memory import (
    MemoryFactCorrectionRequest,
    MemoryIndexRebuildRequest,
    MemoryProcedureCandidateRequest,
    MemoryProcedureReviewRequest,
    MemoryRolloutGateRequest,
    MemorySubjectPurgeRequest,
)
from app.services.memory_lifecycle_service import (
    correct_memory_fact,
    purge_memory_subject,
)
from app.services.memory_procedure_service import (
    get_memory_operations_status,
    list_memory_procedure_candidates,
    review_memory_procedure_candidate,
    submit_memory_procedure_candidate,
)
from app.services.memory_rollout_service import record_memory_rollout_gate
from app.services.memory_vector_service import rebuild_memory_subject_index
from app.utils.auth import require_admin_access


router = APIRouter(
    prefix="/api/v1/memory/subjects",
    tags=["memory-admin"],
    dependencies=[Depends(require_admin_access)],
)


@router.post(
    "/{subject_id}/facts/{fact_id}/corrections", response_model=APIResponse
)
async def correct_fact_api(
    subject_id: str,
    fact_id: int,
    request: MemoryFactCorrectionRequest,
) -> APIResponse:
    try:
        result = correct_memory_fact(
            tenant_id=request.tenant_id,
            subject_id=subject_id,
            target_fact_id=fact_id,
            fact_value=request.fact_value,
            valid_from=request.valid_from,
            request_id=request.request_id,
            reason_code=request.reason_code,
            operator_id=request.operator_id,
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID, message=str(exc), status_code=400
        ) from exc
    return APIResponse(code=0, message="success", data=result)


@router.post("/{subject_id}/purge", response_model=APIResponse)
async def purge_subject_api(
    subject_id: str, request: MemorySubjectPurgeRequest
) -> APIResponse:
    try:
        result = await purge_memory_subject(
            tenant_id=request.tenant_id,
            subject_id=subject_id,
            confirm_subject_id=request.confirm_subject_id,
            request_id=request.request_id,
            requested_by=request.requested_by,
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID, message=str(exc), status_code=400
        ) from exc
    return APIResponse(code=0, message="success", data=result)


@router.post("/{subject_id}/rebuild-index", response_model=APIResponse)
async def rebuild_subject_index_api(
    subject_id: str, request: MemoryIndexRebuildRequest
) -> APIResponse:
    indexed = await rebuild_memory_subject_index(
        tenant_id=request.tenant_id, subject_id=subject_id
    )
    return APIResponse(
        code=0,
        message="success",
        data={"tenant_id": request.tenant_id, "subject_id": subject_id, "indexed": indexed},
    )


@router.get("/operations/status", response_model=APIResponse)
async def memory_operations_status_api(
    tenant_id: str = Query(min_length=1, max_length=128),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=get_memory_operations_status(tenant_id),
    )


@router.post("/rollout-gates", response_model=APIResponse)
async def create_rollout_gate_api(
    request: MemoryRolloutGateRequest,
) -> APIResponse:
    status = record_memory_rollout_gate(**request.model_dump())
    return APIResponse(
        code=0,
        message="success",
        data={
            "tenant_id": request.tenant_id,
            "evaluation_version": request.evaluation_version,
            "status": status,
        },
    )


@router.post("/procedures", response_model=APIResponse)
async def submit_procedure_api(
    request: MemoryProcedureCandidateRequest,
) -> APIResponse:
    try:
        result = submit_memory_procedure_candidate(**request.model_dump())
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID, message=str(exc), status_code=400
        ) from exc
    return APIResponse(code=0, message="success", data=result)


@router.get("/procedures", response_model=APIResponse)
async def list_procedures_api(
    tenant_id: str = Query(min_length=1, max_length=128),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    try:
        items = list_memory_procedure_candidates(
            tenant_id=tenant_id, status=status, limit=limit
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID, message=str(exc), status_code=400
        ) from exc
    return APIResponse(code=0, message="success", data={"items": items})


@router.post("/procedures/{candidate_id}/review", response_model=APIResponse)
async def review_procedure_api(
    candidate_id: int, request: MemoryProcedureReviewRequest
) -> APIResponse:
    try:
        result = review_memory_procedure_candidate(
            candidate_id=candidate_id, **request.model_dump()
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID, message=str(exc), status_code=400
        ) from exc
    return APIResponse(code=0, message="success", data=result)
