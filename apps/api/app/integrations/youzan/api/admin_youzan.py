from fastapi import APIRouter, Depends

from app.core.auth import require_admin_access
from app.domains.conversations.schemas.chat import APIResponse
from app.shared.schemas.common import AppError, ErrorCode
from app.integrations.youzan.schemas.credentials import YouzanCredentialsUpdateRequest
from app.integrations.youzan.services.youzan_credential_service import (
    YouzanCredentialStoreError,
    YouzanCredentials,
    credential_status,
    save_youzan_credentials,
)
from app.integrations.youzan.services.youzan_order_sync_service import (
    sync_youzan_orders,
)
from app.integrations.youzan.services.youzan_token_service import (
    get_youzan_token_manager,
    reset_youzan_token_manager,
)


router = APIRouter(
    prefix="/api/v1/admin/youzan",
    tags=["admin-youzan"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("/credentials", response_model=APIResponse)
async def get_credentials() -> APIResponse:
    return APIResponse(code=0, message="success", data=credential_status())


@router.put("/credentials", response_model=APIResponse)
async def update_credentials(
    request: YouzanCredentialsUpdateRequest,
) -> APIResponse:
    try:
        status = save_youzan_credentials(
            YouzanCredentials(
                client_id=request.client_id,
                client_secret=request.client_secret,
                kdt_id=request.kdt_id,
            )
        )
    except YouzanCredentialStoreError as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            str(exc),
            status_code=500,
        ) from exc
    except Exception as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"有赞凭据保存失败：{type(exc).__name__}",
            status_code=500,
        ) from exc
    reset_youzan_token_manager()
    try:
        await get_youzan_token_manager().get_access_token(force_refresh=True)
    except Exception as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"有赞凭据已保存，但 Token 验证失败：{type(exc).__name__}",
            status_code=502,
        ) from exc
    return APIResponse(
        code=0,
        message="success",
        data={**status, "token_verified": True},
    )


@router.post("/orders/sync", response_model=APIResponse)
async def sync_orders() -> APIResponse:
    result = await sync_youzan_orders(trigger="admin")
    return APIResponse(code=0, message="success", data=result)
