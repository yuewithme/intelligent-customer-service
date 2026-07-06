from hmac import compare_digest

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.config import get_settings


router = APIRouter(prefix="/api/gate", tags=["admin-gate"])
COOKIE_NAME = "admin_gate"


class GateRequest(BaseModel):
    password: str = ""


def _password() -> str:
    settings = get_settings()
    return settings.admin_gate_password or settings.api_key


def _secret() -> str:
    settings = get_settings()
    return settings.admin_gate_secret or _password()


def _unlocked(request: Request) -> bool:
    settings = get_settings()
    if not settings.admin_gate_enabled:
        return True
    return compare_digest(request.cookies.get(COOKIE_NAME, ""), _secret())


@router.get("")
async def gate_status(request: Request) -> dict:
    return {"code": 0, "data": {"unlocked": _unlocked(request)}}


@router.post("")
async def unlock_gate(payload: GateRequest, request: Request, response: Response):
    settings = get_settings()
    expected = _password()
    if settings.admin_gate_enabled and (
        not expected or not compare_digest(payload.password, expected)
    ):
        response.status_code = 401
        return {"code": 401, "message": "访问密码不正确"}

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        COOKIE_NAME,
        _secret(),
        httponly=True,
        max_age=2_592_000,
        path="/",
        samesite="lax",
        secure=forwarded_proto == "https" or request.url.scheme == "https",
    )
    return {"code": 0, "data": {"unlocked": True}}
