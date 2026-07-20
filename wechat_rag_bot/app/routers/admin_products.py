from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.admin_product import (
    ProductKnowledgeImportRequest,
    ProductKnowledgePayload,
    ProductNoteRequest,
    ProductSortRequest,
)
from app.schemas.chat import APIResponse
from app.services.youzan_product_sync_service import (
    list_products,
    sync_youzan_products,
    update_product_note,
    update_product_sort,
)
from app.utils.auth import require_api_key
from app.services.product_knowledge_service import (
    create_product_knowledge,
    delete_product_knowledge,
    import_product_knowledge,
    list_product_knowledge,
    list_product_options,
    update_product_knowledge,
)


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
    knowledge_linked: bool | None = None,
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
            knowledge_linked=knowledge_linked,
            sort_by=sort_by,
            sort_direction=sort_direction,
            catalog_only=True,
        ),
    )


@router.post("/sync", response_model=APIResponse)
async def sync_products() -> APIResponse:
    result = await sync_youzan_products(trigger="manual")
    return APIResponse(code=0, message="success", data=result)


@router.get("/options", response_model=APIResponse)
async def product_options() -> APIResponse:
    return APIResponse(code=0, message="success", data=list_product_options())


@router.get("/knowledge", response_model=APIResponse)
async def product_knowledge(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str | None = None,
    linked: bool | None = None,
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_product_knowledge(
            page=page,
            page_size=page_size,
            keyword=keyword,
            linked=linked,
        ),
    )


@router.post("/knowledge", response_model=APIResponse)
async def create_knowledge(request: ProductKnowledgePayload) -> APIResponse:
    try:
        result = create_product_knowledge(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


@router.put("/knowledge/{record_id}", response_model=APIResponse)
async def update_knowledge(
    record_id: int,
    request: ProductKnowledgePayload,
) -> APIResponse:
    try:
        result = update_product_knowledge(record_id, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


@router.delete("/knowledge/{record_id}", response_model=APIResponse)
async def delete_knowledge(record_id: int) -> APIResponse:
    try:
        delete_product_knowledge(record_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data={"deleted": True})


@router.post("/knowledge/import", response_model=APIResponse)
async def import_knowledge(request: ProductKnowledgeImportRequest) -> APIResponse:
    try:
        result = import_product_knowledge(
            [record.model_dump() for record in request.records]
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
