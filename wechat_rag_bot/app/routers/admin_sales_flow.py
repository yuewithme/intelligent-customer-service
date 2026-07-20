import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas.chat import APIResponse
from app.services.sales_stage_catalog import get_sales_stage_definitions
from app.services.user_profile_service import (
    adjust_sales_opportunity_stage,
    close_sales_opportunity,
    get_sales_opportunity,
)
from app.talk_script.first_order_sales_seed import ensure_first_order_sales_templates
from app.talk_script.repository import list_sales_templates
from app.utils.auth import require_api_key


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
    ensure_first_order_sales_templates()
    coverage: dict[str, int] = {}
    for row in list_sales_templates():
        coverage[row.sales_stage or "unknown"] = coverage.get(row.sales_stage or "unknown", 0) + 1
    items = []
    for definition in get_sales_stage_definitions():
        item = definition.model_dump(mode="json")
        item["script_coverage"] = coverage.get(definition.stage.value, 0)
        items.append(item)
    return APIResponse(code=0, message="success", data={"items": items})


@router.get("/scripts", response_model=APIResponse)
async def scripts(
    stage: str | None = None,
    action: str | None = None,
    branch: str | None = None,
    status: str | None = Query(default="active"),
) -> APIResponse:
    ensure_first_order_sales_templates()
    rows = list_sales_templates(
        sales_stage=stage,
        sales_action=action,
        branch_code=branch,
        status=status,
    )
    return APIResponse(
        code=0,
        message="success",
        data={"items": [_script_item(row) for row in rows], "total": len(rows)},
    )


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


def _script_item(row) -> dict:
    return {
        "template_id": row.template_id,
        "name": row.template_name,
        "sales_stage": row.sales_stage,
        "sales_action": row.sales_action,
        "branch_code": row.branch_code,
        "answer": row.answer_default,
        "required_conditions": _json_list(row.required_conditions_json),
        "exclude_conditions": _json_list(row.exclude_conditions_json),
        "required_fact_keys": _json_list(row.required_fact_keys_json),
        "variables": _json_list(row.variables_json),
        "priority": row.priority,
        "status": row.status,
        "version": row.version,
    }


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
