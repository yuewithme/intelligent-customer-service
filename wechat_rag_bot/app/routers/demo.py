from fastapi import APIRouter

from app.schemas.chat import APIResponse
from app.schemas.demo import DemoChatRequest
from app.services.demo_sales_agent_service import chat_with_demo_sales_agent
router = APIRouter(
    prefix="/api/v1/demo",
    tags=["demo"],
)


@router.post("/chat", response_model=APIResponse)
async def demo_chat(request: DemoChatRequest) -> APIResponse:
    data = await chat_with_demo_sales_agent(
        channel="web_demo",
        customer_id=request.customer_id,
        conversation_id=request.conversation_id,
        message=request.message,
        customer_name=request.customer_name,
    )
    return APIResponse(code=0, message="success", data=data)
