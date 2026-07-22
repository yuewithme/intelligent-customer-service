import argparse
import asyncio
import json
import os
from typing import Any

import httpx


class EyunConfigError(RuntimeError):
    pass


def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EyunConfigError(f"Missing environment variable: {name}")
    return value


def _base_url() -> str:
    return _require_env("EYUN_BASE_URL").rstrip("/")


def _authorization(required: bool = True) -> str:
    value = os.getenv("EYUN_AUTHORIZATION", "").strip()
    if required and not value:
        raise EyunConfigError(
            "Missing EYUN_AUTHORIZATION. Run login first, or copy it from Eyun console."
        )
    return value


def _default_wid() -> str:
    return _require_env("EYUN_WID")


def _headers(include_auth: bool = True) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    authorization = _authorization(required=include_auth)
    if authorization:
        headers["Authorization"] = authorization
    return headers


async def _post(path: str, payload: dict[str, Any], include_auth: bool = True) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{_base_url()}{path}",
            headers=_headers(include_auth=include_auth),
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def _print_response(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


async def login(_: argparse.Namespace) -> None:
    data = await _post(
        "/member/login",
        {
            "account": _require_env("EYUN_ACCOUNT"),
            "password": _require_env("EYUN_PASSWORD"),
        },
        include_auth=False,
    )
    _print_response(data)
    token = data.get("data", {}).get("Authorization")
    if token:
        print("\nSet this before the next step:")
        print(f"EYUN_AUTHORIZATION={token}")


async def qrcode(args: argparse.Namespace) -> None:
    data = await _post(
        "/iPadLogin",
        {
            "wcId": args.wc_id,
            "deviceType": args.device_type,
        },
    )
    _print_response(data)
    qr_url = data.get("data", {}).get("qrCodeUrl")
    w_id = data.get("data", {}).get("wId")
    if qr_url:
        print(f"\nOpen this QR code URL and scan it with WeChat:\n{qr_url}")
    if w_id:
        print(f"\nAfter scanning, confirm login with:\npython -m app.scripts.eyun_smoke_test confirm --wid {w_id}")


async def confirm(args: argparse.Namespace) -> None:
    wid = args.wid or _default_wid()
    data = await _post(
        "/getIPadLoginInfo",
        {
            "wId": wid,
            "autoCheck": args.auto_check,
        },
    )
    _print_response(data)
    wc_id = data.get("data", {}).get("wcId")
    if wc_id:
        print(f"\nSave this wcId for future reconnects: {wc_id}")


async def send_text(args: argparse.Namespace) -> None:
    from app.integrations.eyun.services.message_risk_control_service import (
        enqueue_wechat_outbound,
        process_due_eyun_outbound_messages,
        utcnow,
    )

    wid = args.wid or _default_wid()
    queued = await enqueue_wechat_outbound(
        w_id=wid,
        wc_id=args.to,
        content=args.content,
        source_batch_key="smoke-test",
        due_at=utcnow(),
    )
    await process_due_eyun_outbound_messages(limit=1)
    _print_response(queued)


async def init_contacts(args: argparse.Namespace) -> None:
    wid = args.wid or _default_wid()
    data = await _post("/initAddressList", {"wId": wid})
    _print_response(data)


async def list_contacts(args: argparse.Namespace) -> None:
    wid = args.wid or _default_wid()
    data = await _post("/getAddressList", {"wId": wid})
    _print_response(data)


async def contact_detail(args: argparse.Namespace) -> None:
    wid = args.wid or _default_wid()
    data = await _post(
        "/getContact",
        {
            "wId": wid,
            "wcId": args.wc_id,
        },
    )
    _print_response(data)


async def set_callback(args: argparse.Namespace) -> None:
    data = await _post(
        "/setHttpCallbackUrl",
        {
            "httpUrl": args.url,
            "type": args.type,
        },
    )
    _print_response(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eyun WeChat API smoke test helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Get EYUN_AUTHORIZATION.")
    login_parser.set_defaults(func=login)

    qrcode_parser = subparsers.add_parser("qrcode", help="Create WeChat login QR code.")
    qrcode_parser.add_argument("--wc-id", default="", help="Empty for first login; previous wcId for reconnect.")
    qrcode_parser.add_argument("--device-type", default="ipad")
    qrcode_parser.set_defaults(func=qrcode)

    confirm_parser = subparsers.add_parser("confirm", help="Confirm QR-code login after scanning.")
    confirm_parser.add_argument("--wid", default="")
    confirm_parser.add_argument("--auto-check", action="store_true")
    confirm_parser.set_defaults(func=confirm)

    send_parser = subparsers.add_parser("send-text", help="Send a text message.")
    send_parser.add_argument("--wid", default="")
    send_parser.add_argument("--to", default="filehelper")
    send_parser.add_argument("--content", default="Hello from Eyun smoke test")
    send_parser.set_defaults(func=send_text)

    init_contacts_parser = subparsers.add_parser(
        "init-contacts", help="Initialize WeChat contacts before listing them."
    )
    init_contacts_parser.add_argument("--wid", default="")
    init_contacts_parser.set_defaults(func=init_contacts)

    list_contacts_parser = subparsers.add_parser(
        "list-contacts", help="List friend, chatroom, and official-account IDs."
    )
    list_contacts_parser.add_argument("--wid", default="")
    list_contacts_parser.set_defaults(func=list_contacts)

    contact_detail_parser = subparsers.add_parser(
        "contact-detail", help="Get nickname, avatar, and alias for one or more wcIds."
    )
    contact_detail_parser.add_argument("--wid", default="")
    contact_detail_parser.add_argument("--wc-id", required=True)
    contact_detail_parser.set_defaults(func=contact_detail)

    callback_parser = subparsers.add_parser("set-callback", help="Configure optimized webhook callback.")
    callback_parser.add_argument("--url", required=True)
    callback_parser.add_argument("--type", type=int, default=2)
    callback_parser.set_defaults(func=set_callback)

    return parser


async def main() -> None:
    _load_env_file()
    parser = build_parser()
    args = parser.parse_args()
    try:
        await args.func(args)
    except EyunConfigError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
