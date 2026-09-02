from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.ai.services.speech_recognition_service import (
    SpeechRecognitionError,
    transcribe_audio_file,
)


class VideoUnderstandingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoUnderstanding:
    summary: str
    visible_content: list[str] = field(default_factory=list)
    orchid_observations: list[str] = field(default_factory=list)
    transcript: str = ""
    language: str = ""
    emotion: str = ""
    duration_seconds: float | None = None
    frame_count: int = 0
    needs_clarification: bool = False
    confidence: float = 0.0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "visible_content": self.visible_content,
            "orchid_observations": self.orchid_observations,
            "transcript": self.transcript,
            "language": self.language,
            "emotion": self.emotion,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "needs_clarification": self.needs_clarification,
            "confidence": self.confidence,
        }

    def to_agent_text(self) -> str:
        parts = [f"[客户视频理解] 摘要：{self.summary}"]
        if self.transcript:
            parts.append(f"音频转写：{self.transcript}")
        if self.visible_content:
            parts.append("画面内容：" + "；".join(self.visible_content))
        if self.orchid_observations:
            parts.append("兰花观察：" + "；".join(self.orchid_observations))
        if self.needs_clarification:
            parts.append("仍需结合上下文自然确认客户希望重点解决的问题。")
        return "\n".join(parts)


_VIDEO_PROMPT = """你是萧岚苑养兰客服的视频观察助手。根据按时间顺序抽取的关键帧，客观总结客户视频中的可见内容。
重点识别兰花叶、根、芦头、花、盆土、病斑、腐烂、虫害、订单或商品画面。证据不足时保留不确定性，不得编造品种、病原、订单状态或客户意图。
只返回 JSON：
{
  "summary": "",
  "visible_content": [],
  "orchid_observations": [],
  "needs_clarification": false,
  "confidence": 0.0
}"""


async def understand_video_file(video_path: Path) -> VideoUnderstanding:
    settings = get_settings()
    if not settings.video_understanding_enabled:
        raise VideoUnderstandingError("video understanding is disabled")
    path = video_path.resolve()
    if not path.is_file():
        raise VideoUnderstandingError("video file is missing")

    with tempfile.TemporaryDirectory(prefix="video-understanding-", dir=path.parent) as tmp:
        workdir = Path(tmp)
        duration = await _probe_duration(path)
        if duration and duration > settings.video_understanding_max_seconds:
            duration = float(settings.video_understanding_max_seconds)
        frames = await _extract_frames(
            path,
            workdir,
            duration=duration,
            count=settings.video_understanding_frame_count,
        )
        audio_path = await _extract_audio(path, workdir, duration=duration)

        transcript = ""
        language = ""
        emotion = ""
        if audio_path is not None:
            try:
                speech = await transcribe_audio_file(audio_path)
                transcript = speech.text
                language = speech.language
                emotion = speech.emotion
            except SpeechRecognitionError:
                pass

        visual: dict[str, Any] = {}
        if frames and settings.vision_enabled:
            try:
                visual = await _analyze_frames(frames)
            except Exception:  # noqa: BLE001
                visual = {}

    summary = str(visual.get("summary") or "").strip()
    if not summary and transcript:
        summary = "视频主要内容来自音频转写。"
    if not summary:
        raise VideoUnderstandingError("video produced no reliable understanding")
    return VideoUnderstanding(
        summary=summary,
        visible_content=_string_list(visual.get("visible_content")),
        orchid_observations=_string_list(visual.get("orchid_observations")),
        transcript=transcript,
        language=language,
        emotion=emotion,
        duration_seconds=duration,
        frame_count=len(frames),
        needs_clarification=bool(visual.get("needs_clarification")),
        confidence=_confidence(visual.get("confidence")),
    )


async def _probe_duration(video_path: Path) -> float | None:
    output = await _run_process(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
        allow_failure=True,
    )
    try:
        value = float(output.strip())
    except ValueError:
        return None
    return value if value > 0 else None


async def _extract_frames(
    video_path: Path,
    workdir: Path,
    *,
    duration: float | None,
    count: int,
) -> list[Path]:
    interval = max((duration or float(count)) / max(count, 1), 0.5)
    target = workdir / "frame-%02d.jpg"
    await _run_process(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval:.3f},scale=960:-2:force_original_aspect_ratio=decrease",
        "-frames:v",
        str(count),
        str(target),
    )
    return sorted(workdir.glob("frame-*.jpg"))[:count]


async def _extract_audio(
    video_path: Path, workdir: Path, *, duration: float | None
) -> Path | None:
    target = workdir / "audio.wav"
    await _run_process(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-t",
        f"{duration or get_settings().video_understanding_max_seconds:.3f}",
        str(target),
        allow_failure=True,
    )
    return target if target.is_file() and target.stat().st_size else None


async def _analyze_frames(frames: list[Path]) -> dict[str, Any]:
    settings = get_settings()
    api_key = settings.vision_api_key.strip() or settings.dashscope_api_key.strip()
    if not api_key:
        raise VideoUnderstandingError("vision api key is missing")
    content: list[dict[str, Any]] = []
    for frame in frames:
        encoded = base64.b64encode(await asyncio.to_thread(frame.read_bytes)).decode(
            "ascii"
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    content.append({"type": "text", "text": _VIDEO_PROMPT})
    async with httpx.AsyncClient(timeout=settings.video_understanding_timeout_seconds) as client:
        response = await client.post(
            f"{settings.vision_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.vision_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    if not isinstance(raw, str):
        raise VideoUnderstandingError("video vision response is not text")
    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise VideoUnderstandingError("video vision response is invalid")
    return parsed


async def _run_process(
    *args: str,
    allow_failure: bool = False,
) -> str:
    settings = get_settings()
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoUnderstandingError(f"media tool is missing: {args[0]}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.video_understanding_timeout_seconds,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise VideoUnderstandingError(f"media tool timed out: {args[0]}") from exc
    if process.returncode and not allow_failure:
        error = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise VideoUnderstandingError(error or f"media tool failed: {args[0]}")
    return stdout.decode("utf-8", errors="replace")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
