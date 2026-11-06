from fastapi import APIRouter, Depends, HTTPException, Query
import httpx

from app.domains.sales.schemas.care_manual import (
    CareManualBatchLinkRequest,
    CareCourseImportRequest,
    CareManualMatchRequest,
    CareManualUpdateRequest,
    CareManualKnowledgeLinkRequest,
)
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.care_manual_service import (
    add_care_manual_product_links,
    auto_link_care_manual_products,
    get_care_manual,
    list_care_manuals,
    suggest_care_manual_product_links,
    sync_care_manuals,
    test_match_care_manuals,
    update_care_manual,
    link_care_manual_knowledge,
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
    source_type: str | None = Query(default=None, pattern="^(manual|course)$"),
    match_status: str | None = Query(
        default=None, pattern="^(bound|unbound|product_bound|product_unbound)?$"
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
            source_type=source_type,
            match_status=match_status,
        ),
    )


@router.post("/sync/run", response_model=APIResponse)
async def run_sync(source_type: str = Query(default="manual", pattern="^(manual|course)$")) -> APIResponse:
    try:
        result = await sync_care_manuals(trigger="manual", source_type=source_type)
        if source_type == "manual":
            result["product_links"] = auto_link_care_manual_products()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="有赞课程暂不可用，已保留原数据") from exc
    return APIResponse(code=0, message="success", data=result)


@router.post("/courses/import", response_model=APIResponse)
async def import_courses(request: CareCourseImportRequest) -> APIResponse:
    try:
        result = await sync_care_manuals(source_type="course", course_urls=request.urls)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="有赞课程暂不可用，已保留原数据") from exc
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


@router.get("/product-links/suggestions", response_model=APIResponse)
async def product_link_suggestions() -> APIResponse:
    return APIResponse(code=0, message="success", data=suggest_care_manual_product_links())


@router.post("/product-links/batch", response_model=APIResponse)
async def batch_link_products(request: CareManualBatchLinkRequest) -> APIResponse:
    try:
        result = add_care_manual_product_links(
            [link.model_dump() for link in request.links]
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)


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


@router.put("/{card_id}/knowledge", response_model=APIResponse)
async def care_manual_knowledge_link(card_id: int, request: CareManualKnowledgeLinkRequest) -> APIResponse:
    try:
        result = link_care_manual_knowledge(card_id, request.knowledge_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=result)
