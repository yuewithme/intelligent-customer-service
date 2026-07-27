import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.decisioning.services.conversation_case_service import (
    export_conversation_cases,
    get_case_shadow_run,
    get_conversation_case,
    list_case_shadow_runs,
    list_conversation_cases,
    start_case_shadow_run,
)
from app.shared.schemas.common import AppError, ErrorCode


router = APIRouter(
    prefix="/api/v1/admin/conversation-cases",
    tags=["admin-conversation-cases"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def conversation_cases(
    keyword: str | None = None,
    source_group: str | None = None,
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_conversation_cases(
            keyword=keyword,
            source_group=source_group,
        ),
    )


@router.get("/export")
async def export_case_library() -> StreamingResponse:
    items = export_conversation_cases()

    def lines():
        for item in items:
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=conversation-case-library.jsonl"
            )
        },
    )


@router.get("/runs", response_model=APIResponse)
async def case_shadow_runs(
    case_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_case_shadow_runs(case_id=case_id, limit=limit),
    )


@router.get("/runs/{run_id}", response_model=APIResponse)
async def case_shadow_run_detail(run_id: str) -> APIResponse:
    data = get_case_shadow_run(run_id)
    if data is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="整案影子回放记录不存在",
            status_code=404,
        )
    return APIResponse(code=0, message="success", data=data)


@router.post("/{case_id}/runs", response_model=APIResponse)
async def run_conversation_case(case_id: str) -> APIResponse:
    try:
        data = start_case_shadow_run(case_id)
    except LookupError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="整案案例不存在",
            status_code=404,
        ) from exc
    return APIResponse(code=0, message="success", data=data)


@router.get("/{case_id}", response_model=APIResponse)
async def conversation_case_detail(case_id: str) -> APIResponse:
    data = get_conversation_case(case_id)
    if data is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="整案案例不存在",
            status_code=404,
        )
    return APIResponse(code=0, message="success", data=data)
