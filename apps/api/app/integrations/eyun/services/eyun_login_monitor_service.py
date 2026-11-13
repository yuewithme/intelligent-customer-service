import asyncio
import json
import logging
import re
from time import monotonic
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.eyun.services.eyun_account_settings_service import (
    get_eyun_account_settings_revision,
    refresh_eyun_account_wid,
)
from app.integrations.feishu.services.webhook_alert_service import (
    render_system_alert,
    send_feishu_webhook_alert,
)


logger = logging.getLogger("wechat_rag_bot.eyun_login_monitor")

EYUN_OFFLINE_NOTIFICATION = "30000"
_status_by_wc_id: dict[str, bool] = {}
_alerted_status_by_wc_id: dict[str, bool] = {}
_wid_by_wc_id: dict[str, str] = {}
_offline_observations_by_wc_id: dict[str, int] = {}
_last_reconnect_by_wc_id: dict[str, float] = {}
_notification_tasks: set[asyncio.Task[None]] = set()
_state_lock = asyncio.Lock()
_OFFLINE_CONFIRMATIONS_REQUIRED = 2
_MAX_REASON_LENGTH = 240


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
    configured_w_id = settings.eyun_wid
    configuration_revision = get_eyun_account_settings_revision()
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

    # An admin may change the account while the provider request is in flight.
    if (
        get_eyun_account_settings_revision() != configuration_revision
        or (settings.eyun_wc_id.strip(), settings.eyun_wid)
        != (configured_wc_id, configured_w_id)
    ):
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
        if w_id and settings.eyun_wid != w_id:
            refresh_eyun_account_wid(w_id=w_id, wc_id=wc_id)
        await _apply_status(
            wc_id=wc_id,
            w_id=w_id,
            online=True,
            configuration_revision=configuration_revision,
        )
        return True

    reason_known, reason = await _query_offline_reason(
        base_url=base_url,
        authorization=authorization,
        wc_id=wc_id,
    )
    if (
        get_eyun_account_settings_revision() != configuration_revision
        or (settings.eyun_wc_id.strip(), settings.eyun_wid)
        != (configured_wc_id, configured_w_id)
    ):
        return None
    if not reason_known:
        return None
    if reason is None:
        await _apply_status(
            wc_id=wc_id,
            w_id=_wid_by_wc_id.get(wc_id, settings.eyun_wid),
            online=True,
            configuration_revision=configuration_revision,
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
        configuration_revision=configuration_revision,
    )
    await _reconnect_if_offline(
        base_url=base_url,
        authorization=authorization,
        wc_id=wc_id,
        configuration_revision=configuration_revision,
    )
    return False


