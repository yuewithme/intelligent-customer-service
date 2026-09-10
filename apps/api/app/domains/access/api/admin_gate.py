from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete

from app.core.auth import get_account
from app.domains.access.accounts import LoginSession, SESSION_SECONDS, account_session, login, token_hash

router = APIRouter(prefix="/api/gate", tags=["admin-gate"])
COOKIE_NAME = "admin_gate"


class GateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def status(account: dict | None) -> dict:
    return {"code": 0, "data": {"unlocked": account is not None, "role": account["role"] if account else None, "account": account}}


@router.get("")
def gate_status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return status(get_account(request))


@router.post("")
def unlock_gate(payload: GateRequest, request: Request, response: Response):
    token, account = login(payload.username.strip().lower(), payload.password, request.cookies.get(COOKIE_NAME, ""))
    response.set_cookie(COOKIE_NAME, token, httponly=True, max_age=SESSION_SECONDS, path="/", samesite="lax",
                        secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return status(account)


@router.delete("")
def lock_gate(request: Request, response: Response) -> dict:
    with account_session() as db:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies.get(COOKIE_NAME, ""))))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return status(None)
