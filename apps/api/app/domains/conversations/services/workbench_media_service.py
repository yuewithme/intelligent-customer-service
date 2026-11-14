import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request, UploadFile

from app.core.config import get_settings
from app.core.ids import generate_id
from app.shared.schemas.common import AppError, ErrorCode
from app.integrations.web.services.public_media_storage import public_media_url


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


async def store_workbench_image(request: Request, file: UploadFile) -> dict:
    original_name = Path(file.filename or "image").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            message="仅支持 JPG、PNG、GIF、WEBP 图片",
            status_code=422,
        )
    target = workbench_media_storage_dir() / (
        f"{generate_id('workbench_media')}{suffix}"
    )
    maximum = get_settings().sop_image_max_bytes
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise AppError(
                        ErrorCode.REQUEST_INVALID,
                        message=f"图片大小不能超过 {maximum // 1024 // 1024}MB",
                        status_code=413,
                    )
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    relative_url = f"/static/workbench-media/{target.name}"
    url = await public_media_url(target, _public_url(request, relative_url))
    return {
        "name": original_name,
        "size": size,
        "url": url,
        "relative_url": relative_url,
    }


async def store_workbench_video(request: Request, file: UploadFile) -> dict:
    from app.integrations.eyun.services.eyun_video_file_service import (
        VideoValidationError, prepare_video_bytes,
    )

    original_name = Path(file.filename or "video").name
    if Path(original_name).suffix.lower() not in VIDEO_SUFFIXES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            message="仅支持 MP4、MOV、M4V、WebM 视频", status_code=422,
        )
    maximum = get_settings().sop_video_max_bytes
    source = await file.read(maximum + 1)
    if len(source) > maximum:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message=f"视频大小不能超过 {maximum // 1024 // 1024}MB", status_code=413,
        )
    if not source:
        raise AppError(ErrorCode.REQUEST_INVALID, message="视频文件为空", status_code=422)
    try:
        prepared = await asyncio.to_thread(prepare_video_bytes, source)
    except VideoValidationError as exc:
        raise AppError(ErrorCode.REQUEST_INVALID, message=str(exc), status_code=422) from exc
    except RuntimeError as exc:
        raise AppError(ErrorCode.REQUEST_INVALID, message=str(exc), status_code=503) from exc
    if len(prepared["source"]) > maximum:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message=f"转码后的视频大小不能超过 {maximum // 1024 // 1024}MB", status_code=413,
        )
    directory = workbench_media_storage_dir()
    name = generate_id("workbench_media")
    video, cover = directory / f"{name}.mp4", directory / f"{name}.jpg"
    try:
        await asyncio.to_thread(video.write_bytes, prepared["source"])
        await asyncio.to_thread(cover.write_bytes, prepared["thumbnail"])
    except Exception:
        video.unlink(missing_ok=True)
        cover.unlink(missing_ok=True)
        raise
    video_url = await public_media_url(video, _public_url(request, f"/static/workbench-media/{video.name}"))
    cover_url = await public_media_url(cover, _public_url(request, f"/static/workbench-media/{cover.name}"))
    return {
        "name": original_name,
        "url": video_url,
        "thumb_url": cover_url,
    }


def workbench_media_storage_dir() -> Path:
    directory = Path(get_settings().upload_dir) / "workbench-media"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _public_url(request: Request, relative_url: str) -> str:
    configured = get_settings().app_public_base_url.strip().rstrip("/")
    if configured:
        return f"{configured}{relative_url}"
    origin = urlsplit(request.headers.get("origin", ""))
    if origin.scheme in {"http", "https"} and origin.netloc:
        return f"{origin.scheme}://{origin.netloc}{relative_url}"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}{relative_url}"
