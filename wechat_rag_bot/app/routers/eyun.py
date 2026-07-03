from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from app.services.eyun_callback_service import (
    eyun_success,
    handle_eyun_callback,
    should_process_eyun_payload,
)


router = APIRouter(prefix="/eyun", tags=["eyun"])


@router.post("/callback")
async def receive_eyun_callback(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    payload = await request.json()
    if should_process_eyun_payload(payload):
        background_tasks.add_task(handle_eyun_callback, payload)
    return eyun_success()
