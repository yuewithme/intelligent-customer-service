from fastapi import APIRouter, Depends

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.orchestration.services.workbench_service import (
    get_capability_workbench,
)


router = APIRouter(
    prefix="/api/v1/admin/orchestration",
    tags=["admin-orchestration"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/workbench", response_model=APIResponse)
async def capability_workbench() -> APIResponse:
    return APIResponse(code=0, message="success", data=get_capability_workbench())
