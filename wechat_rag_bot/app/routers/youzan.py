from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.services.youzan_callback_service import (
    YouzanCallbackError,
    process_youzan_callback,
)


router = APIRouter(prefix="/youzan", tags=["youzan"])


@router.get("/callback")
async def youzan_callback_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "enabled": bool(
                settings.youzan_callback_enabled
                and settings.youzan_client_id.strip()
                and settings.youzan_client_secret.strip()
            )
        },
    }


@router.post("/callback")
async def receive_youzan_callback(request: Request) -> dict[str, Any]:
    settings = get_settings()
    if not (
        settings.youzan_callback_enabled
        and settings.youzan_client_id.strip()
        and settings.youzan_client_secret.strip()
    ):
        raise HTTPException(status_code=503, detail="Youzan callback is not configured")
    raw_body = await request.body()
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid callback payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid callback payload")
    try:
        return process_youzan_callback(
            payload,
            client_id=settings.youzan_client_id,
            client_secret=settings.youzan_client_secret,
            expected_kdt_id=settings.youzan_kdt_id,
            raw_body=raw_body,
        )
    except YouzanCallbackError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
