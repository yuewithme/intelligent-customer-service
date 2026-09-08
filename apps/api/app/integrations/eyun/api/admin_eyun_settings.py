from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints

from app.core.auth import require_admin_access
from app.domains.conversations.schemas.chat import APIResponse
from app.integrations.eyun.services.eyun_account_settings_service import (
    get_eyun_account_settings,
    save_eyun_account_settings,
)


AccountIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256, pattern=r"^\S+$")
]


class EyunAccountSettingsRequest(BaseModel):
    w_id: AccountIdentifier
    wc_id: AccountIdentifier


router = APIRouter(
    prefix="/api/v1/admin/eyun-settings",
    tags=["eyun-settings"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("", response_model=APIResponse)
async def get_account_settings() -> APIResponse:
    return APIResponse(code=0, message="success", data=get_eyun_account_settings())


@router.put("", response_model=APIResponse)
async def update_account_settings(request: EyunAccountSettingsRequest) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=save_eyun_account_settings(w_id=request.w_id, wc_id=request.wc_id),
    )
