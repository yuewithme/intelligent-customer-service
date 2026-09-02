from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings


class SpeechRecognitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechTranscript:
    text: str
    language: str = ""
    emotion: str = ""
    duration_seconds: int | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "emotion": self.emotion,
            "duration_seconds": self.duration_seconds,
        }


async def transcribe_audio_file(audio_path: Path) -> SpeechTranscript:
    settings = get_settings()
    if not settings.speech_recognition_enabled:
        raise SpeechRecognitionError("speech recognition is disabled")
    path = audio_path.resolve()
    if not path.is_file():
        raise SpeechRecognitionError("audio file is missing")

    api_key = (
        settings.speech_recognition_api_key.strip()
        or settings.vision_api_key.strip()
        or settings.dashscope_api_key.strip()
    )
    if not api_key:
        raise SpeechRecognitionError("speech recognition api key is missing")
    base_url = (
        settings.speech_recognition_base_url.strip()
        or settings.vision_base_url.strip()
    )
    if not base_url:
        raise SpeechRecognitionError("speech recognition base url is missing")

    audio_bytes = await asyncio.to_thread(path.read_bytes)
    if not audio_bytes:
        raise SpeechRecognitionError("audio file is empty")
    mime_type = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"

    last_error: Exception | None = None
    for _ in range(settings.speech_recognition_max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=settings.speech_recognition_timeout_seconds
            ) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.speech_recognition_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_audio",
                                        "input_audio": {"data": data_url},
                                    }
                                ],
                            }
                        ],
                        "stream": False,
                        "asr_options": {"enable_itn": True},
                    },
                )
            response.raise_for_status()
            return _parse_transcript(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
    raise SpeechRecognitionError("speech recognition request failed") from last_error


def _parse_transcript(payload: dict[str, Any]) -> SpeechTranscript:
    message = payload["choices"][0]["message"]
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or item.get("content") or "")
            for item in content
            if isinstance(item, dict)
        )
    text = str(content or "").strip()
    if not text:
        raise ValueError("speech recognition response is empty")
    annotations = message.get("annotations")
    annotation = (
        annotations[0]
        if isinstance(annotations, list)
        and annotations
        and isinstance(annotations[0], dict)
        else {}
    )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    duration = usage.get("seconds")
    return SpeechTranscript(
        text=text,
        language=str(annotation.get("language") or ""),
        emotion=str(annotation.get("emotion") or ""),
        duration_seconds=(
            int(duration) if isinstance(duration, (int, float)) else None
        ),
    )
