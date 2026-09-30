import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.auth import get_account
from app.core.config import get_settings
from app.domains.access.platform import APP_ID, COOKIE_NAME, platform_request
from app.shared.schemas.common import AppError, ErrorCode

router = APIRouter(prefix="/api/gate", tags=["platform-login"])
FLOW_COOKIE = "platform_login"


def status(account):
    return {"code": 0, "data": {"unlocked": account is not None, "role": account["role"] if account else None, "account": account}}


def safe_target(value: str):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "/workbench"
    path = unquote(parsed.path)
    if (not path.startswith("/") or path.startswith("//") or "\\" in unquote(value)
            or parsed.netloc or parsed.scheme or any(ord(c) < 32 for c in unquote(value))
            or "//" in path or any(part in {".", ".."} for part in path.split("/"))
            or path.lower().rstrip("/") in {"/gate", "/login", "/api"} or path.lower().startswith("/api/")):
        return "/workbench"
    return value


def secure_cookie():
    return get_settings().platform_app_origin.startswith("https://")


def sign(value):
    return hmac.new(get_settings().platform_shared_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


@router.get("")
def gate_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return status(get_account(request))


@router.get("/login")
def login(redirect: str = Query(default="/workbench", max_length=2048)):
    settings = get_settings()
    if len(settings.platform_shared_secret) < 32:
        raise AppError(ErrorCode.INTERNAL_ERROR, "统一平台接入尚未配置，请联系管理员", status_code=503)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    flow = base64.urlsafe_b64encode(json.dumps({"state": state, "verifier": verifier, "target": safe_target(redirect), "expires": time.time() + 300}).encode()).decode()
    response = RedirectResponse(settings.platform_portal_url.rstrip("/") + "/connect?" + urlencode({"app": APP_ID, "state": state, "challenge": challenge}), status_code=302)
    response.set_cookie(FLOW_COOKIE, flow + "." + sign(flow), max_age=300, path="/api/gate", secure=secure_cookie(), httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback")
async def callback(request: Request, code: str = Query(min_length=32, max_length=128), state: str = Query(min_length=1, max_length=128)):
    try:
        raw, signature = request.cookies.get(FLOW_COOKIE, "").split(".")
        if not hmac.compare_digest(sign(raw), signature):
            raise ValueError()
        flow = json.loads(base64.urlsafe_b64decode(raw))
        if flow["expires"] <= time.time() or not secrets.compare_digest(flow["state"], state):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise AppError(ErrorCode.UNAUTHENTICATED, "登录请求已失效，请从工作台重新进入", status_code=401) from None
    result = await platform_request(request, "/service/exchange", {"code": code, "verifier": flow["verifier"]})
    account = result["account"]
    target = safe_target(flow["target"])
    if account["role"] != "admin" and urlsplit(target).path not in account["pages"]:
        target = account["pages"][0] if account["pages"] else "/no-access"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE_NAME, result["token"], max_age=max(0, int(result["expires"] - time.time())), path="/", httponly=True, secure=secure_cookie(), samesite="lax")
    response.delete_cookie(FLOW_COOKIE, path="/api/gate")
    response.delete_cookie("admin_gate", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.delete("")
async def logout(request: Request, response: Response):
    if token := request.cookies.get(COOKIE_NAME):
        await platform_request(request, "/service/logout", {"token": token})
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie("admin_gate", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"code": 0, "data": {"portal_url": get_settings().platform_portal_url.rstrip("/") + "/workspace"}}


@router.get("/resources")
async def resources(request: Request):
    secret = get_settings().platform_shared_secret
    if len(secret) < 32 or request.headers.get("X-App-Id") != APP_ID or not secrets.compare_digest(request.headers.get("X-App-Key", ""), secret):
        raise AppError(ErrorCode.UNAUTHENTICATED, status_code=401)
    from app.domains.conversations.services.conversation_service import list_conversation_tenants
    data = await list_conversation_tenants()
    return {"items": [{"wc_id": item["wc_id"], "display_name": item.get("display_name") or item["wc_id"]} for item in data["items"]]}
