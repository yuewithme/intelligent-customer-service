import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.config import get_settings
from app.schemas.chat import APIResponse
from app.schemas.common import AppError, ErrorCode
from app.services.knowledge_service import SUPPORTED_SUFFIXES, index_document
from app.utils.auth import require_api_key
from app.utils.ids import generate_id


router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)


@router.post(
    "/upload",
    response_model=APIResponse,
    dependencies=[Depends(require_api_key)],
)
async def upload_knowledge(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    tenant_id: str = Form(...),
    permission: str = Form("public"),
) -> APIResponse:
    original_name = _safe_filename(file.filename or "upload")
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(ErrorCode.UNSUPPORTED_FILE_TYPE)

    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{generate_id('document')}{suffix}"
    with stored_path.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)

    result = await index_document(
        path=stored_path,
        original_name=original_name,
        kb_id=kb_id,
        tenant_id=tenant_id,
        permission=permission,
    )
    return APIResponse(code=0, message="success", data=result)

