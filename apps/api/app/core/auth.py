import hashlib
import hmac
import secrets
from typing import Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode


bearer = HTTPBearer(auto_error=False)
GateRole = Literal["admin", "test"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _gate_secret() -> str:
    settings = get_settings()
    return settings.admin_gate_secret or settings.admin_gate_password or settings.api_key


def create_gate_cookie(role: GateRole) -> str:
    signature = hmac.new(
        _gate_secret().encode("utf-8"),
        role.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{role}.{signature}"


def get_gate_role(request: Request) -> GateRole | None:
    settings = get_settings()
    if not settings.admin_gate_enabled:
        return "admin"
    cookie = request.cookies.get("admin_gate", "")
    secret = _gate_secret()
    if cookie and secret and secrets.compare_digest(cookie, secret):
        return "admin"
    for role in ("admin", "test"):
        if secrets.compare_digest(cookie, create_gate_cookie(role)):
            return role
    return None


def _has_api_key(credentials: HTTPAuthorizationCredentials | None) -> bool:
    settings = get_settings()
    return bool(
        credentials is not None
        and credentials.scheme.lower() == "bearer"
        and secrets.compare_digest(credentials.credentials, settings.api_key)
    )


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    settings = get_settings()
    if not settings.api_auth_enabled:
        return
    role = get_gate_role(request)
    if role == "admin":
        return
    if role == "test":
        if request.method.upper() in SAFE_METHODS:
            return
        raise AppError(ErrorCode.REQUEST_INVALID, "测试身份仅支持只读访问", status_code=403)
    if _has_api_key(credentials):
        return
    if credentials is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    raise AppError(ErrorCode.INVALID_API_KEY, status_code=401)


async def require_admin_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if not get_settings().api_auth_enabled:
        return
    role = get_gate_role(request)
    if role == "test":
        raise AppError(ErrorCode.REQUEST_INVALID, "测试身份无权访问正式会话", status_code=403)
    if role == "admin" or _has_api_key(credentials):
        return
    raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)


async def require_gate_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if not get_settings().api_auth_enabled:
        return
    if get_gate_role(request) or _has_api_key(credentials):
        return
    raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
