import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.decisioning.services.conversation_case_service import (
    export_conversation_cases,
    get_conversation_case,
    list_conversation_cases,
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
    library_type: str = Query(
        default="complete",
        pattern="^(complete|cleaned)$",
    ),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_conversation_cases(
            keyword=keyword,
            library_type=library_type,
        ),
    )


@router.get("/export")
async def export_case_library(
    library_type: str = Query(
        default="complete",
        pattern="^(complete|cleaned)$",
    ),
) -> StreamingResponse:
    items = export_conversation_cases(library_type)

    def lines():
        for item in items:
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename={library_type}-conversation-case-library.jsonl"
            )
        },
    )


@router.get("/{case_id}", response_model=APIResponse)
async def conversation_case_detail(
    case_id: str,
    library_type: str = Query(
        default="complete",
        pattern="^(complete|cleaned)$",
    ),
) -> APIResponse:
    data = get_conversation_case(case_id, library_type)
    if data is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="整案案例不存在",
            status_code=404,
        )
    return APIResponse(code=0, message="success", data=data)
