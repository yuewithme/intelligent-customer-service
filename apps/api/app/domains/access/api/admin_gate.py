from hmac import compare_digest

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.auth import create_gate_cookie, get_gate_role


router = APIRouter(prefix="/api/gate", tags=["admin-gate"])
COOKIE_NAME = "admin_gate"


class GateRequest(BaseModel):
    password: str = ""


def _password() -> str:
    settings = get_settings()
    return settings.admin_gate_password or settings.api_key


@router.get("")
async def gate_status(request: Request) -> dict:
    role = get_gate_role(request)
    return {"code": 0, "data": {"unlocked": role is not None, "role": role}}


@router.post("")
async def unlock_gate(payload: GateRequest, request: Request, response: Response):
    settings = get_settings()
    role = None
    if not settings.admin_gate_enabled or compare_digest(payload.password, _password()):
        role = "admin"
    elif settings.admin_gate_test_password and compare_digest(
        payload.password, settings.admin_gate_test_password
    ):
        role = "test"
    if role is None:
        response.status_code = 401
        return {"code": 401, "message": "访问密码不正确"}

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        COOKIE_NAME,
        create_gate_cookie(role),
        httponly=True,
        max_age=2_592_000,
        path="/",
        samesite="lax",
        secure=forwarded_proto == "https" or request.url.scheme == "https",
    )
    return {"code": 0, "data": {"unlocked": True, "role": role}}


@router.delete("")
async def lock_gate(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"code": 0, "data": {"unlocked": False, "role": None}}
