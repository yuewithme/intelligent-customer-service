from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request, UploadFile

from app.core.config import get_settings
from app.core.ids import generate_id
from app.shared.schemas.common import AppError, ErrorCode


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


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
    return {
        "name": original_name,
        "size": size,
        "url": _public_url(request, relative_url),
        "relative_url": relative_url,
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
