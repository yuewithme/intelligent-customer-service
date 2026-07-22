from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.domains.sales.api.admin_unpurchased_sop import upload_media as upload_sop_media
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.schemas.unpurchased_sop import (
    UnpurchasedSopStepRequest,
    UnpurchasedSopTestSendRequest,
    UnpurchasedSopUpdateRequest,
)
from app.domains.sales.services.unpurchased_sop_service import (
    SERVICE_SOP_ID,
    create_unpurchased_sop_step,
    delete_unpurchased_sop_step,
    get_unpurchased_sop,
    list_unpurchased_sop_contacts,
    list_unpurchased_sop_deliveries,
    sync_eyun_contacts,
    sync_service_sop_enrollments,
    test_send_unpurchased_sop_step,
    update_unpurchased_sop,
    update_unpurchased_sop_step,
)
from app.integrations.web.services.link_card_metadata_service import enrich_sop_link_cards
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/service-sop",
    tags=["service-sop"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def get_sop() -> APIResponse:
    sync_service_sop_enrollments()
    return APIResponse(
        code=0,
        message="success",
        data=get_unpurchased_sop(sop_id=SERVICE_SOP_ID),
    )


@router.put("", response_model=APIResponse)
async def update_sop(request: UnpurchasedSopUpdateRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_unpurchased_sop(request, sop_id=SERVICE_SOP_ID),
    )


@router.post("/steps", response_model=APIResponse)
async def create_step(request: UnpurchasedSopStepRequest) -> APIResponse:
    await enrich_sop_link_cards(request)
    return APIResponse(
        code=0,
        message="success",
        data=create_unpurchased_sop_step(request, sop_id=SERVICE_SOP_ID),
    )


@router.put("/steps/{step_id}", response_model=APIResponse)
async def update_step(
    step_id: int, request: UnpurchasedSopStepRequest
) -> APIResponse:
    await enrich_sop_link_cards(request)
    return APIResponse(
        code=0,
        message="success",
        data=update_unpurchased_sop_step(
            step_id, request, sop_id=SERVICE_SOP_ID
        ),
    )


@router.delete("/steps/{step_id}", response_model=APIResponse)
async def delete_step(step_id: int) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=delete_unpurchased_sop_step(step_id, sop_id=SERVICE_SOP_ID),
    )


@router.post("/contacts/sync", response_model=APIResponse)
async def sync_contacts() -> APIResponse:
    result = await sync_eyun_contacts()
    result.update(sync_service_sop_enrollments())
    return APIResponse(code=0, message="success", data=result)


@router.get("/contacts", response_model=APIResponse)
async def contacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default="", max_length=256),
) -> APIResponse:
    sync_service_sop_enrollments()
    return APIResponse(
        code=0,
        message="success",
        data=list_unpurchased_sop_contacts(
            page, page_size, keyword, sop_id=SERVICE_SOP_ID
        ),
    )


@router.get("/deliveries", response_model=APIResponse)
async def deliveries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_unpurchased_sop_deliveries(
            page, page_size, sop_id=SERVICE_SOP_ID
        ),
    )


@router.post("/test-send", response_model=APIResponse)
async def test_send(request: UnpurchasedSopTestSendRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=await test_send_unpurchased_sop_step(
            request.step_id,
            contact_ids=request.contact_ids,
            sop_id=SERVICE_SOP_ID,
        ),
    )


@router.post("/media/upload", response_model=APIResponse)
async def upload_media(request: Request, file: UploadFile = File(...)) -> APIResponse:
    return await upload_sop_media(request, file)
