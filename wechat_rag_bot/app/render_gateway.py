"""Public Render entrypoint for the sales-agent web demo.

This service deliberately owns no sales state. It serves the demo page and
forwards every conversation action to the formal Sales Agent deployment.
"""

from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.demo import DemoChatRequest, DemoOpeningRequest


class RenderGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    demo_upstream_base_url: str = Field(default="", alias="DEMO_UPSTREAM_BASE_URL")
    demo_upstream_timeout_seconds: float = Field(
        default=200, ge=1, alias="DEMO_UPSTREAM_TIMEOUT_SECONDS"
    )


@lru_cache
def get_settings() -> RenderGatewaySettings:
    return RenderGatewaySettings()


def _frontend_dir() -> Path | None:
    candidates = (
        Path("/app/admin-web"),
        Path(__file__).resolve().parents[2] / "admin-web" / "dist-prod",
    )
    return next((path for path in candidates if (path / "index.html").is_file()), None)


app = FastAPI(title="Sales Agent Demo Gateway")
frontend_dir = _frontend_dir()

if frontend_dir is not None:
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/demo-chat")


@app.get("/demo-chat", include_in_schema=False)
@app.get("/demo-chat/", include_in_schema=False)
async def demo_chat_page() -> FileResponse:
    if frontend_dir is None:
        raise HTTPException(status_code=404, detail="Demo frontend is unavailable")
    return FileResponse(frontend_dir / "index.html")


@app.get("/health")
async def health() -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "status": "ok",
            "mode": "demo_gateway",
            "upstream_configured": bool(
                get_settings().demo_upstream_base_url.strip()
            ),
        },
    }


async def _forward(path: str, payload: dict) -> JSONResponse:
    settings = get_settings()
    upstream = settings.demo_upstream_base_url.strip().rstrip("/")
    if not upstream:
        return JSONResponse(
            status_code=503,
            content={
                "code": 50000,
                "message": "正式销售服务未配置",
                "data": None,
            },
        )

    timeout = httpx.Timeout(
        connect=min(settings.demo_upstream_timeout_seconds, 15),
        read=settings.demo_upstream_timeout_seconds,
        write=settings.demo_upstream_timeout_seconds,
        pool=15,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{upstream}/api/v1/demo/{path}", json=payload
            )
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return JSONResponse(
            status_code=502,
            content={
                "code": 50000,
                "message": "正式销售服务暂时不可用",
                "data": None,
            },
        )
    return JSONResponse(status_code=response.status_code, content=body)


@app.post("/api/v1/demo/opening")
async def demo_opening(request: DemoOpeningRequest) -> JSONResponse:
    return await _forward("opening", request.model_dump(exclude_none=True))


@app.post("/api/v1/demo/chat")
async def demo_chat(request: DemoChatRequest) -> JSONResponse:
    return await _forward("chat", request.model_dump(exclude_none=True))
