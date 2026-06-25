import secrets

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode


bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    settings = get_settings()
    if not settings.api_auth_enabled:
        return
    if credentials is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    if credentials.scheme.lower() != "bearer" or not secrets.compare_digest(
        credentials.credentials, settings.api_key
    ):
        raise AppError(ErrorCode.INVALID_API_KEY, status_code=401)

