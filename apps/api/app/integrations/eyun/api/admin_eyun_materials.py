from fastapi import APIRouter, Depends, Query

from app.domains.conversations.schemas.chat import APIResponse
from app.integrations.eyun.schemas.eyun_material import (
    BulkSendRequest,
    MaterialFromMessageRequest,
    MaterialUpdateRequest,
)
from app.integrations.eyun.services.eyun_material_service import (
    create_material_from_conversation_message,
    list_bulk_jobs,
    list_materials,
    update_material,
)
from app.integrations.eyun.services.message_risk_control_service import enqueue_wechat_bulk_send
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/wechat-materials",
    tags=["admin-wechat-materials"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def materials(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    keyword: str = "",
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_materials(page=page, page_size=page_size, status=status, keyword=keyword),
    )


@router.post("/from-message", response_model=APIResponse)
async def from_message(request: MaterialFromMessageRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=create_material_from_conversation_message(
            request.conversation_message_id, request.name
        ),
    )


@router.put("/{material_id}", response_model=APIResponse)
async def patch_material(
    material_id: int, request: MaterialUpdateRequest
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_material(material_id, name=request.name, status=request.status),
    )


@router.post("/bulk-send", response_model=APIResponse)
async def bulk_send(request: BulkSendRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=await enqueue_wechat_bulk_send(
            recipients=[recipient.model_dump() for recipient in request.recipients],
            items=[item.model_dump() for item in request.items],
            source_type=request.source_type,
            source_id=request.source_id,
            created_by=request.operator_id,
        ),
    )


@router.get("/bulk-jobs", response_model=APIResponse)
async def bulk_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_bulk_jobs(page=page, page_size=page_size),
    )
