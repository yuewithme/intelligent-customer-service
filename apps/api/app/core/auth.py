import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.domains.access.accounts import Role as GateRole
from app.domains.access.platform import COOKIE_NAME
from app.shared.schemas.common import AppError, ErrorCode

bearer = HTTPBearer(auto_error=False)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_account(request: Request) -> dict | None:
    return getattr(request.state, "account", None)


def get_gate_role(request: Request) -> GateRole | None:
    account = get_account(request)
    return account["role"] if account else None


def _has_api_key(credentials: HTTPAuthorizationCredentials | None) -> bool:
    return bool(credentials and credentials.scheme.lower() == "bearer"
                and get_settings().api_key
                and secrets.compare_digest(credentials.credentials, get_settings().api_key))


async def _authorize(request: Request, credentials, mode: str) -> None:
    account = get_account(request)
    if account:
        from app.domains.access.permissions import authorize_account
        await authorize_account(request, account, mode)
        return
    # A stale browser session must never fall back to a proxy's service credential.
    if "admin_gate" not in request.cookies and COOKIE_NAME not in request.cookies and (
        _has_api_key(credentials) or not get_settings().api_auth_enabled
    ):
        return
    raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)


async def require_api_key(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    await _authorize(request, credentials, "general")


async def require_admin_access(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    await _authorize(request, credentials, "formal")


async def require_gate_access(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    await _authorize(request, credentials, "demo")


def operator_identity(request: Request, supplied: str) -> str:
    account = get_account(request)
    return account["username"] if account else supplied
