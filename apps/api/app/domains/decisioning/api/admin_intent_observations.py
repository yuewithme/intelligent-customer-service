import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.domains.conversations.schemas.chat import APIResponse
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.decisioning.schemas.intent_observation import (
    IntentAnnotationItem,
    IntentAnnotationRequest,
    IntentObservationDetail,
    IntentObservationListResponse,
)
from app.domains.decisioning.services.intent_observation_service import (
    build_training_dataset,
    create_intent_annotation,
    get_intent_observation,
    list_intent_observations,
)
from app.domains.decisioning.services.intent_taxonomy_service import load_intent_taxonomy
from app.core.auth import require_api_key


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-intent-observations"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/intent-taxonomy", response_model=APIResponse)
async def intent_taxonomy() -> APIResponse:
    return APIResponse(code=0, message="success", data=load_intent_taxonomy())


@router.get("/intent-observations", response_model=APIResponse)
async def intent_observations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    annotation_status: str | None = None,
    primary_domain: str | None = None,
    primary_goal: str | None = None,
    scope: str | None = None,
    classifier_source: str | None = None,
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    max_confidence: float | None = Query(default=None, ge=0, le=1),
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> APIResponse:
    result = await list_intent_observations(
        page=page,
        page_size=page_size,
        annotation_status=annotation_status,
        primary_domain=primary_domain,
        primary_goal=primary_goal,
        scope=scope,
        classifier_source=classifier_source,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
    )
    data = IntentObservationListResponse.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.get("/intent-training-data", response_model=APIResponse)
async def intent_training_data(
    limit: int | None = Query(default=None, ge=1),
    redact_pii: bool = True,
    start_time: str | None = None,
    end_time: str | None = None,
) -> APIResponse:
    items = await build_training_dataset(
        limit=limit,
        redact_pii=redact_pii,
        start_time=start_time,
        end_time=end_time,
    )
    return APIResponse(
        code=0,
        message="success",
        data={"items": items, "total": len(items), "redact_pii": redact_pii},
    )


@router.get("/intent-training-data/export")
async def export_intent_training_data(
    limit: int | None = Query(default=None, ge=1),
    redact_pii: bool = True,
    start_time: str | None = None,
    end_time: str | None = None,
) -> StreamingResponse:
    items = await build_training_dataset(
        limit=limit,
        redact_pii=redact_pii,
        start_time=start_time,
        end_time=end_time,
    )

    def lines():
        for item in items:
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=intent-training-data.jsonl"
        },
    )


@router.get("/intent-observations/{trace_id}", response_model=APIResponse)
async def intent_observation_detail(trace_id: str) -> APIResponse:
    result = await get_intent_observation(trace_id)
    if result is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="意图观测记录不存在",
            status_code=404,
        )
    data = IntentObservationDetail.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)


@router.post(
    "/intent-observations/{trace_id}/annotations",
    response_model=APIResponse,
)
async def annotate_intent_observation(
    trace_id: str,
    request: IntentAnnotationRequest,
) -> APIResponse:
    result = await create_intent_annotation(trace_id, request)
    data = IntentAnnotationItem.model_validate(result).model_dump()
    return APIResponse(code=0, message="success", data=data)
