from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.sales_stage_catalog import get_sales_stage_definitions
from app.domains.customers.services.user_profile_service import (
    adjust_sales_opportunity_stage,
    close_sales_opportunity,
    get_sales_opportunity,
)
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/admin/sales-flow",
    tags=["admin-sales-flow"],
    dependencies=[Depends(require_api_key)],
)


class StageAdjustmentRequest(BaseModel):
    stage: str
    reason: str = Field(min_length=1, max_length=500)
    operator_id: str = Field(min_length=1, max_length=128)


class OpportunityCloseRequest(BaseModel):
    status: str
    reason: str = Field(min_length=1, max_length=500)
    operator_id: str = Field(min_length=1, max_length=128)


@router.get("/stages", response_model=APIResponse)
async def stages() -> APIResponse:
    items = [
        definition.model_dump(mode="json")
        for definition in get_sales_stage_definitions()
    ]
    return APIResponse(code=0, message="success", data={"items": items})


@router.get("/opportunities/{user_id}", response_model=APIResponse)
async def opportunity(user_id: str) -> APIResponse:
    return APIResponse(code=0, message="success", data=await get_sales_opportunity(user_id))


@router.patch("/opportunities/{user_id}/stage", response_model=APIResponse)
async def adjust_stage(user_id: str, request: StageAdjustmentRequest) -> APIResponse:
    try:
        data = await adjust_sales_opportunity_stage(
            user_id,
            stage=request.stage,
            reason=request.reason,
            operator_id=request.operator_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=data)


@router.post("/opportunities/{user_id}/close", response_model=APIResponse)
async def close_opportunity(user_id: str, request: OpportunityCloseRequest) -> APIResponse:
    try:
        data = await close_sales_opportunity(
            user_id,
            status=request.status,
            reason=request.reason,
            operator_id=request.operator_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=data)
