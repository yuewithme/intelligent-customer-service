from fastapi import APIRouter, Depends, HTTPException, Query

from app.domains.sales.schemas.care_manual import CareManualMatchRequest, CareManualUpdateRequest
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.care_manual_service import (
    get_care_manual,
    list_care_manuals,
    sync_care_manuals,
    test_match_care_manuals,
    update_care_manual,
)
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/care-manuals",
    tags=["admin-care-manuals"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def care_manuals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str | None = None,
    enabled: bool | None = None,
    match_status: str | None = Query(
        default=None, pattern="^(bound|unbound)?$"
    ),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_care_manuals(
            page=page,
            page_size=page_size,
            keyword=keyword,
            enabled=enabled,
            match_status=match_status,
        ),
    )


@router.post("/sync/run", response_model=APIResponse)
async def run_sync() -> APIResponse:
    try:
        result = await sync_care_manuals(trigger="manual")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


@router.post("/match/test", response_model=APIResponse)
async def test_match(request: CareManualMatchRequest) -> APIResponse:
    if not request.query and not request.product_name and not request.youzan_item_id:
        raise HTTPException(status_code=422, detail="请至少输入一种匹配条件")
    return APIResponse(
        code=0,
        message="success",
        data=test_match_care_manuals(**request.model_dump()),
    )


@router.get("/{card_id}", response_model=APIResponse)
async def care_manual(card_id: int) -> APIResponse:
    try:
        result = get_care_manual(card_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


@router.put("/{card_id}", response_model=APIResponse)
async def edit_care_manual(
    card_id: int, request: CareManualUpdateRequest
) -> APIResponse:
    try:
        result = update_care_manual(card_id, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)
