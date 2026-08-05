from fastapi import APIRouter, Depends, Query

from app.domains.conversations.schemas.chat import APIResponse
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.schemas.log import (
    ChatLogDetail,
    ChatLogListResponse,
    ChatLogStats,
    RagDebugSearchRequest,
    RagDebugSearchResponse,
)
from app.domains.conversations.services.chat_log_service import (
    get_chat_log,
    get_chat_log_stats,
    list_chat_logs,
)
from app.domains.knowledge.services.rag_debug_service import debug_rag_search
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-logs"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/chat-logs", response_model=APIResponse)
async def chat_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id: str | None = None,
    session_id: str | None = None,
    route: str | None = None,
    primary_intent: str | None = None,
    template_id: str | None = None,
    status: str | None = None,
    need_human: bool | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> APIResponse:
    result = await list_chat_logs(
        page=page,
        page_size=page_size,
        user_id=user_id,
        session_id=session_id,
        route=route,
        primary_intent=primary_intent,
        template_id=template_id,
        status=status,
        need_human=need_human,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
    )
    data = ChatLogListResponse.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.get("/chat-logs/{trace_id}", response_model=APIResponse)
async def chat_log_detail(trace_id: str) -> APIResponse:
    result = await get_chat_log(trace_id)
    if result is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="日志不存在",
            status_code=404,
            data=None,
        )
    data = ChatLogDetail.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.get("/chat-log-stats", response_model=APIResponse)
async def chat_log_stats(
    start_time: str | None = None,
    end_time: str | None = None,
) -> APIResponse:
    result = await get_chat_log_stats(start_time=start_time, end_time=end_time)
    data = ChatLogStats.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.post("/rag-debug/search", response_model=APIResponse)
async def rag_debug_search(request: RagDebugSearchRequest) -> APIResponse:
    result = await debug_rag_search(request)
    data = RagDebugSearchResponse.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)
