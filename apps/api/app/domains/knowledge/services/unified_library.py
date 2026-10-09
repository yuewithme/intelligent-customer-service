import httpx
from functools import wraps
from inspect import signature

from fastapi.encoders import jsonable_encoder
from app.shared.schemas.common import AppError, ErrorCode

from app.core.config import get_settings


def enabled():
    return get_settings().unified_library_enabled


def call(operation: str, **arguments):
    settings = get_settings()
    root = settings.platform_portal_url.rstrip("/").removesuffix("/portal")
    try:
        response = httpx.post(root + "/review/api/library/operations/" + operation,
            json={"arguments": jsonable_encoder(arguments)},
            headers={"X-App-Id": "orchid-sales-agent", "X-App-Key": settings.platform_shared_secret}, timeout=30)
        if operation.startswith("activities.") and response.status_code in {400, 404, 409, 422}:
            raise AppError(ErrorCode.REQUEST_INVALID,
                response.json().get("message", "活动操作无效"), status_code=response.status_code)
        if response.status_code == 404:
            raise LookupError(response.json().get("message", "资料不存在"))
        if response.status_code in {400, 409, 422}:
            raise ValueError(response.json().get("message", "资料字段无效"))
        response.raise_for_status()
        return response.json()["data"]
    except (httpx.HTTPError, KeyError) as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, "统一资料库暂不可用", status_code=503) from exc


def remote(operation):
    def decorate(function):
        @wraps(function)
        def dispatch(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            bound = signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            values = dict(bound.arguments)
            if operation == "catalog._persist_sync":
                values["detail_results"] = {key: value if isinstance(value, dict) else None for key, value in values["detail_results"].items()}
            if isinstance(values.get("error"), Exception):
                values["error"] = type(values["error"]).__name__
            return call(operation, **values)
        return dispatch
    return decorate


async def search_library(query: str, category: str = "all") -> dict:
    settings = get_settings()
    if len(settings.platform_shared_secret) < 32:
        return {"items": [], "unavailable": True}
    root = settings.platform_portal_url.rstrip("/").removesuffix("/portal")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            result = await client.get(root + "/review/api/library/resources", params={"query": query, "category": category},
                                      headers={"X-App-Id": "orchid-sales-agent", "X-App-Key": settings.platform_shared_secret})
            result.raise_for_status()
            return result.json()
    except (httpx.HTTPError, ValueError):
        return {"items": [], "unavailable": True}
