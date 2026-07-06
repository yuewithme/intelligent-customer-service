import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode


bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    settings = get_settings()
    if not settings.api_auth_enabled:
        return
    gate_secret = (
        settings.admin_gate_secret
        or settings.admin_gate_password
        or settings.api_key
    )
    gate_cookie = request.cookies.get("admin_gate", "")
    if (
        settings.admin_gate_enabled
        and gate_secret
        and secrets.compare_digest(gate_cookie, gate_secret)
    ):
        return
    if credentials is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    if credentials.scheme.lower() != "bearer" or not secrets.compare_digest(
        credentials.credentials, settings.api_key
    ):
        raise AppError(ErrorCode.INVALID_API_KEY, status_code=401)
