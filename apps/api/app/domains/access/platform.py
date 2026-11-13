import asyncio
import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode

COOKIE_NAME = "platform_session"
APP_ID = "orchid-sales-agent"
logger = logging.getLogger("wechat_rag_bot.platform")


async def platform_request(request: Request, path: str, payload: dict) -> dict:
    settings = get_settings()
    if len(settings.platform_shared_secret) < 32:
        raise AppError(ErrorCode.INTERNAL_ERROR, "统一平台接入尚未配置，请联系管理员", status_code=503)
    # Session checks are read-only; exchanging a one-time code must never be replayed.
    attempts = 2 if path in {"/service/session", "/service/library-session"} else 1
    for attempt in range(attempts):
        try:
            response = await request.app.state.platform_client.post(
                settings.platform_api_url.rstrip("/") + path, json=payload,
                headers={"X-App-Id": APP_ID, "X-App-Key": settings.platform_shared_secret},
            )
            if response.status_code in (401, 403):
                raise AppError(ErrorCode.UNAUTHENTICATED if response.status_code == 401 else ErrorCode.PERMISSION_DENIED,
                               "登录已失效或应用权限已被撤销", status_code=response.status_code)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            transient = isinstance(error, httpx.TransportError) or status in {502, 503, 504}
            if transient and attempt + 1 < attempts:
                await asyncio.sleep(0.1)
                continue
            logger.warning(
                "Platform request failed path=%s error=%s status=%s attempts=%s",
                path, type(error).__name__, status, attempt + 1,
            )
            raise AppError(ErrorCode.INTERNAL_ERROR, "统一权限服务暂不可用，请稍后重试", status_code=503) from None


async def resolve_account(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return (await platform_request(request, "/service/session", {"token": token}))["account"]


async def stream_account(request: Request) -> dict | None:
    try:
        return await resolve_account(request)
    except AppError:
        return None


async def platform_identity(request: Request, call_next):
    request.state.account = None
    from app.domains.access.libraries import resolve_library
    try:
        if await resolve_library(request):
            return await call_next(request)
    except AppError as error:
        return JSONResponse({"code": int(error.code), "message": error.message}, status_code=error.status_code)
    if request.url.path.startswith("/api/") and request.url.path not in ("/api/gate/login", "/api/gate/callback", "/api/gate/resources"):
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"} and COOKIE_NAME in request.cookies:
                origin = request.headers.get("origin")
                expected = get_settings().platform_app_origin.rstrip("/")
                if origin and origin != expected:
                    raise AppError(ErrorCode.PERMISSION_DENIED, "请求来源不匹配", status_code=403)
            # Logout must remain available for an expired or revoked session.
            if not (request.url.path == "/api/gate" and request.method == "DELETE"):
                request.state.account = await resolve_account(request)
                if request.state.account and request.state.account.get("read_only") and request.method not in {"GET", "HEAD", "OPTIONS"}:
                    raise AppError(ErrorCode.PERMISSION_DENIED, "测试账号仅可查看，不能回复或修改资料", status_code=403)
        except AppError as error:
            return JSONResponse({"code": int(error.code), "message": error.message}, status_code=error.status_code)
    return await call_next(request)
