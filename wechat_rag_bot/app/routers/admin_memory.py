from fastapi import APIRouter, Depends

from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.memory import MemoryFactCorrectionRequest, MemorySubjectPurgeRequest
from app.services.memory_lifecycle_service import (
    correct_memory_fact,
    purge_memory_subject,
)
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
