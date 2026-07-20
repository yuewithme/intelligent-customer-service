import asyncio
import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.schemas.unpurchased_sop import UnpurchasedSopStepRequest


logger = logging.getLogger("wechat_rag_bot.link_card_metadata")
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LinkCardMetadata:
    title: str = ""
    description: str = ""
    thumb_url: str = ""


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() != "meta":
            return
        key = (
            values.get("property")
            or values.get("name")
            or values.get("itemprop")
            or ""
        ).lower()
        content = values.get("content", "").strip()
        if key and content and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())


async def enrich_sop_link_cards(request: UnpurchasedSopStepRequest) -> None:
    async def enrich(message) -> None:
        if message.message_type != "link_card" or not message.url:
            return
        if message.title and message.description and message.thumb_url:
            return
        try:
            metadata = await fetch_link_card_metadata(message.url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Link card metadata fetch failed for %s: %s", message.url, exc)
            return
        message.title = message.title or metadata.title[:256] or None
        message.description = message.description or metadata.description[:1000] or None
        message.thumb_url = message.thumb_url or metadata.thumb_url or None

    await asyncio.gather(*(enrich(message) for message in request.messages))


async def fetch_link_card_metadata(url: str) -> LinkCardMetadata:
    current_url = url.strip()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_public_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise ValueError("link card redirect has no location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type:
                    raise ValueError("link card URL did not return HTML")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_HTML_BYTES:
                        raise ValueError("link card page is too large")
                encoding = response.encoding or "utf-8"
                html = bytes(body).decode(encoding, errors="replace")
                metadata = _parse_link_card_metadata(html, current_url)
                if metadata.thumb_url:
                    await _assert_public_url(metadata.thumb_url)
                return metadata
    raise ValueError("link card URL redirected too many times")


def _parse_link_card_metadata(html: str, page_url: str) -> LinkCardMetadata:
    youzan = _parse_youzan_goods_data(html)
    parser = _MetadataParser()
    parser.feed(html)
    title = youzan.title or _first(
        parser.meta, "og:title", "twitter:title", "title"
    )
    if not title:
        title = " ".join(parser.title_parts)
    description = youzan.description or _first(
        parser.meta, "og:description", "twitter:description", "description"
    )
    thumb_url = youzan.thumb_url or _first(
        parser.meta, "og:image", "twitter:image", "twitter:image:src", "image"
    )
    if thumb_url:
        thumb_url = _normalize_thumb_url(urljoin(page_url, thumb_url))
    return LinkCardMetadata(
        title=_clean_text(title),
        description=_clean_text(description),
        thumb_url=thumb_url,
    )


def _parse_youzan_goods_data(html: str) -> LinkCardMetadata:
    match = re.search(r'"goodsData":"([^"\r\n]*)"', html)
    if not match:
        return LinkCardMetadata()
    try:
        decoded = unquote(match.group(1))
        decoded = re.sub(
            r"%u([0-9a-fA-F]{4})",
            lambda item: chr(int(item.group(1), 16)),
            decoded,
        )
        goods = json.loads(decoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return LinkCardMetadata()
    content = goods.get("content") if isinstance(goods, dict) else None
    content = content if isinstance(content, dict) else {}
    video = content.get("videoContentDTO")
    video = video if isinstance(video, dict) else {}
    column = goods.get("column") if isinstance(goods, dict) else None
    column = column if isinstance(column, dict) else {}
    picture = column.get("picture")
    picture = picture if isinstance(picture, dict) else {}
    return LinkCardMetadata(
        title=str(content.get("title") or ""),
        description=str(content.get("summary") or content.get("columnTitle") or ""),
        thumb_url=str(
            content.get("cover")
            or video.get("videoCover")
            or picture.get("cover")
            or ""
        ),
    )


def _normalize_thumb_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.lower().endswith("yzcdn.cn"):
        if "imageView2/" not in parsed.query:
            separator = "&" if parsed.query else "?"
            return f"{url}{separator}imageView2/2/w/300/h/300/q/60/format/jpg"
    return url


async def _assert_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("link card URL must use public HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("link card URL cannot contain credentials")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not addresses:
        raise ValueError("link card host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("link card URL cannot resolve to a private address")


def _first(values: dict[str, str], *keys: str) -> str:
    return next((values[key].strip() for key in keys if values.get(key)), "")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
