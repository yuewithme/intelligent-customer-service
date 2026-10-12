from fastapi import APIRouter, Depends
from typing import Literal
from pydantic import BaseModel, Field

from app.core.auth import require_api_key
from app.domains.orchestration.schemas.sop_flow import SopFlowDefinition, SopScope
from app.domains.orchestration.schemas.canvas_workflow import CreateCanvas, SaveCanvas, PublishCanvas
from app.domains.orchestration.services import canvas_workflow_service as canvases
from app.domains.orchestration.services.sop_flow_service import save_flow
from app.domains.handoff.services.handoff_notification_service import update_sop_enabled
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.orchestration.services.workbench_service import (
    get_capability_workbench,
    update_capability_workbench_sop_node,
)


class SopNodeHandoffUpdateRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    handoff_enabled: bool


class SopEnabledUpdateRequest(BaseModel):
    sop_scope: Literal["first_order", "service", "seeding"]
    enabled: bool


router = APIRouter(
    prefix="/api/v1/admin/orchestration",
    tags=["admin-orchestration"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/workbench", response_model=APIResponse)
async def capability_workbench() -> APIResponse:
    return APIResponse(code=0, message="success", data=get_capability_workbench())


@router.get("/workbench/canvas/workflows", response_model=APIResponse)
async def canvas_list() -> APIResponse:
    return APIResponse(code=0, message="success", data=canvases.list_canvases())


@router.post("/workbench/canvas/workflows", response_model=APIResponse)
async def canvas_create(request: CreateCanvas) -> APIResponse:
    return APIResponse(code=0, message="草稿已创建", data=canvases.create_canvas(request.name))


@router.get("/workbench/canvas/workflows/{workflow_id}", response_model=APIResponse)
async def canvas_detail(workflow_id: str) -> APIResponse:
    return APIResponse(code=0, message="success", data=canvases.get_canvas(workflow_id))


@router.put("/workbench/canvas/workflows/{workflow_id}", response_model=APIResponse)
async def canvas_save(workflow_id: str, request: SaveCanvas) -> APIResponse:
    return APIResponse(code=0, message="草稿已保存", data=canvases.save_canvas(workflow_id, request.revision, request.definition))


@router.post("/workbench/canvas/workflows/{workflow_id}/publish", response_model=APIResponse)
async def canvas_publish(workflow_id: str, request: PublishCanvas) -> APIResponse:
    result = await canvases.publish_canvas(workflow_id, request.revision)
    return APIResponse(code=0, message="流程理解已发布" if result["published"] else "请修改流程后重新发布", data=result)


@router.put("/workbench/sop-enabled", response_model=APIResponse)
async def update_sop_switch(request: SopEnabledUpdateRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=update_sop_enabled(request.sop_scope, request.enabled))


@router.put("/workbench/flows/{scope}", response_model=APIResponse)
async def update_flow(scope: SopScope, request: SopFlowDefinition) -> APIResponse:
    saved = save_flow(scope, request)
    return APIResponse(code=0, message="流程已保存并生效", data={"revision": saved.revision})


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
