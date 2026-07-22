from fastapi import APIRouter

from app.schemas.chat import APIResponse
from app.schemas.demo import DemoChatRequest, DemoOpeningRequest
from app.services.demo_sales_agent_service import (
    chat_with_demo_sales_agent,
    open_demo_sales_conversation,
)

router = APIRouter(
    prefix="/api/v1/demo",
    tags=["demo"],
)

@router.post("/opening", response_model=APIResponse)
async def demo_opening(request: DemoOpeningRequest) -> APIResponse:
    data = await open_demo_sales_conversation(
        channel="web_demo",
        customer_id=request.customer_id,
        conversation_id=request.conversation_id,
        customer_name=request.customer_name,
    )
    return APIResponse(code=0, message="success", data=data)


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
