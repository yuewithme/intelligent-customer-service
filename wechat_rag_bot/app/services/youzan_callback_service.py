from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.parse import unquote

from app.services.youzan_identity_store import YouzanIdentityStore
from app.services.youzan_order_service import YouzanCustomerIdentity


class YouzanCallbackError(ValueError):
    pass


def verify_youzan_signature(
    payload: dict[str, Any],
    *,
    client_id: str,
    client_secret: str,
) -> bool:
    received_client_id = str(payload.get("client_id") or "")
    msg = str(payload.get("msg") or "")
    signature = str(payload.get("sign") or "").lower()
    if not client_id or not client_secret or received_client_id != client_id:
        return False
    expected = hashlib.md5(  # noqa: S324 - provider protocol requires MD5
        f"{client_id}{msg}{client_secret}".encode("utf-8")
    ).hexdigest()
    return bool(signature) and hmac.compare_digest(signature, expected)


def process_youzan_callback(
    payload: dict[str, Any],
    *,
    client_id: str,
    client_secret: str,
    expected_kdt_id: str = "",
    raw_body: bytes = b"",
    store: YouzanIdentityStore | None = None,
) -> dict[str, Any]:
    if not verify_youzan_signature(
        payload,
        client_id=client_id,
        client_secret=client_secret,
    ):
        raise YouzanCallbackError("invalid callback signature")
    kdt_id = str(payload.get("kdt_id") or "")
    if expected_kdt_id and kdt_id != expected_kdt_id:
        raise YouzanCallbackError("unexpected store")

    event_data = decode_youzan_message(str(payload.get("msg") or ""))
    digest_source = raw_body or json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(digest_source).hexdigest()
    msg_id = str(payload.get("msg_id") or "") or _fallback_msg_id(payload, digest)
    event_type = str(payload.get("type") or "")
    event_status = str(payload.get("status") or "")

    store = store or YouzanIdentityStore()
    inserted = store.record_event(
        msg_id=msg_id,
        kdt_id=kdt_id,
        event_type=event_type,
        event_status=event_status,
        payload_digest=digest,
    )
    refreshed = 0
    if inserted:
        identity = _identity_from_event(event_data)
        refreshed = store.refresh_matching(identity, kdt_id=kdt_id)
    return {
        "code": 0,
        "message": "success",
        "data": {"duplicate": not inserted, "bindings_refreshed": refreshed},
    }


def decode_youzan_message(value: str) -> dict[str, Any]:
    if not value:
        return {}
    decoded = unquote(value)
    try:
        outer = json.loads(decoded)
    except (TypeError, ValueError):
        return {}
    if not isinstance(outer, dict):
        return {}
    data = outer.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            data = {}
    if isinstance(data, dict):
        return {**outer, **data}
    return outer


def _identity_from_event(data: dict[str, Any]) -> YouzanCustomerIdentity:
    mobile = _nested_text(data, "mobile")
    yz_uid = _nested_text(data, "yz_uid")
    return YouzanCustomerIdentity(
        yz_uid=yz_uid,
        buyer_id=_nested_text(data, "buyer_id") or yz_uid,
        yz_open_id=_nested_text(data, "yz_open_id", "yz_openid"),
        fans_id=_nested_text(data, "fans_id"),
        weixin_openid=_nested_text(data, "weixin_openid", "openid", "open_id"),
        union_id=_nested_text(data, "union_id", "unionid"),
        mobile_masked=(f"{mobile[:3]}****{mobile[-4:]}" if len(mobile) == 11 else ""),
    )


def _nested_text(data: dict[str, Any], *keys: str) -> str:
    queue: list[tuple[dict[str, Any], int]] = [(data, 0)]
    while queue:
        current, depth = queue.pop(0)
        for key in keys:
            value = current.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        if depth >= 3:
            continue
        queue.extend(
            (value, depth + 1)
            for value in current.values()
            if isinstance(value, dict)
        )
    return ""


def _fallback_msg_id(payload: dict[str, Any], digest: str) -> str:
    parts = (
        str(payload.get("type") or ""),
        str(payload.get("status") or ""),
        str(payload.get("id") or ""),
        str(payload.get("version") or ""),
        digest,
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
