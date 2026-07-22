from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import io
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

from app.core.config import get_settings


EYUN_LINK_CARD_THUMB_MAX_BYTES = 51_200
_TARGET_BYTES = 48_000
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MAX_DIMENSION = 640


def link_card_thumbnail_storage_dir() -> Path:
    directory = Path(get_settings().upload_dir) / "link-card-thumbs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def compress_link_card_thumbnail(source_url: str) -> str:
    source_url = source_url.strip()
    parsed = urlsplit(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Eyun link card thumbnail must be a public HTTP(S) URL")
    await _assert_public_url(source_url)

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(source_url)
    response.raise_for_status()
    await _assert_public_url(str(response.url))
    source = response.content
    if not source or len(source) > _MAX_SOURCE_BYTES:
        raise RuntimeError("Eyun link card thumbnail source is empty or exceeds 20 MB")

    digest = hashlib.sha256(source).hexdigest()[:24]
    target = link_card_thumbnail_storage_dir() / f"link-card-{digest}.jpg"
    if not target.exists() or target.stat().st_size >= EYUN_LINK_CARD_THUMB_MAX_BYTES:
        compressed = await asyncio.to_thread(_compress_thumbnail_bytes, source)
        _replace_file(target, compressed)

    public_base_url = get_settings().app_public_base_url.strip().rstrip("/")
    if not public_base_url:
        raise RuntimeError("APP_PUBLIC_BASE_URL is required for link card compression")
    return f"{public_base_url}/static/link-card-thumbs/{target.name}"


def _compress_thumbnail_bytes(source: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            if getattr(image, "is_animated", False):
                image.seek(0)
            image = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - normalize image decoder errors
        raise RuntimeError("Eyun link card thumbnail cannot be decoded") from exc

    image.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.Resampling.LANCZOS)
    while True:
        for quality in (82, 74, 66, 58, 50, 42):
            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            content = output.getvalue()
            if len(content) <= _TARGET_BYTES:
                return content
        if max(image.size) <= 160:
            raise RuntimeError("Eyun link card thumbnail cannot be compressed below 50 KB")
        image.thumbnail(
            (max(160, int(image.width * 0.8)), max(160, int(image.height * 0.8))),
            Image.Resampling.LANCZOS,
        )


def _replace_file(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)


async def _assert_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Eyun link card thumbnail must use public HTTP(S)")
    if parsed.username or parsed.password:
        raise RuntimeError("Eyun link card thumbnail URL cannot contain credentials")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise RuntimeError("Eyun link card thumbnail host cannot be resolved") from exc
    if not addresses:
        raise RuntimeError("Eyun link card thumbnail host cannot be resolved")
    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise RuntimeError("Eyun link card thumbnail cannot use a private address")
