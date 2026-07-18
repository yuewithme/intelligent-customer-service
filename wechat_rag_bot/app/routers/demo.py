import httpx
from fastapi import APIRouter

from app.config import get_settings
from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.demo import DemoChatRequest, DemoOpeningRequest
from app.services.demo_sales_agent_service import (
    chat_with_demo_sales_agent,
    open_demo_sales_conversation,
)

router = APIRouter(
    prefix="/api/v1/demo",
    tags=["demo"],
)


async def _proxy_demo_request(path: str, payload: dict) -> APIResponse | None:
    settings = get_settings()
    upstream = settings.demo_upstream_base_url.strip().rstrip("/")
    if not upstream:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=settings.demo_upstream_timeout_seconds
        ) as client:
            response = await client.post(
                f"{upstream}/api/v1/demo/{path}",
                json=payload,
            )
        body = response.json()
    except Exception as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "正式销售服务暂时不可用",
            status_code=502,
        ) from exc
    if response.status_code >= 400:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            str(body.get("message") or "正式销售服务请求失败"),
            status_code=response.status_code,
        )
    return APIResponse.model_validate(body)


@router.post("/opening", response_model=APIResponse)
async def demo_opening(request: DemoOpeningRequest) -> APIResponse:
    proxied = await _proxy_demo_request(
        "opening", request.model_dump(exclude_none=True)
    )
    if proxied is not None:
        return proxied

    data = await open_demo_sales_conversation(
        channel="web_demo",
        customer_id=request.customer_id,
        conversation_id=request.conversation_id,
        customer_name=request.customer_name,
    )
    return APIResponse(code=0, message="success", data=data)


@router.post("/chat", response_model=APIResponse)
async def demo_chat(request: DemoChatRequest) -> APIResponse:
    proxied = await _proxy_demo_request(
        "chat", request.model_dump(exclude_none=True)
    )
    if proxied is not None:
        return proxied

    data = await chat_with_demo_sales_agent(
        channel="web_demo",
        customer_id=request.customer_id,
        conversation_id=request.conversation_id,
        message=request.message,
        customer_name=request.customer_name,
    )
    return APIResponse(code=0, message="success", data=data)
