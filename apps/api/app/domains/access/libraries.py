from fastapi import Request

from app.shared.schemas.common import AppError, ErrorCode


LIBRARY_APIS = {
    "product-knowledge": {
        "/api/v1/admin/products": "/operations/products",
        "/api/v1/admin/care-manuals": "/operations/care-manuals",
        "/api/v1/admin/activities": "/knowledge-ops/current-activities",
    },
    "phrase-library": {
        "/api/v1/admin/conversation-cases": "/operations/conversation-cases",
    },
}


def library_page(path: str, library: str | None = None) -> str | None:
    groups = [LIBRARY_APIS[library]] if library in LIBRARY_APIS else LIBRARY_APIS.values()
    return next((page for group in groups for prefix, page in group.items()
                 if path == prefix or path.startswith(prefix + "/")), None)


async def resolve_library(request: Request) -> bool:
    prefix = "/api/library/"
    if not request.url.path.startswith(prefix):
        return False
    from app.domains.access.platform import platform_request

    library, _, tail = request.url.path[len(prefix):].partition("/")
    path = "/api/" + tail
    if library not in LIBRARY_APIS or (path != "/api/gate" and not library_page(path, library)):
        raise AppError(ErrorCode.PERMISSION_DENIED, "资料接口不存在", status_code=403)
    token = request.headers.get("X-Library-Session", "")
    if not 32 <= len(token) <= 128:
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    result = await platform_request(request, "/service/library-session", {"token": token, "library": library})
    actor = result["account"]
    request.state.account = {**actor, "username": actor["id"], "enabled": True,
                             "all_wechat": False, "wechat_ids": []}
    request.state.library_app = library
    request.scope["path"] = path
    request.scope["raw_path"] = path.encode()
    if hasattr(request, "_url"):
        del request._url
    return True
