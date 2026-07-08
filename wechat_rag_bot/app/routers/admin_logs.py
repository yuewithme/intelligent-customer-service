from fastapi import APIRouter, Depends, Query

from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.log import (
    ChatLogDetail,
    ChatLogListResponse,
    ChatLogStats,
    RagDebugSearchRequest,
    RagDebugSearchResponse,
    TalkScriptMatchLogListResponse,
    TalkScriptMatchStats,
)
from app.services.chat_log_service import (
    get_chat_log,
    get_chat_log_stats,
    list_chat_logs,
)
from app.services.rag_debug_service import debug_rag_search
from app.talk_script.repository import get_match_log_stats, list_match_logs
from app.utils.auth import require_api_key


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


@router.get("/talk-script-match-logs", response_model=APIResponse)
async def talk_script_match_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    customer_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    scene_id: str | None = None,
    template_id: str | None = None,
    need_human: bool | None = None,
) -> APIResponse:
    result = list_match_logs(
        page=page,
        page_size=page_size,
        customer_id=customer_id,
        session_id=session_id,
        trace_id=trace_id,
        status=status,
        scene_id=scene_id,
        template_id=template_id,
        need_human=need_human,
    )
    data = TalkScriptMatchLogListResponse.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.get("/talk-script-match-stats", response_model=APIResponse)
async def talk_script_match_stats(
    customer_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    scene_id: str | None = None,
    template_id: str | None = None,
    need_human: bool | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    low_confidence_threshold: float = Query(default=0.5, ge=0, le=1),
    low_confidence_limit: int = Query(default=20, ge=0, le=100),
) -> APIResponse:
    result = get_match_log_stats(
        customer_id=customer_id,
        session_id=session_id,
        trace_id=trace_id,
        status=status,
        scene_id=scene_id,
        template_id=template_id,
        need_human=need_human,
        start_time=start_time,
        end_time=end_time,
        low_confidence_threshold=low_confidence_threshold,
        low_confidence_limit=low_confidence_limit,
    )
    data = TalkScriptMatchStats.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.post("/rag-debug/search", response_model=APIResponse)
async def rag_debug_search(request: RagDebugSearchRequest) -> APIResponse:
    result = await debug_rag_search(request)
    data = RagDebugSearchResponse.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)
