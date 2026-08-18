from fastapi import APIRouter, Depends

from app.core.auth import require_admin_access
from app.domains.conversations.schemas.chat import APIResponse
from app.integrations.youzan.schemas.credentials import YouzanCredentialsUpdateRequest
from app.integrations.youzan.services.youzan_credential_service import (
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
    status = save_youzan_credentials(
        YouzanCredentials(
            client_id=request.client_id,
            client_secret=request.client_secret,
            kdt_id=request.kdt_id,
        )
    )
    reset_youzan_token_manager()
    await get_youzan_token_manager().get_access_token(force_refresh=True)
    return APIResponse(
        code=0,
        message="success",
        data={**status, "token_verified": True},
    )


@router.post("/orders/sync", response_model=APIResponse)
async def sync_orders() -> APIResponse:
    result = await sync_youzan_orders(trigger="admin")
    return APIResponse(code=0, message="success", data=result)
