from fastapi import APIRouter, Depends

from app.domains.conversations.schemas.chat import APIResponse
from app.domains.decisioning.services.intent_example_service import add_intent_example
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/intent-examples",
    tags=["intent-examples"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=APIResponse)
async def add_intent_example_api(example: dict) -> APIResponse:
    result = await add_intent_example(example)
    return APIResponse(code=0, message="success", data=result)
