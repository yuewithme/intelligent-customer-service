from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.admin_product import ProductNoteRequest, ProductSortRequest
from app.schemas.chat import APIResponse
from app.services.youzan_product_sync_service import (
    list_products,
    sync_youzan_products,
    update_product_note,
    update_product_sort,
)
from app.utils.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/products",
    tags=["admin-products"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def products(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str | None = None,
    status: str | None = None,
    sort_by: str = Query(
        default="manual", pattern="^(manual|title|price|stock|updated_at)$"
    ),
    sort_direction: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_products(
            page=page,
            page_size=page_size,
            keyword=keyword,
            status=status,
            sort_by=sort_by,
            sort_direction=sort_direction,
        ),
    )


@router.post("/sync", response_model=APIResponse)
async def sync_products() -> APIResponse:
    result = await sync_youzan_products(trigger="manual")
    return APIResponse(code=0, message="success", data=result)


@router.put("/{item_id}/note", response_model=APIResponse)
async def product_note(item_id: str, request: ProductNoteRequest) -> APIResponse:
    try:
        result = update_product_note(item_id, request.internal_note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


@router.put("/{item_id}/sort", response_model=APIResponse)
async def sort_product(item_id: str, request: ProductSortRequest) -> APIResponse:
    try:
        result = update_product_sort(item_id, request.sort_order)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)
