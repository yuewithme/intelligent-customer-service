from pathlib import Path

import httpx
import pytest

from app.services.eyun_callback_service import persist_eyun_video


@pytest.mark.asyncio
async def test_persist_eyun_video_downloads_authenticated_file(monkeypatch, tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"video-bytes",
        )

    monkeypatch.setattr(
        "app.services.eyun_callback_service._video_storage_dir",
        lambda: tmp_path,
    )
    url = await persist_eyun_video(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        source_url="https://cdn.example.com/protected-video",
        authorization="secret-token",
        msg_id="789",
    )

    assert url.startswith("/static/media/")
    assert url.endswith(".mp4")
    assert (tmp_path / Path(url).name).read_bytes() == b"video-bytes"
    assert requests[0].headers["Authorization"] == "secret-token"


@pytest.mark.asyncio
async def test_persist_eyun_video_rejects_permission_page(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"permission denied",
        )

    monkeypatch.setattr(
        "app.services.eyun_callback_service._video_storage_dir",
        lambda: tmp_path,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="not a video"):
            await persist_eyun_video(
                client=client,
                source_url="https://cdn.example.com/protected-video",
                authorization="secret-token",
                msg_id="789",
            )

    assert list(tmp_path.iterdir()) == []
