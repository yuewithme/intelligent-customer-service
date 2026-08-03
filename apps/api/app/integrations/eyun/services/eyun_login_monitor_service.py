import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings


logger = logging.getLogger("wechat_rag_bot.eyun_login_monitor")

EYUN_OFFLINE_NOTIFICATION = "30000"
_status_by_wc_id: dict[str, bool] = {}
_alerted_status_by_wc_id: dict[str, bool] = {}
_wid_by_wc_id: dict[str, str] = {}
_offline_observations_by_wc_id: dict[str, int] = {}
_notification_tasks: set[asyncio.Task[None]] = set()
_state_lock = asyncio.Lock()
_OFFLINE_CONFIRMATIONS_REQUIRED = 2


def schedule_eyun_offline_notification(payload: dict[str, Any]) -> None:
    task = asyncio.create_task(handle_eyun_offline_notification(payload))
    _notification_tasks.add(task)
    task.add_done_callback(_notification_tasks.discard)


async def handle_eyun_offline_notification(payload: dict[str, Any]) -> None:
    del payload
    # Offline callbacks may be duplicated or refer to an expired/stale instance.
    # Re-check the account, but leave offline confirmation to the periodic monitor
    # so repeated callbacks cannot manufacture consecutive offline observations.
    await poll_eyun_login_status(confirm_offline=False)


async def poll_eyun_login_status(*, confirm_offline: bool = True) -> bool | None:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    configured_wc_id = settings.eyun_wc_id.strip()
    if not base_url or not authorization:
        return None

    try:
        result = await _post_eyun(
            f"{base_url}/queryLoginWx",
            authorization=authorization,
            payload={},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun online-list query failed: %s", exc)
        return None

    if str(result.get("code")) != "1000":
        logger.warning("Eyun online-list query returned failure: %s", result)
        return None

    rows = result.get("data") if isinstance(result.get("data"), list) else []
    online_rows = [row for row in rows if isinstance(row, dict)]
    wc_id = configured_wc_id
    if not wc_id and len(online_rows) == 1:
        wc_id = str(online_rows[0].get("wcId") or "").strip()
    if not wc_id:
        logger.warning("Skip Eyun login monitor because EYUN_WC_ID is not configured")
        return None

    matched = next(
        (
            row
            for row in online_rows
            if str(row.get("wcId") or "").strip() == wc_id
        ),
        None,
    )
    if matched is not None:
        w_id = str(matched.get("wId") or "").strip()
        await _apply_status(wc_id=wc_id, w_id=w_id, online=True)
        if w_id and settings.eyun_wid != w_id:
            settings.eyun_wid = w_id
        return True

    reason_known, reason = await _query_offline_reason(
        base_url=base_url,
        authorization=authorization,
        wc_id=wc_id,
    )
    if not reason_known:
        return None
    if reason is None:
        await _apply_status(
            wc_id=wc_id,
            w_id=_wid_by_wc_id.get(wc_id, settings.eyun_wid),
            online=True,
        )
        return True
    if not confirm_offline:
        return False
    await _apply_status(
        wc_id=wc_id,
        w_id=_wid_by_wc_id.get(wc_id, settings.eyun_wid),
        online=False,
        reason=reason,
        require_offline_confirmation=True,
    )
    return False


async def eyun_login_monitor_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await poll_eyun_login_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eyun login monitor tick failed: %s", exc)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.eyun_login_monitor_interval_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def _apply_status(
    *,
    wc_id: str,
    w_id: str,
    online: bool,
    reason: str | None = None,
    require_offline_confirmation: bool = False,
) -> None:
    async with _state_lock:
        if online:
            _offline_observations_by_wc_id.pop(wc_id, None)
        elif require_offline_confirmation:
            observations = _offline_observations_by_wc_id.get(wc_id, 0) + 1
            _offline_observations_by_wc_id[wc_id] = observations
            if observations < _OFFLINE_CONFIRMATIONS_REQUIRED:
                return

        previous = _status_by_wc_id.get(wc_id)
        _status_by_wc_id[wc_id] = online
        if w_id:
            _wid_by_wc_id[wc_id] = w_id
        if previous is None and online:
            _alerted_status_by_wc_id[wc_id] = True
            return
        if _alerted_status_by_wc_id.get(wc_id) is online:
            return

        if online:
            content = _render_recovery_message(wc_id=wc_id, w_id=w_id)
        else:
            content = _render_offline_message(
                wc_id=wc_id,
                w_id=w_id,
                reason=reason,
            )
        if await _send_feishu_alert(content):
            _alerted_status_by_wc_id[wc_id] = online


async def _query_offline_reason(
    *,
    base_url: str,
    authorization: str,
    wc_id: str,
) -> tuple[bool, str | None]:
    try:
        result = await _post_eyun(
            f"{base_url}/offlineReason",
            authorization=authorization,
            payload={"wcId": wc_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun offline-reason query failed: %s", exc)
        return False, None
    if str(result.get("code")) != "1000":
        logger.warning("Eyun offline-reason query returned failure: %s", result)
        return False, None
    rows = result.get("data") if isinstance(result.get("data"), list) else []
    if not rows or not isinstance(rows[0], dict):
        return False, None
    if "reason" not in rows[0]:
        return False, None
    reason = rows[0].get("reason")
    reason_text = str(reason).strip() if reason is not None else ""
    return True, reason_text or None


async def _post_eyun(
    url: str,
    *,
    authorization: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url,
            headers={"Authorization": authorization},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Eyun returned a non-object response")
    return result


async def _send_feishu_alert(content: str) -> bool:
    webhook_url = get_settings().feishu_handoff_webhook_url.strip()
    if not webhook_url:
        logger.warning("Skip Eyun login alert because Feishu webhook is not configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": content}},
            )
        response.raise_for_status()
        result = response.json()
        status_code = result.get("StatusCode", result.get("code"))
        if status_code not in (0, "0"):
            raise RuntimeError(
                str(result.get("StatusMessage") or result.get("msg") or status_code)
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Feishu Eyun login alert failed: %s", exc)
        return False


def _render_offline_message(
    *,
    wc_id: str,
    w_id: str,
    reason: str | None,
) -> str:
    return (
        "【紧急提醒】Eyun 微信账号已离线\n\n"
        f"微信账号：{wc_id}\n"
        f"实例 ID：{w_id or '未知'}\n"
        f"掉线原因：{reason or 'Eyun 暂未返回明确原因'}\n"
        f"发现时间：{_local_time_text()}\n\n"
        "智能客服可能无法收发微信消息，请尽快检查 Eyun 登录状态。"
    )


def _render_recovery_message(*, wc_id: str, w_id: str) -> str:
    return (
        "【恢复通知】Eyun 微信账号已恢复在线\n\n"
        f"微信账号：{wc_id}\n"
        f"当前实例 ID：{w_id or '未知'}\n"
        f"恢复时间：{_local_time_text()}"
    )


def _local_time_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _reset_monitor_state() -> None:
    _status_by_wc_id.clear()
    _alerted_status_by_wc_id.clear()
    _wid_by_wc_id.clear()
    _offline_observations_by_wc_id.clear()
