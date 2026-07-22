from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import APIResponse, ChatRequest
from app.domains.conversations.services.channel_service import normalize_chat_request
from app.domains.decisioning.services.intent_example_service import retrieve_intent_examples
from app.domains.decisioning.services.intent_service import classify_intent
from app.domains.conversations.services.state_service import get_user_state
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/debug",
    tags=["debug"],
    dependencies=[Depends(require_api_key)],
)


class DebugIntentRequest(BaseModel):
    user_id: str
    message: str
    session_id: str | None = None
    kb_id: str = "kb_default"


@router.post("/intent", response_model=APIResponse)
async def debug_intent(request: DebugIntentRequest) -> APIResponse:
    chat_request = ChatRequest(
        channel="debug",
        user_id=request.user_id,
        session_id=request.session_id,
        message=request.message,
        kb_id=request.kb_id,
        metadata={},
    )
    message = await normalize_chat_request(chat_request)
    user_state = await get_user_state(message.user_id, message.session_id)
    candidates = await retrieve_intent_examples(
        message.message,
        top_k=get_settings().intent_example_top_k,
    )
    intent = await classify_intent(message, user_state, candidates)
    return APIResponse(
        code=0,
        message="success",
        data={
            "intent": intent.model_dump(),
            "candidates": candidates,
            "user_state": user_state.model_dump(),
        },
    )
