from fastapi import APIRouter, Depends

from app.domains.conversations.schemas.chat import APIResponse, ChatData, ChatRequest
from app.domains.conversations.services.chat_orchestrator import handle_chat
from app.core.auth import require_api_key


router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=APIResponse,
    dependencies=[Depends(require_api_key)],
)
async def chat(request: ChatRequest) -> APIResponse:
    result = await handle_chat(request)
    data = ChatData.model_validate(result)
    payload = data.model_dump(exclude_none=False)
    if "metadata" not in result:
        payload.pop("metadata", None)
    if "handoff" not in result:
        payload.pop("handoff", None)
    return APIResponse(
        code=0,
        message="success",
        data=payload,
    )
