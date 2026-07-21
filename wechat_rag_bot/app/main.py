import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routers import (
    admin_activities,
    admin_care_manuals,
    admin_conversations,
    admin_eyun_materials,
    admin_gate,
    admin_handoff_notification,
    admin_logs,
    admin_memory,
    admin_products,
    admin_sales_flow,
    admin_tags,
    admin_service_sop,
    admin_unpurchased_sop,
    chat,
    debug,
    demo,
    demo_admin,
    eyun,
    intent_examples,
    knowledge,
    state,
    templates,
    user_profile,
    wechat,
    youzan,
)
from app.schemas.common import AppError, ErrorCode
from app.services.message_risk_control_service import eyun_risk_control_worker
from app.services.unpurchased_sop_service import unpurchased_sop_worker
from app.services.youzan_product_sync_service import youzan_product_sync_worker
from app.workers.memory_worker import memory_worker
from app.services.eyun_callback_service import video_storage_dir
from app.services.link_card_thumbnail_service import link_card_thumbnail_storage_dir
from app.routers.admin_unpurchased_sop import sop_media_storage_dir
from app.utils.logger import configure_logging
from app.mcp_server import mcp_asgi_app, run_sales_mcp_session_manager


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().evaluation_mode:
        yield
        return
    stop_event = asyncio.Event()
    app.state.eyun_risk_control_stop_event = stop_event
    app.state.eyun_risk_control_task = asyncio.create_task(
        eyun_risk_control_worker(stop_event)
    )
    app.state.unpurchased_sop_task = asyncio.create_task(
        unpurchased_sop_worker(stop_event)
    )
    app.state.youzan_product_sync_task = asyncio.create_task(
        youzan_product_sync_worker(stop_event)
    )
    app.state.memory_v2_task = None
    if getattr(get_settings(), "memory_v2_write_enabled", False):
        app.state.memory_v2_task = asyncio.create_task(memory_worker(stop_event))
    async with run_sales_mcp_session_manager():
        try:
            yield
        finally:
            stop_event.set()
            await app.state.eyun_risk_control_task
            await app.state.unpurchased_sop_task
            await app.state.youzan_product_sync_task
            if app.state.memory_v2_task is not None:
                await app.state.memory_v2_task


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
video_storage_dir().mkdir(parents=True, exist_ok=True)
app.mount(
    "/static/media",
    StaticFiles(directory=video_storage_dir()),
    name="video-media",
)
app.mount(
    "/static/sop-media",
    StaticFiles(directory=sop_media_storage_dir()),
    name="sop-media",
)
app.mount(
    "/static/link-card-thumbs",
    StaticFiles(directory=link_card_thumbnail_storage_dir()),
    name="link-card-thumbnails",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(admin_activities.router)
app.include_router(admin_care_manuals.router)
app.include_router(admin_conversations.router)
app.include_router(admin_eyun_materials.router)
app.include_router(admin_gate.router)
app.include_router(admin_handoff_notification.router)
app.include_router(admin_tags.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(templates.router)
app.include_router(intent_examples.router)
app.include_router(user_profile.router)
app.include_router(state.router)
app.include_router(debug.router)
app.include_router(demo.router)
app.include_router(demo_admin.router)
app.include_router(demo_admin.profile_router)
app.include_router(admin_logs.router)
app.include_router(admin_memory.router)
app.include_router(admin_products.router)
app.include_router(admin_sales_flow.router)
app.include_router(admin_unpurchased_sop.router)
app.include_router(admin_service_sop.router)
app.include_router(wechat.router)
app.include_router(eyun.router)
app.include_router(youzan.router)


def _admin_web_dir() -> Path | None:
    candidates = (
        Path("/app/admin-web"),
        Path(__file__).resolve().parents[2] / "admin-web" / "dist-prod",
    )
    return next((path for path in candidates if (path / "index.html").is_file()), None)


admin_web_dir = _admin_web_dir()
if admin_web_dir is not None:
    app.mount(
        "/assets",
        StaticFiles(directory=admin_web_dir / "assets"),
        name="admin-web-assets",
    )

    @app.get("/demo-chat", include_in_schema=False)
    @app.get("/demo-chat/", include_in_schema=False)
    async def demo_chat_page() -> FileResponse:
        return FileResponse(admin_web_dir / "index.html")

@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": int(exc.code), "message": exc.message, "data": exc.data},
    )


@app.exception_handler(RequestValidationError)
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


@app.exception_handler(StarletteHTTPException)
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


@app.exception_handler(Exception)
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


@app.get("/health")
async def health() -> dict:
    return {"code": 0, "message": "success", "data": {"status": "ok"}}


app.mount("/", mcp_asgi_app)
