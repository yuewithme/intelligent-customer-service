import asyncio

from app.core.config import get_settings
from app.integrations.ai.services.speech_recognition_service import SpeechTranscript
from app.integrations.ai.services.video_understanding_service import (
    understand_video_file,
)


def test_video_understanding_combines_frames_and_audio(monkeypatch, tmp_path):
    from app.integrations.ai.services import video_understanding_service

    video_path = tmp_path / "customer.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setenv("VIDEO_UNDERSTANDING_ENABLED", "true")
    monkeypatch.setenv("VISION_ENABLED", "true")
    get_settings.cache_clear()

    async def fake_probe(path):
        assert path == video_path
        return 8.0

    async def fake_frames(path, workdir, *, duration, count):
        assert duration == 8.0
        frames = [workdir / "frame-01.jpg", workdir / "frame-02.jpg"]
        for frame in frames:
            frame.write_bytes(b"jpeg")
        return frames

    async def fake_audio(path, workdir, *, duration):
        audio = workdir / "audio.wav"
        audio.write_bytes(b"wav")
        return audio

    async def fake_transcribe(path):
        return SpeechTranscript(text="叶子这里发黄了", language="zh")

    async def fake_analyze(frames):
        return {
            "summary": "视频展示一盆叶片发黄的兰花。",
            "visible_content": ["整盆兰花", "两片黄叶"],
            "orchid_observations": ["黄叶集中在下部"],
            "needs_clarification": True,
            "confidence": 0.82,
        }

    monkeypatch.setattr(video_understanding_service, "_probe_duration", fake_probe)
    monkeypatch.setattr(video_understanding_service, "_extract_frames", fake_frames)
    monkeypatch.setattr(video_understanding_service, "_extract_audio", fake_audio)
    monkeypatch.setattr(video_understanding_service, "transcribe_audio_file", fake_transcribe)
    monkeypatch.setattr(video_understanding_service, "_analyze_frames", fake_analyze)

    result = asyncio.run(understand_video_file(video_path))

    assert result.summary == "视频展示一盆叶片发黄的兰花。"
    assert result.transcript == "叶子这里发黄了"
    assert result.frame_count == 2
    assert result.needs_clarification is True
    assert "音频转写：叶子这里发黄了" in result.to_agent_text()
    get_settings.cache_clear()
