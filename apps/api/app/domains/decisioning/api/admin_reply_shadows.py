import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.decisioning.schemas.reply_shadow import ReplyShadowAnnotationRequest
from app.domains.decisioning.services.reply_shadow_service import (
    build_reply_shadow_dataset,
    create_reply_shadow_annotation,
    get_reply_shadow_run,
    list_reply_shadow_runs,
)
from app.shared.schemas.common import AppError, ErrorCode


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-reply-shadows"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/reply-shadows", response_model=APIResponse)
async def reply_shadows(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    review_status: str | None = None,
    review_priority: str | None = None,
    keyword: str | None = None,
) -> APIResponse:
    data = await list_reply_shadow_runs(
        page=page,
        page_size=page_size,
        status=status,
        review_status=review_status,
        review_priority=review_priority,
        keyword=keyword,
    )
    return APIResponse(code=0, message="success", data=data)


@router.get("/reply-shadows/export")
async def export_reply_shadow_dataset(
    limit: int | None = Query(default=None, ge=1),
    redact_pii: bool = True,
) -> StreamingResponse:
    items = await build_reply_shadow_dataset(limit=limit, redact_pii=redact_pii)

    def lines():
        for item in items:
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=reply-shadow-dataset.jsonl"
        },
    )


@router.get("/reply-shadows/{trace_id}", response_model=APIResponse)
async def reply_shadow_detail(trace_id: str) -> APIResponse:
    data = await get_reply_shadow_run(trace_id)
    if data is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="影子销售决策记录不存在",
            status_code=404,
        )
    return APIResponse(code=0, message="success", data=data)


@router.post(
    "/reply-shadows/{trace_id}/annotations",
    response_model=APIResponse,
)
async def annotate_reply_shadow(
    trace_id: str,
    request: ReplyShadowAnnotationRequest,
) -> APIResponse:
    try:
        data = await create_reply_shadow_annotation(trace_id, request)
    except LookupError as exc:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="影子销售决策记录不存在",
            status_code=404,
        ) from exc
    return APIResponse(code=0, message="success", data=data)
