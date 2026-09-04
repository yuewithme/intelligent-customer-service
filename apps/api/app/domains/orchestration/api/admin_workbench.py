from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.orchestration.services.workbench_service import (
    get_capability_workbench,
    update_capability_workbench_sop_node,
)


class SopNodeHandoffUpdateRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    handoff_enabled: bool


router = APIRouter(
    prefix="/api/v1/admin/orchestration",
    tags=["admin-orchestration"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/workbench", response_model=APIResponse)
async def capability_workbench() -> APIResponse:
    return APIResponse(code=0, message="success", data=get_capability_workbench())


@router.put("/workbench/sop-node-handoff", response_model=APIResponse)
async def update_sop_node_handoff(
    request: SopNodeHandoffUpdateRequest,
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_capability_workbench_sop_node(
            node_id=request.node_id,
            handoff_enabled=request.handoff_enabled,
        ),
    )
