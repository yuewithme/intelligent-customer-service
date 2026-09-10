from fastapi import Request
from sqlalchemy import select

from app.core.auth import SAFE_METHODS
from app.domains.conversations.services.conversation_service import _get_session
from app.infrastructure.database.models import ConversationModel, ConversationMessageModel
from app.shared.schemas.common import AppError, ErrorCode

CONVERSATIONS = "/api/v1/admin/conversations"
PAGE_APIS = {
    "/api/v1/admin/conversation-cases": "/operations/conversation-cases",
    "/api/v1/admin/orchestration": "/operations/capability-workbench",
    "/api/v1/admin/tags": "/operations/tags",
    "/api/v1/admin/products": "/operations/products",
    "/api/v1/admin/care-manuals": "/operations/care-manuals",
    "/api/v1/admin/activities": "/knowledge-ops/current-activities",
    "/api/v1/admin/handoff-notification": "/settings/handoff",
    "/api/v1/admin/eyun-settings": "/settings/model-config",
    CONVERSATIONS: "/workbench",
    "/api/v1/users": "/workbench",
    "/api/v1/demo-admin": "/workbench",
}


def deny(message="无权访问此页面或操作"):
    raise AppError(ErrorCode.REQUEST_INVALID, message, status_code=403)


def can_access_conversation(account: dict, conversation_id: str) -> bool:
    if account["role"] == "admin":
        return True
    filters = [ConversationModel.conversation_id == conversation_id, ConversationModel.channel == "wechat"]
    if account["wechat_ids"]:
        filters.append(ConversationModel.owner_wc_id.in_(account["wechat_ids"]))
    with _get_session() as db:
        return db.scalar(select(ConversationModel.id).where(*filters)) is not None


def check_conversation(account: dict, conversation_id: str):
    if not can_access_conversation(account, conversation_id):
        deny("未分配此会话所属微信")


def check_message(account: dict, message_id):
    try:
        message_id = int(message_id)
    except (ValueError, TypeError):
        deny()
    with _get_session() as db:
        conversation_id = db.scalar(select(ConversationMessageModel.conversation_id).where(ConversationMessageModel.id == message_id))
    if not conversation_id:
        deny()
    check_conversation(account, conversation_id)


def check_customer(account: dict, user_id: str):
    with _get_session() as db:
        rows = db.execute(select(ConversationModel.channel, ConversationModel.owner_wc_id).where(ConversationModel.user_id == user_id)).all()
    # Profiles are shared by customer ID, so mixed ownership cannot safely expose a global profile.
    if not rows or any(channel != "wechat" or (account["wechat_ids"] and owner not in account["wechat_ids"]) for channel, owner in rows):
        deny("该客户资料包含未分配微信的数据")


async def authorize_account(request: Request, account: dict, mode: str):
    if account["role"] == "admin":
        return
    path = request.url.path.rstrip("/")
    safe = request.method.upper() in SAFE_METHODS
    page = next((page for prefix, page in PAGE_APIS.items() if path == prefix or path.startswith(prefix + "/")), None)
    workbench_asset = safe and "/workbench" in account["pages"] and path in (
        "/api/v1/admin/tags", "/api/v1/admin/care-manuals", "/api/v1/admin/activities",
    )
    activity_send = account["role"] == "employee" and "/workbench" in account["pages"] and path.startswith("/api/v1/admin/activities/") and path.endswith("/send") and request.method == "POST"
    if not workbench_asset and not activity_send and (page is None or page not in account["pages"]):
        deny()
    if account["role"] == "test":
        if mode == "formal" or (not safe and mode != "demo"):
            deny("测试账号仅可观看已授权页面及操作演示会话")
        return
    if mode == "demo" or path.startswith("/api/v1/admin/eyun-settings"):
        deny()
    if path.startswith(CONVERSATIONS):
        if path in (CONVERSATIONS + "/message-recognition-stats", CONVERSATIONS + "/touch-delivery-stats"):
            deny()
        if request.query_params.get("test_only", "").lower() in ("true", "1", "yes", "on"):
            deny()
        if conversation_id := request.path_params.get("conversation_id"):
            check_conversation(account, conversation_id)
            if path.endswith("/orders"):
                with _get_session() as db:
                    user_id = db.scalar(select(ConversationModel.user_id).where(ConversationModel.conversation_id == conversation_id))
                check_customer(account, user_id)
        if message_id := request.path_params.get("message_id"):
            check_message(account, message_id)
        if not safe and request.headers.get("content-type", "").startswith("application/json"):
            payload = await request.json()
            if isinstance(payload, dict) and payload.get("source_message_id") is not None:
                check_message(account, payload["source_message_id"])
        return
    if path.startswith("/api/v1/users/"):
        check_customer(account, request.path_params["user_id"])
        if not safe:
            payload = await request.json()
            if request.method != "PATCH" or not path.endswith("/profile") or not isinstance(payload, dict) or set(payload) - {"customer_tags", "metadata"}:
                deny("员工仅可修改授权客户的标签")
        return
    if path.startswith("/api/v1/admin/activities/"):
        if path.endswith("/send-logs"):
            deny()
        if path.endswith("/send") and request.method == "POST" and "/workbench" in account["pages"]:
            payload = await request.json()
            check_conversation(account, payload.get("conversation_id", ""))
            return
    if not safe:
        deny("此页面仅授予观看权限，修改需要管理员")
