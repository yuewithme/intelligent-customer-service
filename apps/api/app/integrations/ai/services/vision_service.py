import json
import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings


logger = logging.getLogger("wechat_rag_bot.vision")

SUPPORTED_ORDER_STORES = frozenset({"萧岚苑", "萧兰苑"})
PURCHASE_TAG = "抖音已购"
UNPAID_OR_CLOSED_ORDER_MARKERS = (
    "待付款",
    "未付款",
    "未支付",
    "已关闭",
    "交易关闭",
    "已取消",
    "退款",
)
PAID_ORDER_MARKERS = (
    "已付款",
    "已支付",
    "待发货",
    "已发货",
    "待收货",
    "已签收",
    "交易完成",
    "交易成功",
    "已完成",
)


class VisionError(RuntimeError):
    pass


class VisionRecognitionError(VisionError):
    pass


class UnsupportedStoreOrderError(VisionRecognitionError):
    def __init__(self, store_name: str):
        self.store_name = store_name
        super().__init__("order screenshot is not from a supported store")


class OrderScreenshotAnalysis(BaseModel):
    is_order_screenshot: bool = False
    store_name: str = ""
    platform: str = ""
    page_type: str = ""
    product: str = ""
    amount: str = ""
    order_number: str = ""
    status: str = ""
    evidence: list[str] = Field(default_factory=list)


class OrchidHealthAnalysis(BaseModel):
    visible_symptoms: list[str] = Field(default_factory=list)
    primary_diagnosis: str = ""
    alternative_diagnosis: str = ""
    evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    severity: str = ""
    isolation_needed: bool = False
    safe_actions: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)


class OrchidGeneralAnalysis(BaseModel):
    visible_features: list[str] = Field(default_factory=list)
    likely_type: str = ""
    identification_basis: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    flowering_state: str = ""
    care_observations: list[str] = Field(default_factory=list)


class VisionAnalysis(BaseModel):
    category: Literal[
        "order", "orchid_health", "orchid_general", "unsupported"
    ] = "unsupported"
    summary: str = ""
    order: OrderScreenshotAnalysis | None = None
    orchid_health: OrchidHealthAnalysis | None = None
    orchid_general: OrchidGeneralAnalysis | None = None
    needs_ocr: bool = False
    needs_clarification: bool = False
    confidence: float = Field(default=0, ge=0, le=1)


VISION_PROMPT = """你是萧岚苑养兰客服的图片观察助手。图片结论是客服判断的软证据，不是硬路由条件。处理以下场景：

场景一：订单截图
- 必须确实是电商订单详情、待收货、已发货或交易完成页面。
- 逐字识别页面展示的店铺名，不得把“兰”改成“岚”，也不得主动把店铺名归一化。
- 提取平台、页面类型、商品、金额、订单状态和订单号。
- 手机号、姓名、详细地址禁止输出。订单号可以提取，但不要把它当作店铺验证依据。
- 即使店铺不是“萧岚苑”或“萧兰苑”，仍将 category 设为 order 并返回真实店铺名，程序会自行决定是否支持。

场景二：兰花病虫害或健康问题
- 图片必须展示兰花的叶、根、芦头、花或整株，并存在可见异常。
- 输出可见症状、最可能问题、第二候选、不确定项、安全处理建议和需要追问的问题。
- 仅凭照片不能确定病原时必须保留不确定性；不要把干硬的正常老叶鞘直接判成腐烂。
- 不对健康兰花硬做病害诊断。

场景三：正常兰花、开花状态或品类询问
- category 设为 orchid_general。
- 客观记录可见的花、叶、假鳞茎、整株状态和开花情况。
- 客户询问“是不是建兰”等品类时，可以给出 likely_type 和判断依据；证据不足时使用“更像、可能是”，不强行精确到具体品种或商品名。
- 可以给出与图片直接相关的养护观察，但不从单张照片夸大推断。

其他非订单、非兰花图片：category 为 unsupported，不猜测与客户养兰问题无关的内容。

只返回一个 JSON 对象，不要使用 Markdown。格式必须为：
{
  "category": "order | orchid_health | orchid_general | unsupported",
  "summary": "",
  "order": {
    "is_order_screenshot": false,
    "store_name": "",
    "platform": "",
    "page_type": "",
    "product": "",
    "amount": "",
    "order_number": "",
    "status": "",
    "evidence": []
  },
  "orchid_health": {
    "visible_symptoms": [],
    "primary_diagnosis": "",
    "alternative_diagnosis": "",
    "evidence": [],
    "uncertainties": [],
    "severity": "",
    "isolation_needed": false,
    "safe_actions": [],
    "clarifying_questions": []
  },
  "orchid_general": {
    "visible_features": [],
    "likely_type": "",
    "identification_basis": [],
    "uncertainties": [],
    "flowering_state": "",
    "care_observations": []
  },
  "needs_ocr": false,
  "needs_clarification": false,
  "confidence": 0.0
}"""


