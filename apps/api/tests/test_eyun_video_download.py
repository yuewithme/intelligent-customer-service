import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import eyun_callback_service
from app.integrations.eyun.services.eyun_callback_service import (
    _decode_silk_to_wav,
    persist_eyun_video,
    persist_eyun_voice,
)


def test_video_storage_uses_compose_persistent_data_directory():
    from app.core.config import PROJECT_ROOT

    expected = PROJECT_ROOT / "data" / "media"

    assert eyun_callback_service.video_storage_dir() == expected


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
        "app.integrations.eyun.services.eyun_callback_service.video_storage_dir",
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
        "app.integrations.eyun.services.eyun_callback_service.video_storage_dir",
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


def test_decode_silk_to_browser_playable_wav(monkeypatch, tmp_path):
    silk_path = tmp_path / "voice.silk"
    pcm_path = tmp_path / "voice.pcm"
    wav_path = tmp_path / "voice.wav"
    silk_path.write_bytes(b"silk")

    def fake_decode(source, target, *, pcm_rate):
        assert source == str(silk_path)
        assert pcm_rate == 24000
        Path(target).write_bytes(b"\x01\x00\x02\x00")

    monkeypatch.setitem(sys.modules, "pilk", SimpleNamespace(decode=fake_decode))

    _decode_silk_to_wav(silk_path, pcm_path, wav_path)

    with wave.open(str(wav_path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24000
        assert audio.readframes(2) == b"\x01\x00\x02\x00"


@pytest.mark.asyncio
async def test_persist_eyun_voice_downloads_and_converts_file(monkeypatch, tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"silk-bytes",
        )

    def fake_decode(silk_path, pcm_path, wav_path):
        assert silk_path.read_bytes() == b"silk-bytes"
        wav_path.write_bytes(b"RIFF-playable-wav")

    monkeypatch.setattr(
        "app.integrations.eyun.services.eyun_callback_service.media_storage_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "app.integrations.eyun.services.eyun_callback_service._decode_silk_to_wav",
        fake_decode,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        url = await persist_eyun_voice(
            client=client,
            source_url="https://cdn.example.com/protected-voice",
            authorization="secret-token",
            msg_id="789",
        )

    assert url.startswith("/static/media/")
    assert url.endswith(".wav")
    assert (tmp_path / Path(url).name).read_bytes() == b"RIFF-playable-wav"
    assert requests[0].headers["Authorization"] == "secret-token"
    assert not list(tmp_path.glob(".*.tmp"))
