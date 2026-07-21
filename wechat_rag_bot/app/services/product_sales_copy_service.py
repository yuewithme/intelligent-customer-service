import json
import re
import asyncio
from typing import Any

from app.services.llm_service import generate_answer


MIN_COPY_LENGTH = 90
MAX_COPY_LENGTH = 150
_ABSOLUTE_CLAIMS = (
    "唯一",
    "第一",
    "顶级",
    "绝版",
    "传世",
    "金奖",
    "闭眼养",
    "人民币",
    "清代",
    "国展",
    "获奖",
    "无需春化",
)
_FORBIDDEN_PATTERNS = (
    (re.compile(r"https?://", re.I), "包含链接"),
    (re.compile(r"(?<![A-Za-z0-9])L[1-6](?![A-Za-z0-9])", re.I), "包含内部等级"),
    (re.compile(r"\d+(?:\.\d+)?\s*元"), "包含价格"),
    (re.compile(r"(?:库存|现货|优惠|购买链接|立即下单|点击下单)"), "包含实时交易信息"),
    (re.compile(r"(?:^|\s)\d+[.、]"), "使用编号罗列"),
)


def build_product_sales_copy_prompt(product: dict[str, Any]) -> str:
    facts = {
        "商品名称": _text(product.get("product_name")),
        "兰花品类": _text(product.get("category")),
        "内部商品等级": _text(product.get("audience_tag")),
        "花色": _text(product.get("flower_color")),
        "香味": _text(product.get("fragrance")),
        "花期": _text(product.get("bloom_period")),
        "养护场景": _text(product.get("care_scenes")),
        "核心资料": _text(product.get("highlighted_features")),
    }
    payload = json.dumps(facts, ensure_ascii=False, indent=2)
    return f"""你是“萧岚苑”的资深兰花塑品文案师。

你的任务不是罗列商品参数，也不是直接催促客户购买，而是根据真实商品资料，塑造这款兰花独特、可信、令人向往的产品形象。你需要回答：“这款兰花为什么值得被客户记住？”

【塑品原则】
1. 先确定一个最有价值的塑品主线，只围绕这条主线展开。
2. 可以选择视觉辨识度、生长或开花变化、香气与花期体验、空间陈设价值、可信的品种底蕴，或好养勤花带来的长期拥有价值。
3. 不要平均介绍所有字段，只选择最有辨识度的1—2个特点深入表达。
4. 把参数转化为客户能够想象的画面和感受，但不得改变事实。
5. 语气像真正懂兰花的人：专业但不学术，有审美但不空泛，有价值感但不油腻。

【等级仅用于内部塑品方向】
- L1—L2：突出好养、容易获得正反馈和日常观赏价值。
- L3—L4：突出花色、花型、香气、株型的特色和辨识度。
- L5—L6：突出有资料依据的品种底蕴、稀缺性、稳定性和收藏价值。
- 最终话术中禁止出现L1—L6。

【写作要求】
1. 输出一段可以直接承接在“推荐您看看某款商品，当前售价××元。”之后的话术。
2. 90—150个汉字，使用2—3句话，不换行。
3. 开头不要重复商品名称，不以“这款”“它的特点是”“推荐理由是”开头。
4. 不使用编号、项目符号、字段名称、标题、表情符号。
5. 不写成百科、参数说明或商品详情页，不机械地以“值得入手”“不要错过”结尾。
6. 不写价格、库存、优惠、购买链接、当前是否带花或下单引导。
7. 不增加资料中不存在的香味、花期、获奖记录、稀缺性、养护表现或开花次数。
8. “唯一、顶级、第一、绝版、传世、金奖、闭眼养”等表达，只有输入资料明确支持时才允许使用。
9. 完全忽略数据库中原有的销售话术。原有话术不是事实来源，不得继承其中的历史、获奖、稀缺性或营销说法。
10. 先在内部检查价值点、画面感、差异性和事实依据，不输出分析过程。

【商品资料】
{payload}

【输出格式】
只输出合法JSON，不要代码块或解释：
{{"sales_copy":"最终塑品话术"}}
"""


async def generate_product_sales_copy(
    product: dict[str, Any],
    *,
    max_attempts: int = 3,
) -> str:
    prompt = build_product_sales_copy_prompt(product)
    errors: list[str] = []
    for attempt in range(max_attempts):
        current_prompt = prompt
        if attempt and errors:
            current_prompt += (
                "\n\n上一次输出未通过校验，请修正以下问题后重新输出JSON："
                + "；".join(errors)
            )
        try:
            result = await generate_answer(current_prompt, purpose="rag")
        except Exception:  # noqa: BLE001 - transient provider failures are retried
            errors = ["模型接口暂时不可用"]
            if attempt + 1 < max_attempts:
                await asyncio.sleep(2**attempt)
            continue
        try:
            copy = parse_sales_copy_response(str(result.get("answer") or ""))
        except ValueError as exc:
            errors = [str(exc)]
            continue
        errors = validate_sales_copy(copy, product)
        if not errors:
            return copy
    raise ValueError(f"生成话术连续{max_attempts}次未通过校验：{'；'.join(errors)}")


def parse_sales_copy_response(raw: str) -> str:
    value = str(raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回JSON") from None
        try:
            payload = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回的JSON无法解析") from exc
    copy = _text(payload.get("sales_copy") if isinstance(payload, dict) else "")
    copy = re.sub(r"\s+", "", copy)
    if not copy:
        raise ValueError("sales_copy为空")
    return copy


def validate_sales_copy(copy: str, product: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if len(copy) < MIN_COPY_LENGTH:
        errors.append(f"长度不足{MIN_COPY_LENGTH}字")
    if len(copy) > MAX_COPY_LENGTH:
        errors.append(f"长度超过{MAX_COPY_LENGTH}字")
    for pattern, message in _FORBIDDEN_PATTERNS:
        if pattern.search(copy):
            errors.append(message)
    source = _text(product.get("highlighted_features"))
    for claim in _ABSOLUTE_CLAIMS:
        if claim in copy and claim not in source:
            errors.append(f"无资料依据的表达：{claim}")
    for year in re.findall(r"(?:19|20)\d{2}年", copy):
        if year not in source:
            errors.append(f"无资料依据的年份：{year}")
    name = _text(product.get("product_name"))
    if name and copy.startswith(name):
        errors.append("开头重复商品名称")
    return list(dict.fromkeys(errors))


def _text(value: Any) -> str:
    return str(value or "").strip()