OCR_PROMPT = """你只负责复核电商订单截图。逐字识别店铺名，严格区分“萧岚苑”和“萧兰苑”，不得归一化或猜测。
同时提取平台、页面类型、商品、金额、订单状态和订单号，禁止输出手机号、姓名和详细地址。
如果不是订单截图则 category=unsupported。只返回与主视觉接口完全相同的 JSON 结构。"""


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
    if (
        analysis.category == "order"
        and analysis.needs_ocr
        and settings.vision_ocr_enabled
    ):
        try:
            ocr = await _call_model(
                model=settings.vision_ocr_model,
                image_source=image_source,
                prompt=OCR_PROMPT,
                api_key=api_key,
            )
            analysis = _merge_order_ocr(analysis, ocr)
        except VisionError as exc:
            logger.warning("Vision OCR fallback failed: %s", exc)

    if (
        analysis.category == "order"
        and analysis.confidence < settings.vision_min_confidence
    ):
        raise VisionRecognitionError("image recognition confidence is insufficient")
    if analysis.category == "order":
        if not is_supported_store_order(analysis):
            order = analysis.order
            if (
                order
                and order.is_order_screenshot
                and order.store_name.strip()
            ):
                raise UnsupportedStoreOrderError(order.store_name)
            raise VisionRecognitionError("order screenshot is not from a supported store")
    elif analysis.category == "orchid_health":
        health = analysis.orchid_health
        if not health or not health.visible_symptoms or not health.primary_diagnosis:
            raise VisionRecognitionError("orchid health result is incomplete")
    elif analysis.category == "orchid_general":
        general = analysis.orchid_general
        if not general or (not general.visible_features and not analysis.summary.strip()):
            raise VisionRecognitionError("orchid general result is incomplete")
    else:
        raise VisionRecognitionError("unsupported image category")
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


def _merge_order_ocr(primary: VisionAnalysis, ocr: VisionAnalysis) -> VisionAnalysis:
    if ocr.category != "order" or not ocr.order:
        return primary
    primary_order = primary.order or OrderScreenshotAnalysis()
    ocr_order = ocr.order
    merged_order = primary_order.model_copy(
        update={
            field: getattr(ocr_order, field) or getattr(primary_order, field)
            for field in (
                "store_name",
                "platform",
                "page_type",
                "product",
                "amount",
                "order_number",
                "status",
            )
        }
        | {
            "is_order_screenshot": (
                primary_order.is_order_screenshot or ocr_order.is_order_screenshot
            ),
            "evidence": list(
                dict.fromkeys([*primary_order.evidence, *ocr_order.evidence])
            ),
        }
    )
    return primary.model_copy(
        update={
            "order": merged_order,
            "needs_ocr": False,
            "confidence": max(primary.confidence, ocr.confidence),
        }
    )


def normalized_supported_store_name(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or "")).strip()
    if compact in SUPPORTED_ORDER_STORES:
        return compact
    for store in SUPPORTED_ORDER_STORES:
        if compact.startswith(f"{store}-") or compact.startswith(f"{store}—"):
            return store
    return ""


def is_supported_store_order(analysis: VisionAnalysis) -> bool:
    return bool(
        analysis.category == "order"
        and analysis.order
        and analysis.order.is_order_screenshot
        and normalized_supported_store_name(analysis.order.store_name)
    )


