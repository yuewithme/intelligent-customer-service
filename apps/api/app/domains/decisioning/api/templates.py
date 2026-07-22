from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domains.conversations.schemas.chat import APIResponse, ChatRequest
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.template import TemplateItem
from app.domains.conversations.services.channel_service import normalize_chat_request
from app.domains.decisioning.services.template_service import create_template, search_templates
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/templates",
    tags=["templates"],
    dependencies=[Depends(require_api_key)],
)


class TemplateSearchRequest(BaseModel):
    message: str
    intent: str
    stage: str = "unknown"
    customer_tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1)


@router.post("", response_model=APIResponse)
async def create_template_api(template: TemplateItem) -> APIResponse:
    result = await create_template(template)
    return APIResponse(code=0, message="success", data=result)


@router.post("/search", response_model=APIResponse)
async def search_templates_api(request: TemplateSearchRequest) -> APIResponse:
    normalized = await normalize_chat_request(
        ChatRequest(
            channel="debug",
            user_id="debug_user",
            message=request.message,
            kb_id="kb_default",
            metadata={},
        )
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent=request.intent,
        sales_stage=request.stage,
        confidence=1,
        need_template=True,
    )
    state = UserState(
        user_id="debug_user",
        sales_stage=request.stage,
        customer_tags=request.customer_tags,
    )
    templates = await search_templates(normalized, intent, state, request.top_k)
    return APIResponse(code=0, message="success", data={"templates": templates})
