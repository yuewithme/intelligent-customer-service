import httpx
from fastapi import APIRouter

from app.config import get_settings
from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.demo import DemoChatRequest
from app.services.demo_sales_agent_service import chat_with_demo_sales_agent
router = APIRouter(
    prefix="/api/v1/demo",
    tags=["demo"],
)


@router.post("/chat", response_model=APIResponse)
async def demo_chat(request: DemoChatRequest) -> APIResponse:
    settings = get_settings()
    upstream = settings.demo_upstream_base_url.strip().rstrip("/")
    if upstream:
        try:
            async with httpx.AsyncClient(
                timeout=settings.demo_upstream_timeout_seconds
            ) as client:
                response = await client.post(
                    f"{upstream}/api/v1/demo/chat",
                    json=request.model_dump(exclude_none=True),
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

    data = await chat_with_demo_sales_agent(
        channel="web_demo",
        customer_id=request.customer_id,
        conversation_id=request.conversation_id,
        message=request.message,
        customer_name=request.customer_name,
    )
    return APIResponse(code=0, message="success", data=data)