def is_verified_store_order(analysis: VisionAnalysis) -> bool:
    if not is_supported_store_order(analysis) or analysis.order is None:
        return False
    platform = re.sub(r"\s+", "", analysis.order.platform).lower()
    status = re.sub(
        r"\s+", "", f"{analysis.order.status} {analysis.order.page_type}"
    )
    return bool(
        "抖音" in platform
        and not any(marker in status for marker in UNPAID_OR_CLOSED_ORDER_MARKERS)
        and any(marker in status for marker in PAID_ORDER_MARKERS)
    )


def purchase_tag_for_analysis(analysis: VisionAnalysis) -> str | None:
    if not get_settings().purchase_tags_enabled:
        return None
    return PURCHASE_TAG if is_verified_store_order(analysis) else None


def format_analysis_for_chat(analysis: VisionAnalysis, *, index: int = 1) -> str:
    if analysis.category == "order" and analysis.order:
        order = analysis.order
        verified_purchase = is_verified_store_order(analysis)
        lines = [
            (
                f"[用户发送的第{index}张图片："
                f"{'已验证抖音已付款订单截图' if verified_purchase else '支持店铺订单截图'}]"
            ),
            f"店铺：{_redact_sensitive(order.store_name)}",
        ]
        for label, value in (
            ("平台", order.platform),
            ("页面类型", order.page_type),
            ("商品", order.product),
            ("金额", order.amount),
            ("订单状态", order.status),
        ):
            if value:
                lines.append(f"{label}：{_redact_sensitive(value)}")
        if order.order_number:
            lines.append(f"订单号：{_mask_identifier(order.order_number)}")
        if verified_purchase and get_settings().purchase_tags_enabled:
            lines.append("系统动作：已将客户标记为抖音已购")
        elif verified_purchase:
            lines.append("系统核验：抖音已付款订单已通过核验")
        return "\n".join(lines)

    health = analysis.orchid_health
    if analysis.category == "orchid_health" and health:
        lines = [
            f"[用户发送的第{index}张图片：兰花健康问题]",
            "可见症状：" + "；".join(health.visible_symptoms),
            f"初步判断：{health.primary_diagnosis}",
        ]
        if health.alternative_diagnosis:
            lines.append(f"第二候选：{health.alternative_diagnosis}")
        if health.uncertainties:
            lines.append("不确定项：" + "；".join(health.uncertainties))
        if health.severity:
            lines.append(f"严重程度：{health.severity}")
        lines.append(f"是否建议隔离：{'是' if health.isolation_needed else '否'}")
        if health.safe_actions:
            lines.append("安全处理建议：" + "；".join(health.safe_actions))
        if health.clarifying_questions:
            lines.append("建议向用户追问：" + "；".join(health.clarifying_questions))
        lines.append("注意：图片诊断仅作初步判断，不能替代实物检查。")
        return "\n".join(_redact_sensitive(line) for line in lines)

    general = analysis.orchid_general
    if analysis.category == "orchid_general" and general:
        lines = [f"[用户发送的第{index}张图片：兰花日常观察]"]
        if general.visible_features:
            lines.append("可见情况：" + "；".join(general.visible_features))
        elif analysis.summary:
            lines.append(f"图片观察：{analysis.summary}")
        if general.likely_type:
            lines.append(f"品类判断：{general.likely_type}")
        if general.identification_basis:
            lines.append("判断依据：" + "；".join(general.identification_basis))
        if general.flowering_state:
            lines.append(f"开花状态：{general.flowering_state}")
        if general.care_observations:
            lines.append("养护观察：" + "；".join(general.care_observations))
        if general.uncertainties:
            lines.append("不确定项：" + "；".join(general.uncertainties))
        lines.append(
            "客服使用：以上是软证据；请以萧岚苑养兰顾问身份自然回答，"
            "有把握就给出判断，证据不足时使用‘看着更像’等条件化表达，"
            "不向客户提及图片识别、模型或系统。"
        )
        return "\n".join(_redact_sensitive(line) for line in lines)
    raise VisionRecognitionError("unsupported image category")


def _mask_identifier(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def _redact_sensitive(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?<!\d)1\d{10}(?!\d)",
        lambda match: f"{match.group()[:3]}****{match.group()[-4:]}",
        text,
    )
    return re.sub(
        r"(?<!\d)\d{8,}(?!\d)",
        lambda match: _mask_identifier(match.group()),
        text,
    )
