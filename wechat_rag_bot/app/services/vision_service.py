import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings


logger = logging.getLogger("wechat_rag_bot.vision")


class VisionError(RuntimeError):
    pass


class VisionRecognitionError(VisionError):
    pass


class VisionAnalysis(BaseModel):
    image_type: str = "other"
    summary: str = ""
    visible_text: list[str] = Field(default_factory=list)
    visible_facts: list[str] = Field(default_factory=list)
    possible_need: str = ""
    needs_ocr: bool = False
    needs_clarification: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


VISION_PROMPT = """你是电商销售系统的图片理解模块，只提取图片中可以确认的事实，不直接回复客户。
请识别商品、订单、物流、付款、聊天截图、商品破损或其他可见内容。
看不清时必须降低 confidence 并设置 needs_clarification=true，禁止猜测商品款号、订单状态或客户意图。
只返回一个 JSON 对象，不要使用 Markdown。字段必须为：
image_type, summary, visible_text, visible_facts, possible_need, needs_ocr,
needs_clarification, risk_flags, confidence。
visible_text、visible_facts、risk_flags 必须是字符串数组，confidence 为 0 到 1 的数字。"""


OCR_PROMPT = """你是中文图片文字识别模块。完整提取图片中清晰可见的文字，保持订单号、金额、规格、时间和物流状态准确。
无法确认的字符不要猜测。只返回一个 JSON 对象，不要使用 Markdown，并使用与主视觉识别相同的字段结构。
将识别到的文字逐行放入 visible_text；summary 简要说明图片类型；confidence 表示文字识别可信度。"""


async def analyze_image(image_source: str) -> VisionAnalysis:
    settings = get_settings()
    if not settings.vision_enabled:
        raise VisionRecognitionError("vision is disabled")
    if not image_source.strip():
        raise VisionRecognitionError("image source is empty")

    api_key = settings.vision_api_key.strip() or settings.dashscope_api_key.strip()
    if not api_key:
        raise VisionRecognitionError("vision api key is missing")

    analysis = await _call_model(
        model=settings.vision_model,
        image_source=image_source,
        prompt=VISION_PROMPT,
        api_key=api_key,
    )
    if analysis.needs_ocr and settings.vision_ocr_enabled:
        try:
            ocr = await _call_model(
                model=settings.vision_ocr_model,
                image_source=image_source,
                prompt=OCR_PROMPT,
                api_key=api_key,
            )
            analysis = _merge_ocr(analysis, ocr)
        except VisionError as exc:
            logger.warning("Vision OCR fallback failed: %s", exc)

    if (
        analysis.confidence < settings.vision_min_confidence
        or analysis.needs_clarification
        or not (analysis.summary or analysis.visible_text or analysis.visible_facts)
    ):
        raise VisionRecognitionError("image recognition confidence is insufficient")
    return analysis


async def _call_model(
    *, model: str, image_source: str, prompt: str, api_key: str
) -> VisionAnalysis:
    settings = get_settings()
    last_error: Exception | None = None
    for _ in range(settings.vision_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.vision_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.vision_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": image_source},
                                    },
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                    },
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_analysis(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            last_error = exc
    raise VisionError("vision model request failed") from last_error


def _parse_analysis(content: Any) -> VisionAnalysis:
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    if not isinstance(content, str):
        raise ValueError("vision response content is not text")
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    return VisionAnalysis.model_validate(json.loads(raw))


def _merge_ocr(primary: VisionAnalysis, ocr: VisionAnalysis) -> VisionAnalysis:
    visible_text = list(primary.visible_text)
    for line in ocr.visible_text:
        if line and line not in visible_text:
            visible_text.append(line)
    return primary.model_copy(
        update={
            "visible_text": visible_text,
            "needs_ocr": False,
            "confidence": max(primary.confidence, ocr.confidence),
        }
    )


def format_analysis_for_chat(analysis: VisionAnalysis, *, index: int = 1) -> str:
    lines = [f"[用户发送的第{index}张图片识别结果]", f"图片类型：{analysis.image_type}"]
    if analysis.summary:
        lines.append(f"内容概述：{analysis.summary}")
    if analysis.visible_facts:
        lines.append("可确认事实：" + "；".join(analysis.visible_facts))
    if analysis.visible_text:
        lines.append("图片文字：" + "；".join(analysis.visible_text))
    if analysis.possible_need:
        lines.append(f"可能诉求（仅供判断，不是事实）：{analysis.possible_need}")
    if analysis.risk_flags:
        lines.append("风险提示：" + "；".join(analysis.risk_flags))
    return "\n".join(lines)
