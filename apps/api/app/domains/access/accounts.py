import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from typing import Literal

from sqlalchemy import Boolean, Float, Integer, JSON, String, create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base
from app.shared.schemas.common import AppError, ErrorCode

Role = Literal["admin", "test", "employee"]
PAGES = {
    "/workbench": "小兰工作台",
    "/operations/conversation-cases": "销售案例库",
    "/operations/capability-workbench": "能力工作台",
    "/operations/tags": "客户标签",
    "/operations/products": "产品信息",
    "/operations/care-manuals": "养护手册",
    "/knowledge-ops/current-activities": "销售活动",
    "/settings/handoff": "转人工设置",
    "/settings/model-config": "模型配置",
}
SESSION_SECONDS = 12 * 60 * 60


class Account(Base):
    __tablename__ = "admin_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pages: Mapped[list] = mapped_column(JSON, default=list)
    wechat_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginSession(Base):
    __tablename__ = "admin_login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class LoginAttempt(Base):
    __tablename__ = "admin_login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[float] = mapped_column(Float)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=5).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=5).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@lru_cache
def _factory(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=[Account.__table__, LoginSession.__table__, LoginAttempt.__table__])
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        if db.scalar(select(Account.id).limit(1)) is None:
            settings = get_settings()
            password = settings.admin_gate_password or settings.api_key
            if password and password != "change_me":
                db.add(Account(username="admin", display_name="管理员", password_hash=hash_password(password), role="admin", pages=list(PAGES), wechat_ids=[]))
                if settings.admin_gate_test_password:
                    db.add(Account(username="test", display_name="测试账号", password_hash=hash_password(settings.admin_gate_test_password), role="test", pages=list(PAGES), wechat_ids=[]))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
    return factory


def account_session():
    return _factory(get_settings().database_url)()


def public_account(account: Account) -> dict:
    return {"id": account.id, "username": account.username, "display_name": account.display_name,
            "role": account.role, "enabled": account.enabled,
            "pages": list(PAGES) if account.role == "admin" else account.pages,
            "wechat_ids": account.wechat_ids}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_account(token: str) -> dict | None:
    if not token or len(token) > 128:
        return None
    with account_session() as db:
        login = db.get(LoginSession, token_hash(token))
        if login is None or login.expires_at <= time.time():
            return None
        account = db.get(Account, login.account_id)
        return public_account(account) if account and account.enabled else None


def revoke_sessions(db, account_id: int):
    db.execute(delete(LoginSession).where(LoginSession.account_id == account_id))


def login(username: str, password: str, previous_token: str = "") -> tuple[str, dict]:
    with account_session() as db:
        now = time.time()
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
        db.execute(delete(LoginAttempt).where(LoginAttempt.expires_at <= now))
        key = token_hash(username)
        attempt = db.get(LoginAttempt, key)
        if attempt and attempt.failures >= 10:
            raise AppError(ErrorCode.REQUEST_INVALID, "登录尝试过多，请 15 分钟后重试", status_code=429)
        account = db.scalar(select(Account).where(Account.username == username))
        # Unknown usernames still perform the password derivation.
        valid = verify_password(password, account.password_hash if account else "scrypt$invalid$invalid")
        if not valid or account is None or not account.enabled:
            if attempt is None:
                attempt = LoginAttempt(key=key, failures=0, expires_at=now + 900)
                db.add(attempt)
            attempt.failures += 1
            db.commit()
            raise AppError(ErrorCode.UNAUTHENTICATED, "账号或密码不正确，或账号已停用", status_code=401)
        if attempt:
            db.delete(attempt)
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(previous_token)))
        token = secrets.token_urlsafe(32)
        db.add(LoginSession(token_hash=token_hash(token), account_id=account.id, expires_at=now + SESSION_SECONDS))
        db.commit()
        return token, public_account(account)
