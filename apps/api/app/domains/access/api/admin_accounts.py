import secrets

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.auth import require_account_admin
from app.domains.access.accounts import Account, LoginAttempt, PAGES, Role, account_session, hash_password, public_account, revoke_sessions, token_hash
from app.domains.conversations.services.conversation_service import list_conversation_tenants
from app.shared.schemas.common import AppError, ErrorCode

router = APIRouter(prefix="/api/v1/admin/accounts", tags=["accounts"], dependencies=[Depends(require_account_admin)])


class AccountPermissions(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)
    role: Role = "employee"
    enabled: bool = True
    pages: list[str] = Field(default_factory=list, max_length=20)
    wechat_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("display_name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("姓名不能为空")
        return value.strip()

    @field_validator("pages")
    @classmethod
    def known_pages(cls, value):
        if set(value) - PAGES.keys():
            raise ValueError("未知页面")
        return list(dict.fromkeys(value))


class AccountCreate(AccountPermissions):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str | None = Field(default=None, min_length=12, max_length=256)


class PasswordReset(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=256)


def invalid(message, status=400):
    raise AppError(ErrorCode.REQUEST_INVALID, message, status_code=status)


async def validate_wechat(payload: AccountPermissions):
    known = {item["wc_id"] for item in (await list_conversation_tenants())["items"]}
    if set(payload.wechat_ids) - known:
        invalid("请选择当前已有的客服微信")
    if payload.role != "employee":
        payload.wechat_ids = []


@router.get("")
def list_accounts(response: Response):
    response.headers["Cache-Control"] = "no-store"
    with account_session() as db:
        return {"code": 0, "data": {"items": [public_account(a) for a in db.scalars(select(Account).order_by(Account.id))]}}


@router.get("/options")
async def options():
    return {"code": 0, "data": {"pages": [{"path": path, "title": title} for path, title in PAGES.items()], "wechats": (await list_conversation_tenants())["items"]}}


@router.post("")
async def create_account(payload: AccountCreate, response: Response):
    await validate_wechat(payload)
    password = payload.password or secrets.token_urlsafe(18)
    account = Account(**payload.model_dump(exclude={"password", "username"}), username=payload.username.lower(), password_hash=hash_password(password))
    with account_session() as db:
        db.add(account)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            invalid("账号已存在", 409)
        response.headers["Cache-Control"] = "no-store"
        return {"code": 0, "data": {"account": public_account(account), "password": password}}


@router.put("/{account_id}")
async def update_account(account_id: int, payload: AccountPermissions, actor: dict = Depends(require_account_admin)):
    await validate_wechat(payload)
    if account_id == actor["id"] and (payload.role != "admin" or not payload.enabled):
        invalid("不能停用自己或移除自己的管理员身份")
    with account_session() as db:
        account = db.get(Account, account_id)
        if account is None:
            invalid("账号不存在", 404)
        for key, value in payload.model_dump().items():
            setattr(account, key, value)
        if not payload.enabled:
            revoke_sessions(db, account_id)
        db.commit()
        return {"code": 0, "data": public_account(account)}


@router.post("/{account_id}/password")
def reset_password(account_id: int, payload: PasswordReset, response: Response):
    password = payload.password or secrets.token_urlsafe(18)
    with account_session() as db:
        account = db.get(Account, account_id)
        if account is None:
            invalid("账号不存在", 404)
        account.password_hash = hash_password(password)
        revoke_sessions(db, account_id)
        db.execute(delete(LoginAttempt).where(LoginAttempt.key == token_hash(account.username)))
        db.commit()
        response.headers["Cache-Control"] = "no-store"
        return {"code": 0, "data": {"username": account.username, "password": password}}
