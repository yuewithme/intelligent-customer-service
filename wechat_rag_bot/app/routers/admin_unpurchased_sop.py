from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.config import get_settings
from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.schemas.unpurchased_sop import (
    UnpurchasedSopStepRequest,
    UnpurchasedSopTestSendRequest,
    UnpurchasedSopUpdateRequest,
)
from app.services.unpurchased_sop_service import (
    create_unpurchased_sop_step,
    delete_unpurchased_sop_step,
    get_unpurchased_sop,
    list_unpurchased_sop_contacts,
    list_unpurchased_sop_deliveries,
    sync_eyun_contacts,
    test_send_unpurchased_sop_step,
    update_unpurchased_sop,
    update_unpurchased_sop_step,
)
from app.utils.auth import require_api_key
from app.utils.ids import generate_id


router = APIRouter(
    prefix="/api/v1/admin/unpurchased-sop",
    tags=["unpurchased-sop"],
    dependencies=[Depends(require_api_key)],
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


@router.get("", response_model=APIResponse)
async def get_sop() -> APIResponse:
    return APIResponse(code=0, message="success", data=get_unpurchased_sop())


@router.put("", response_model=APIResponse)
async def update_sop(request: UnpurchasedSopUpdateRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=update_unpurchased_sop(request))


@router.post("/steps", response_model=APIResponse)
async def create_step(request: UnpurchasedSopStepRequest) -> APIResponse:
    return APIResponse(code=0, message="success", data=create_unpurchased_sop_step(request))


@router.put("/steps/{step_id}", response_model=APIResponse)
async def update_step(step_id: int, request: UnpurchasedSopStepRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=update_unpurchased_sop_step(step_id, request),
    )


@router.delete("/steps/{step_id}", response_model=APIResponse)
async def delete_step(step_id: int) -> APIResponse:
    return APIResponse(code=0, message="success", data=delete_unpurchased_sop_step(step_id))


@router.post("/contacts/sync", response_model=APIResponse)
async def sync_contacts() -> APIResponse:
    return APIResponse(code=0, message="success", data=await sync_eyun_contacts())


@router.get("/contacts", response_model=APIResponse)
async def contacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_unpurchased_sop_contacts(page, page_size),
    )


@router.get("/deliveries", response_model=APIResponse)
async def deliveries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_unpurchased_sop_deliveries(page, page_size),
    )


@router.post("/test-send", response_model=APIResponse)
async def test_send(request: UnpurchasedSopTestSendRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=await test_send_unpurchased_sop_step(request.step_id, request.contact_id),
    )


@router.post("/media/upload", response_model=APIResponse)
async def upload_media(request: Request, file: UploadFile = File(...)) -> APIResponse:
    original_name = Path(file.filename or "media").name
    suffix = Path(original_name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        media_type = "image"
        max_bytes = get_settings().sop_image_max_bytes
    elif suffix in VIDEO_SUFFIXES:
        media_type = "video"
        max_bytes = get_settings().sop_video_max_bytes
    else:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            message="仅支持 JPG、PNG、GIF、WEBP、MP4、MOV、M4V 文件",
            status_code=422,
        )
    directory = sop_media_storage_dir()
    stored_name = f"{generate_id('sop_media')}{suffix}"
    target = directory / stored_name
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(
                        ErrorCode.REQUEST_INVALID,
                        message=f"文件大小不能超过 {max_bytes // 1024 // 1024}MB",
                        status_code=413,
                    )
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    relative_url = f"/static/sop-media/{stored_name}"
    return APIResponse(
        code=0,
        message="success",
        data={
            "type": media_type,
            "name": original_name,
            "size": size,
            "url": _public_url(request, relative_url),
            "relative_url": relative_url,
        },
    )


def sop_media_storage_dir() -> Path:
    directory = Path(get_settings().upload_dir) / "sop-media"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _public_url(request: Request, relative_url: str) -> str:
    configured = get_settings().public_base_url.strip().rstrip("/")
    if configured:
        return f"{configured}{relative_url}"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}{relative_url}"
