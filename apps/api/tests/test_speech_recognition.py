import json

import httpx

from app.core.config import get_settings
from app.integrations.ai.services.speech_recognition_service import (
    transcribe_audio_file,
)


def test_dashscope_audio_transcription_uses_base64_data_url(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"RIFF-test-audio")
    monkeypatch.setenv("SPEECH_RECOGNITION_ENABLED", "true")
    monkeypatch.setenv("SPEECH_RECOGNITION_API_KEY", "test-key")
    monkeypatch.setenv(
        "SPEECH_RECOGNITION_BASE_URL",
        "https://dashscope.example/compatible-mode/v1",
    )
    monkeypatch.setenv("SPEECH_RECOGNITION_MAX_RETRIES", "0")
    get_settings.cache_clear()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "请帮我看看这盆兰花",
                            "annotations": [
                                {
                                    "type": "audio_info",
                                    "language": "zh",
                                    "emotion": "neutral",
                                }
                            ],
                        }
                    }
                ],
                "usage": {"seconds": 3},
            },
        )

    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    from app.integrations.ai.services import speech_recognition_service

    monkeypatch.setattr(speech_recognition_service.httpx, "AsyncClient", fake_client)

    import asyncio

    transcript = asyncio.run(transcribe_audio_file(audio_path))

    assert transcript.text == "请帮我看看这盆兰花"
    assert transcript.language == "zh"
    assert transcript.duration_seconds == 3
    audio = requests[0]["messages"][0]["content"][0]
    assert audio["type"] == "input_audio"
    assert audio["input_audio"]["data"].startswith("data:audio/wav;base64,")
    get_settings.cache_clear()
