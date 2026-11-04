from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import unquote, urlsplit

from app.core.config import PROJECT_ROOT, get_settings


_prepared_metadata: OrderedDict[tuple[bytes, bytes], dict[str, Any]] = OrderedDict()
_prepared_metadata_lock = Lock()


def _cached_metadata(video_hash: bytes, thumbnail_hash: bytes | None = None) -> dict[str, Any] | None:
    with _prepared_metadata_lock:
        key = next((key for key in reversed(_prepared_metadata)
            if key[0] == video_hash and (thumbnail_hash is None or key[1] == thumbnail_hash)), None)
        if key is None:
            return None
        _prepared_metadata.move_to_end(key)
        return dict(_prepared_metadata[key])


class VideoValidationError(ValueError):
    """The downloaded bytes or video message cannot be used for playback."""


def _run_media_command(arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("视频处理工具不可用或处理超时") from exc
    if result.returncode:
        raise VideoValidationError("视频或封面无法完整解码")
    return result.stdout


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(_run_media_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]))


def _video_metadata(path: Path, *, decode: bool = True) -> dict[str, Any]:
    probe = _probe(path)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    duration = float(probe.get("format", {}).get("duration") or 0)
    if not video or not math.isfinite(duration) or duration <= 0:
        raise VideoValidationError("视频缺少有效画面或时长")
    if int(video.get("width") or 0) <= 0 or int(video.get("height") or 0) <= 0:
        raise VideoValidationError("视频尺寸无效")
    if decode:
        _run_media_command([
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-",
        ])
    return {
        "duration": duration,
        "width": int(video["width"]),
        "height": int(video["height"]),
        "compatible": (
            video.get("codec_name") == "h264"
            and video.get("pix_fmt") == "yuv420p"
            and all(s.get("codec_name") == "aac" for s in streams if s.get("codec_type") == "audio")
            and "mp4" in probe.get("format", {}).get("format_name", "").split(",")
        ),
    }


def validate_video_bytes(content: bytes) -> dict[str, Any]:
    cached = _cached_metadata(hashlib.sha256(content).digest())
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="eyun-video-check-") as directory:
        path = Path(directory) / "video.mp4"
        path.write_bytes(content)
        metadata = _video_metadata(path)
    if not metadata["compatible"]:
        raise VideoValidationError("微信视频需要 MP4/H.264/AAC 编码")
    return metadata


def prepare_browser_video(original: Path, converted: Path) -> dict[str, Any]:
    metadata = _video_metadata(original, decode=False)
    encoding = ["-c:v", "copy", "-c:a", "copy"] if metadata["compatible"] else [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:a", "aac",
    ]
    _run_media_command([
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-y", "-i", str(original),
        "-map", "0:v:0", "-map", "0:a:0?", *encoding, "-movflags", "+faststart",
        "-f", "mp4", str(converted),
    ])
    metadata = _video_metadata(converted)
    if not metadata["compatible"]:
        raise VideoValidationError("视频转码未生成兼容格式")
    return metadata


def prepare_video_bytes(source: bytes, thumbnail: bytes = b"") -> dict[str, Any]:
    cached = _cached_metadata(hashlib.sha256(source).digest(), hashlib.sha256(thumbnail).digest())
    if cached is not None:
        return {**cached, "source": source, "thumbnail": thumbnail}
    with tempfile.TemporaryDirectory(prefix="eyun-video-prepare-") as directory:
        root = Path(directory)
        original, converted, cover = root / "source.mp4", root / "video.mp4", root / "cover.jpg"
        original.write_bytes(source)
        metadata = prepare_browser_video(original, converted)
        cover_source = converted
        if thumbnail:
            cover_source = root / "source-cover"
            cover_source.write_bytes(thumbnail)
        _run_media_command([
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-y", "-i", str(cover_source),
            "-frames:v", "1", "-vf", "scale='min(320,iw)':-2", "-q:v", "3", str(cover),
        ])
        cover_stream = _probe(cover)["streams"][0]
        video_bytes = converted.read_bytes()
        # Eyun's CDN round trip corrupts a partial final AES block; a free box
        # aligns the MP4 without changing sample offsets or audio/video bytes.
        if len(video_bytes) % 16:
            padding_size = 8 + (-len(video_bytes) - 8) % 16
            video_bytes += padding_size.to_bytes(4, "big") + b"free" + bytes(padding_size - 8)
        thumbnail_bytes = cover.read_bytes()
        thumbnail_bytes += bytes(-len(thumbnail_bytes) % 16)
        details = {
            **metadata,
            "thumb_width": int(cover_stream["width"]),
            "thumb_height": int(cover_stream["height"]),
        }
        # Reuse only exact validated bytes; retain metadata, never large media buffers.
        key = (hashlib.sha256(video_bytes).digest(), hashlib.sha256(thumbnail_bytes).digest())
        with _prepared_metadata_lock:
            _prepared_metadata[key] = details
            _prepared_metadata.move_to_end(key)
            while len(_prepared_metadata) > 32:
                _prepared_metadata.popitem(last=False)
        return {**details, "source": video_bytes, "thumbnail": thumbnail_bytes}


def local_media_path(url: str) -> Path | None:
    parsed = urlsplit(url)
    settings = get_settings()
    origins = [urlsplit(base.strip()) for base in (settings.app_public_base_url, settings.video_touch_public_base_url) if base.strip()]
    if parsed.scheme or parsed.netloc:
        if not any((parsed.scheme, parsed.netloc.lower()) == (origin.scheme, origin.netloc.lower()) for origin in origins):
            return None
    path = unquote(parsed.path)
    for prefix, directory in (
        ("/static/media/", PROJECT_ROOT / "data" / "media"),
        ("/static/workbench-media/", Path(settings.upload_dir) / "workbench-media"),
    ):
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):]
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            return None
        target = (directory / name).resolve()
        if target.is_relative_to(directory.resolve()) and target.is_file():
            return target
    return None


def validate_thumbnail_bytes(content: bytes) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix="eyun-thumb-check-") as directory:
        path = Path(directory) / "cover.jpg"
        path.write_bytes(content)
        stream = _probe(path)["streams"][0]
        _run_media_command([
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-frames:v", "1", "-f", "null", "-",
        ])
        return int(stream["width"]), int(stream["height"])


def public_media_url(path: str) -> str:
    settings = get_settings()
    base = (settings.video_touch_public_base_url or settings.app_public_base_url).strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("视频处理需要配置可供易云访问的 APP_PUBLIC_BASE_URL")
    return f"{base}{path}"


def publish_video_file(content: bytes, suffix: str) -> str:
    directory = PROJECT_ROOT / "data" / "media"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"eyun-video-{hashlib.sha256(content).hexdigest()}{suffix}"
    target = directory / name
    url = public_media_url(f"/static/media/{name}")
    if not target.exists() or target.read_bytes() != content:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return url