async def _reconnect_if_offline(
    *, base_url: str, authorization: str, wc_id: str, configuration_revision: int
) -> None:
    settings = get_settings()
    async with _state_lock:
        if (
            not settings.eyun_auto_reconnect_enabled
            or get_eyun_account_settings_revision() != configuration_revision
            or _status_by_wc_id.get(wc_id) is not False
            or _offline_observations_by_wc_id.get(wc_id, 0) < _OFFLINE_CONFIRMATIONS_REQUIRED
        ):
            return
        now = monotonic()
        last_attempt = _last_reconnect_by_wc_id.get(wc_id)
        if last_attempt is not None and now - last_attempt < settings.eyun_reconnect_interval_seconds:
            return
        # Reserve before I/O: failures and ambiguous timeouts must also respect cooldown.
        _last_reconnect_by_wc_id[wc_id] = now
    try:
        result = await _post_eyun(
            f"{base_url}/reconnectWx",
            authorization=authorization,
            payload={"wcId": wc_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun auto-reconnect failed for %s: %s", wc_id, exc)
        return
    if str(result.get("code")) != "1000":
        logger.warning("Eyun auto-reconnect rejected for %s: code=%s", wc_id, result.get("code"))
        return
    # Reconnect acceptance alone does not prove recovery; the next poll verifies it.
    logger.info("Eyun auto-reconnect accepted for %s; awaiting online confirmation", wc_id)


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
    configuration_revision: int | None = None,
) -> None:
    async with _state_lock:
        if (
            configuration_revision is not None
            and get_eyun_account_settings_revision() != configuration_revision
        ):
            return
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
        alert_sent = await _send_feishu_alert(content)
        if (
            alert_sent
            and (
                configuration_revision is None
                or get_eyun_account_settings_revision() == configuration_revision
            )
        ):
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
    reason_text = _humanize_offline_reason(reason)
    return True, reason_text or None


def _humanize_offline_reason(reason: Any) -> str:
    extracted = _extract_reason_text(reason)
    if extracted:
        return extracted[:_MAX_REASON_LENGTH]
    if isinstance(reason, (dict, list)):
        fallback = json.dumps(reason, ensure_ascii=False, separators=(",", ":"))
    else:
        fallback = str(reason or "")
    fallback = " ".join(fallback.split())
    if len(fallback) > _MAX_REASON_LENGTH:
        fallback = f"{fallback[: _MAX_REASON_LENGTH - 1]}…"
    return fallback


def _extract_reason_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[:1] in "{[":
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                extracted = _extract_reason_text(parsed)
                if extracted:
                    return extracted
        content_match = re.search(
            r"<Content>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</Content>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if content_match:
            text = (content_match.group(1) or content_match.group(2) or "").strip()
        text = " ".join(re.sub(r"<[^>]+>", " ", text).split())
        return "" if text.lower() in {"success", "ok"} else text
    if isinstance(value, dict):
        for key in ("reason", "errMsg", "error", "errorMessage", "err_msg", "errMessage"):
            if key in value:
                extracted = _extract_reason_text(value[key])
                if extracted:
                    return extracted
        for key in ("data", "baseResponse"):
            if key in value:
                extracted = _extract_reason_text(value[key])
                if extracted:
                    return extracted
        for key in ("string", "content", "message", "msg"):
            if key in value:
                extracted = _extract_reason_text(value[key])
                if extracted:
                    return extracted
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                extracted = _extract_reason_text(nested)
                if extracted:
                    return extracted
    if isinstance(value, list):
        for item in value:
            extracted = _extract_reason_text(item)
            if extracted:
                return extracted
    return ""


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
    return await send_feishu_webhook_alert(content)


def _render_offline_message(
    *,
    wc_id: str,
    w_id: str,
    reason: str | None,
) -> str:
    return render_system_alert(
        "微信账号已离线",
        impact="该微信账号的消息收发可能受影响。",
        status=(
            "已连续两次检测到离线，系统会尝试自动重连。"
            if get_settings().eyun_auto_reconnect_enabled
            else "已连续两次检测到离线，自动重连未开启。"
        ),
        details=(
            f"微信账号：{wc_id}",
            f"实例 ID：{w_id or '未知'}",
            f"掉线原因：{reason or 'Eyun 暂未返回明确原因'}",
        ),
        action=_offline_action(reason),
    )


def _offline_action(reason: str | None) -> str:
    if reason and "重新登录" in reason:
        return "请在 Eyun 控制台重新登录该微信；重登时继续使用上方 WCID，成功后的 WID 会自动同步。"
    return "请在 Eyun 控制台检查该微信的登录状态，必要时使用上方 WCID 重新登录。"


def _render_recovery_message(*, wc_id: str, w_id: str) -> str:
    return render_system_alert(
        "微信账号已恢复在线",
        impact="该微信账号的离线状态已解除。",
        status="已重新查询并确认在线；消息发送结果仍以实际投递为准。",
        details=(f"微信账号：{wc_id}", f"当前实例 ID：{w_id or '未知'}"),
        action="无需重新登录。",
        recovered=True,
    )


def reset_eyun_login_monitor_state() -> None:
    _status_by_wc_id.clear()
    _alerted_status_by_wc_id.clear()
    _wid_by_wc_id.clear()
    _offline_observations_by_wc_id.clear()
    _last_reconnect_by_wc_id.clear()


def _reset_monitor_state() -> None:
    reset_eyun_login_monitor_state()
