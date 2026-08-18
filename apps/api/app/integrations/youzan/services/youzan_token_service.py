from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.feishu.services.webhook_alert_service import (
    send_feishu_webhook_alert,
)
from app.integrations.youzan.client import YouzanClient, YouzanError
from app.integrations.youzan.services.youzan_credential_service import (
    effective_youzan_credentials,
)


class YouzanTokenConfigurationError(RuntimeError):
    """Raised when the service cannot obtain a usable Youzan access token."""


class YouzanTokenManager:
    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._token = settings.youzan_access_token.strip()
        self._expires_at: datetime | None = None
        self._http_client = http_client
        self._lock = asyncio.Lock()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and not self._expiring_soon():
            return self._token
        async with self._lock:
            if not force_refresh and self._token and not self._expiring_soon():
                return self._token
            try:
                await self._refresh()
            except Exception as exc:
                await notify_youzan_failure("Token自动换取", exc)
                raise
            await notify_youzan_recovery("Token自动换取成功")
            return self._token

    def invalidate(self, access_token: str) -> None:
        if access_token and access_token == self._token:
            self._expires_at = datetime.now(timezone.utc)

    def _expiring_soon(self) -> bool:
        if self._expires_at is None:
            return False
        skew = timedelta(seconds=get_settings().youzan_token_refresh_skew_seconds)
        return datetime.now(timezone.utc) + skew >= self._expires_at

    async def _refresh(self) -> None:
        settings = get_settings()
        credentials = effective_youzan_credentials()
        if credentials is None:
            raise YouzanTokenConfigurationError(
                "有赞Token已失效，但未配置CLIENT_ID、CLIENT_SECRET或KDT_ID"
            )
        url = f"{settings.youzan_base_url.rstrip('/')}/auth/token"
        payload = {
            "authorize_type": "silent",
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "grant_id": credentials.kdt_id,
        }
        try:
            if self._http_client is not None:
                response = await self._http_client.post(url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(url, json=payload)
        except httpx.RequestError as exc:
            raise YouzanTokenConfigurationError("有赞Token接口请求失败") from exc
        if not 200 <= response.status_code < 300:
            raise YouzanTokenConfigurationError(
                f"有赞Token接口返回HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise YouzanTokenConfigurationError("有赞Token接口返回无效JSON") from exc
        token_data = _token_data(body)
        token = str(token_data.get("access_token") or "").strip()
        if not token:
            message, code = _token_error(body)
            raise YouzanError(
                message or "有赞Token接口未返回access_token",
                code=code,
                method="auth/token",
            )
        self._token = token
        self._expires_at = _token_expiry(token_data)


_manager: YouzanTokenManager | None = None
_alert_lock = asyncio.Lock()
_outage_alerted = False


def get_youzan_token_manager() -> YouzanTokenManager:
    global _manager
    if _manager is None:
        _manager = YouzanTokenManager()
    return _manager


def reset_youzan_token_manager() -> None:
    global _manager
    _manager = None


def create_managed_youzan_client(*, timeout: float = 15) -> YouzanClient:
    settings = get_settings()
    return YouzanClient(
        access_token=settings.youzan_access_token,
        base_url=settings.youzan_base_url,
        timeout=timeout,
        token_provider=get_youzan_token_manager(),
    )


def youzan_credentials_available() -> bool:
    settings = get_settings()
    credentials = effective_youzan_credentials()
    return bool(
        settings.youzan_access_token.strip()
        or credentials is not None
    )


async def notify_youzan_failure(component: str, exc: Exception) -> None:
    global _outage_alerted
    async with _alert_lock:
        if _outage_alerted:
            return
        code = str(getattr(exc, "code", "") or "")
        detail = str(exc).strip()[:200] or type(exc).__name__
        content = (
            "【紧急提醒】有赞订单能力不可用\n\n"
            f"故障环节：{component}\n"
            f"错误类型：{type(exc).__name__}\n"
            f"错误信息：{detail}\n"
            f"错误码：{code or '无'}\n"
            f"发现时间：{_local_time_text()}\n\n"
            "订单查询可能无法返回实时结果，请检查有赞授权和同步状态。"
        )
        if await send_feishu_webhook_alert(content):
            _outage_alerted = True


async def notify_youzan_recovery(component: str) -> None:
    global _outage_alerted
    async with _alert_lock:
        if not _outage_alerted:
            return
        content = (
            "【恢复通知】有赞订单能力已恢复\n\n"
            f"恢复环节：{component}\n"
            f"恢复时间：{_local_time_text()}"
        )
        if await send_feishu_webhook_alert(content):
            _outage_alerted = False


def reset_youzan_token_state_for_tests() -> None:
    global _outage_alerted
    reset_youzan_token_manager()
    _outage_alerted = False


def _token_data(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        return data
    return body


def _token_error(body: Any) -> tuple[str, str]:
    if not isinstance(body, dict):
        return "", ""
    for key in ("gw_err_resp", "error_response"):
        error = body.get(key)
        if isinstance(error, dict):
            return (
                str(error.get("err_msg") or error.get("msg") or ""),
                str(error.get("err_code") or error.get("code") or ""),
            )
    return (
        str(body.get("message") or body.get("msg") or ""),
        str(body.get("code") or ""),
    )


def _token_expiry(data: dict[str, Any]) -> datetime:
    now = datetime.now(timezone.utc)
    raw = data.get("expires") or data.get("expires_in") or data.get("expire")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return now + timedelta(days=7)
    if value > 10_000_000_000:
        value /= 1000
    if value > now.timestamp() + 300:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return now + timedelta(seconds=max(value, 300))


def _local_time_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
