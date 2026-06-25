from fastapi import APIRouter, Depends

from app.schemas.chat import APIResponse, ChatData, ChatRequest
from app.services.rag_service import rag_chat
from app.utils.auth import require_api_key


router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=APIResponse,
    dependencies=[Depends(require_api_key)],
)
async def chat(request: ChatRequest) -> APIResponse:
    result = await rag_chat(**request.model_dump())
    data = ChatData.model_validate(result)
    return APIResponse(code=0, message="success", data=data.model_dump())

