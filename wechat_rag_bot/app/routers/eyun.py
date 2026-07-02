from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/eyun", tags=["eyun"])


@router.post("/callback")
async def receive_eyun_callback(request: Request) -> dict[str, Any]:
    await request.json()
    return {"code": 0, "message": "success", "data": None}
