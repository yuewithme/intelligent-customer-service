from __future__ import annotations

import html
import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.auth import require_api_key
from app.domains.conversations.schemas.chat import APIResponse
from app.domains.sales.services.video_touch_tracking_service import (
    enqueue_video_touch_test,
    get_video_touch_landing,
    get_video_touch_test,
    list_video_touch_tests,
    record_video_touch_open,
)


public_router = APIRouter(prefix="/v", tags=["video-touch"])
admin_router = APIRouter(
    prefix="/api/v1/admin/video-touch-tests",
    tags=["admin-video-touch-tests"],
    dependencies=[Depends(require_api_key)],
)


class VideoTouchTestRequest(BaseModel):
    material_ref: str = Field(min_length=1, max_length=256)
    wc_id: str = Field(default="filehelper", min_length=1, max_length=256)
    w_id: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=512)


@public_router.get("/{token}", response_class=HTMLResponse)
async def video_touch_landing(token: str) -> HTMLResponse:
    landing = get_video_touch_landing(token)
    if landing is None:
        raise HTTPException(status_code=404, detail="video not found")
    play_session = secrets.token_urlsafe(18)
    play_path = (
        f"/v/{quote(token, safe='')}/play?session="
        f"{quote(play_session, safe='')}"
    )
    safe_title = html.escape(str(landing["title"]), quote=True)
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>{safe_title}</title>
  <style>
    html,body{{height:100%;margin:0;background:#000;color:#fff}}
    body{{overflow:hidden;font:14px system-ui,sans-serif}}
    video{{display:block;width:100%;height:100%;object-fit:contain;background:#000}}
    .manual{{position:fixed;inset:0;margin:auto;width:88px;height:42px;border:0;
      border-radius:21px;background:rgba(0,0,0,.72);color:#fff;font-size:15px}}
  </style>
</head>
<body>
  <video id="player" src="{html.escape(play_path, quote=True)}" autoplay controls
    playsinline webkit-playsinline="true" x5-playsinline="true"
    x5-video-player-type="h5-page" preload="auto"></video>
  <button id="manual" class="manual" type="button" hidden>点击播放</button>
  <script>
    (() => {{
      const player = document.getElementById('player');
      const manual = document.getElementById('manual');
      const play = () => {{
        if (document.visibilityState !== 'visible') return;
        const result = player.play();
        if (result && typeof result.catch === 'function') {{
          result.catch(() => {{ manual.hidden = false; }});
        }}
      }};
      player.addEventListener('playing', () => {{ manual.hidden = true; }});
      manual.addEventListener('click', play);
      document.addEventListener('DOMContentLoaded', play);
      document.addEventListener('visibilitychange', play);
      document.addEventListener('WeixinJSBridgeReady', play);
      window.addEventListener('pageshow', play);
      play();
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(
        content=body,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; media-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
        },
    )


@public_router.get("/{token}/play")
async def video_touch_play(
    token: str,
    request: Request,
    session: str = Query(min_length=16, max_length=128),
) -> StreamingResponse:
    source = record_video_touch_open(
        token=token,
        play_session=session,
        user_agent=request.headers.get("user-agent", ""),
    )
    if not source:
        raise HTTPException(status_code=404, detail="video not found")

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=10),
        follow_redirects=True,
    )
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        response = await client.send(
            client.build_request("GET", source, headers=headers),
            stream=True,
        )
        response.raise_for_status()
    except Exception:
        await client.aclose()
        raise HTTPException(status_code=502, detail="video unavailable")

    async def body():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    passthrough = {
        key: value
        for key in (
            "content-type",
            "content-length",
            "content-range",
            "accept-ranges",
            "etag",
            "last-modified",
        )
        if (value := response.headers.get(key))
    }
    passthrough.setdefault("Content-Type", "video/mp4")
    passthrough["Cache-Control"] = "private, no-store"
    passthrough["Content-Disposition"] = "inline"
    passthrough["X-Content-Type-Options"] = "nosniff"
    return StreamingResponse(
        body(),
        status_code=response.status_code,
        headers=passthrough,
    )


@admin_router.post("", response_model=APIResponse)
async def create_video_touch_test(
    request: VideoTouchTestRequest, http_request: Request
) -> APIResponse:
    forwarded_proto = http_request.headers.get("x-forwarded-proto", "").split(
        ",", 1
    )[0]
    forwarded_host = http_request.headers.get("x-forwarded-host", "").split(
        ",", 1
    )[0]
    public_base_url = (
        f"{forwarded_proto or http_request.url.scheme}://"
        f"{forwarded_host or http_request.headers.get('host', '')}"
    )
    try:
        data = await enqueue_video_touch_test(
            material_ref=request.material_ref,
            wc_id=request.wc_id,
            w_id=request.w_id,
            title=request.title,
            description=request.description,
            public_base_url=public_base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse(code=0, message="success", data=data)


@admin_router.get("", response_model=APIResponse)
async def video_touch_tests(
    limit: int = Query(default=50, ge=1, le=200),
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data=list_video_touch_tests(limit=limit),
    )


@admin_router.get("/{test_id}", response_model=APIResponse)
async def video_touch_test(test_id: int) -> APIResponse:
    data = get_video_touch_test(test_id)
    if data is None:
        raise HTTPException(status_code=404, detail="video touch test not found")
    return APIResponse(code=0, message="success", data=data)
