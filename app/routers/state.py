from fastapi import APIRouter, Depends

from app.schemas.chat import APIResponse
from app.services.state_service import get_user_state, patch_user_state
from app.utils.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/users",
    tags=["state"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{user_id}/state", response_model=APIResponse)
async def get_state_api(user_id: str) -> APIResponse:
    state = await get_user_state(user_id)
    return APIResponse(code=0, message="success", data=state.model_dump())


@router.patch("/{user_id}/state", response_model=APIResponse)
async def patch_state_api(user_id: str, updates: dict) -> APIResponse:
    state = await patch_user_state(user_id, updates)
    return APIResponse(code=0, message="success", data=state.model_dump())
