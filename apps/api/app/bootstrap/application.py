from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.bootstrap.lifecycle import lifespan
from app.bootstrap.routes import register_routes
from app.core.config import get_settings
from app.integrations.eyun.services.eyun_callback_service import video_storage_dir
from app.integrations.web.services.link_card_thumbnail_service import (
    link_card_thumbnail_storage_dir,
)
from app.integrations.mcp.server import mcp_asgi_app
from app.domains.conversations.services.workbench_media_service import (
    workbench_media_storage_dir,
)
from app.domains.catalog.services.agent_media_library_service import (
    agent_media_library_dir,
)
from app.shared.schemas.common import AppError, ErrorCode
from app.core.logger import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    _mount_static_files(application)
    register_routes(application)
    _register_exception_handlers(application)

    @application.get("/health")
    async def health() -> dict:
        return {"code": 0, "message": "success", "data": {"status": "ok"}}

    application.mount("/", mcp_asgi_app)
    return application


def _mount_static_files(application: FastAPI) -> None:
    agent_media_library_dir().mkdir(parents=True, exist_ok=True)
    application.mount(
        "/static/agent-material-library",
        StaticFiles(directory=agent_media_library_dir()),
        name="agent-material-library",
    )
    video_storage_dir().mkdir(parents=True, exist_ok=True)
    application.mount(
        "/static/media",
        StaticFiles(directory=video_storage_dir()),
        name="video-media",
    )
    application.mount(
        "/static/workbench-media",
        StaticFiles(directory=workbench_media_storage_dir()),
        name="workbench-media",
    )
    application.mount(
        "/static/link-card-thumbs",
        StaticFiles(directory=link_card_thumbnail_storage_dir()),
        name="link-card-thumbnails",
    )
    application.mount(
        "/static",
        StaticFiles(directory=Path(__file__).resolve().parents[1] / "static"),
        name="static",
    )


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": int(exc.code), "message": exc.message, "data": exc.data},
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=422,
            content={
                "code": int(ErrorCode.REQUEST_INVALID),
                "message": "请求参数错误",
                "data": None,
            },
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": int(ErrorCode.REQUEST_INVALID),
                "message": str(exc.detail),
                "data": None,
            },
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=500,
            content={
                "code": int(ErrorCode.INTERNAL_ERROR),
                "message": "服务内部错误",
                "data": None,
            },
        )
