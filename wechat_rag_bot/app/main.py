import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routers import (
    admin_activities,
    admin_conversations,
    admin_gate,
    admin_logs,
    chat,
    debug,
    eyun,
    intent_examples,
    knowledge,
    state,
    templates,
    user_profile,
    wechat,
)
from app.schemas.common import AppError, ErrorCode
from app.services.message_risk_control_service import eyun_risk_control_worker
from app.services.eyun_callback_service import video_storage_dir
from app.utils.logger import configure_logging


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
    try:
        yield
    finally:
        stop_event.set()
        await app.state.eyun_risk_control_task


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
video_storage_dir().mkdir(parents=True, exist_ok=True)
app.mount(
    "/static/media",
    StaticFiles(directory=video_storage_dir()),
    name="video-media",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(admin_activities.router)
app.include_router(admin_conversations.router)
app.include_router(admin_gate.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(templates.router)
app.include_router(intent_examples.router)
app.include_router(user_profile.router)
app.include_router(state.router)
app.include_router(debug.router)
app.include_router(admin_logs.router)
app.include_router(wechat.router)
app.include_router(eyun.router)


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
