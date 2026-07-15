import re


_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_CONTACT_CUE_RE = re.compile(
    r"(?:我的)?(?:收货|收件|邮寄)?地址|"
    r"收件人|联系人|姓名|电话|手机|安排发货"
)
_ADDRESS_PREFIX_RE = re.compile(
    r"^(?:我的)?(?:收货|收件|邮寄)?地址\s*(?:是|[:：])?\s*"
)
_EXPLICIT_NAME_RE = re.compile(
    r"(?:收件人|联系人|姓名)\s*(?:是|[:：])?\s*([一-鿿·]{2,10})"
)
_CITY_RE = re.compile(r"([一-鿿]{2,12}?(?:市|州|盟))")
_ADDRESS_ADMIN_RE = re.compile(r"(?:省|市|自治区|自治州|区|县|旗)")
_ADDRESS_DETAIL_RE = re.compile(r"(?:路|街|巷|号|小区|大厦|苑|园|栋|室|期|村|镇|乡)")
_ACTION_RE = re.compile(r"(?:给我|请|帮我|麻烦).*(?:发货|寄出|安排)")


def extract_shipping_contact(
    text: str,
    *,
    allow_mobile_only: bool = False,
) -> dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    parts = [part.strip() for part in re.split(r"[,，;；\n]+", raw) if part.strip()]
    mobile_match = _MOBILE_RE.search(raw)
    address = _find_address(parts)
    has_contact_context = bool(
        _CONTACT_CUE_RE.search(raw)
        or address
        or (allow_mobile_only and mobile_match)
    )
    if not has_contact_context:
        return {}

    mobile = mobile_match.group(1) if mobile_match else ""
    recipient_name = _find_recipient_name(raw, parts, address, mobile)
    city_match = _CITY_RE.search(address)
    return {
        key: value
        for key, value in {
            "recipient_name": recipient_name,
            "mobile": mobile,
            "shipping_address": address,
            "shipping_city": city_match.group(1) if city_match else "",
        }.items()
        if value
    }


def mask_mobile(mobile: str) -> str:
    return f"{mobile[:3]}****{mobile[-4:]}" if len(mobile) == 11 else mobile


def _find_address(parts: list[str]) -> str:
    for part in parts:
        candidate = _ADDRESS_PREFIX_RE.sub("", part).strip(" ：:")
        if _looks_like_address(candidate):
            return candidate
    return ""


def _looks_like_address(value: str) -> bool:
    return bool(
        len(value) >= 6
        and _ADDRESS_ADMIN_RE.search(value)
        and _ADDRESS_DETAIL_RE.search(value)
    )


def _find_recipient_name(
    raw: str,
    parts: list[str],
    address: str,
    mobile: str,
) -> str:
    explicit = _EXPLICIT_NAME_RE.search(raw)
    if explicit:
        return explicit.group(1)

    if mobile:
        for part in parts:
            if mobile not in part:
                continue
            candidate = part.replace(mobile, "").strip(" ：:")
            candidate = re.sub(r"^(?:电话|手机|联系方式)\s*(?:是|[:：])?", "", candidate)
            if _looks_like_name(candidate):
                return candidate

    for part in parts:
        candidate = _ADDRESS_PREFIX_RE.sub("", part).strip(" ：:")
        if candidate == address or mobile in candidate or _ACTION_RE.search(candidate):
            continue
        if _looks_like_name(candidate):
            return candidate
    return ""


def _looks_like_name(value: str) -> bool:
    return bool(re.fullmatch(r"[一-鿿·]{2,6}", value))
