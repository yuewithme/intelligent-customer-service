from fastapi import APIRouter, Depends

from app.domains.sales.schemas.admin_tag import (
    TagCategoryCreateRequest,
    TagCategoryUpdateRequest,
    TagCreateRequest,
    TagUpdateRequest,
)
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.admin_tag_service import (
    create_tag,
    create_tag_category,
    delete_tag,
    delete_tag_category,
    list_tag_categories,
    update_tag,
    update_tag_category,
)
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin/tags",
    tags=["admin-tags"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def categories() -> APIResponse:
    return APIResponse(code=0, message="success", data=list_tag_categories())


@router.post("/categories", response_model=APIResponse)
async def create_category(request: TagCategoryCreateRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=create_tag_category(request))


@router.put("/categories/{category_id}", response_model=APIResponse)
async def update_category(
    category_id: str, request: TagCategoryUpdateRequest
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_tag_category(category_id, request),
    )


@router.delete("/categories/{category_id}", response_model=APIResponse)
async def remove_category(category_id: str) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=delete_tag_category(category_id),
    )


@router.post("/categories/{category_id}/items", response_model=APIResponse)
async def add_tag(category_id: str, request: TagCreateRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=create_tag(category_id, request))


@router.put("/items/{tag_id}", response_model=APIResponse)
async def edit_tag(tag_id: int, request: TagUpdateRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=update_tag(tag_id, request))


@router.delete("/items/{tag_id}", response_model=APIResponse)
async def remove_tag(tag_id: int) -> APIResponse:
    return APIResponse(code=0, message="success", data=delete_tag(tag_id))
