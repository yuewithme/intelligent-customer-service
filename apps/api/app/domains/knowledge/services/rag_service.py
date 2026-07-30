import re
import time
from typing import Any

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.schemas.context import ContextPackage, ContextSelectionInput
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.schemas.prompt import PromptBuildInput
from app.domains.knowledge.services import (
    embedding_service,
    qdrant_service,
    rerank_service,
)
from app.integrations.ai.services import llm_service
from app.domains.catalog.orchid_products.knowledge_index import search_orchid_knowledge_chunks
from app.domains.knowledge.services.context_selector import select_context
from app.domains.catalog.services.product_knowledge_service import search_catalog_products
from app.domains.decisioning.services.prompt_builder import build_prompt
from app.core.ids import generate_id
from app.core.logger import log_event
from app.core.time import now_iso


DEFAULT_ORCHID_KB_ID = "kb_orchid_basic"
SALES_SECTION_PREFIXES = (
    "CHUNK SCRIPT-",
    "CHUNK FLOW-",
    "CHUNK SOP-",
)
CARE_MARKERS = (
    "养护",
    "栽培",
    "浇水",
    "施肥",
    "烂根",
    "空根",
    "黑根",
    "病虫",
    "植料",
    "通风",
    "光照",
    "换盆",
    "修根",
    "服盆",
    "黄叶",
    "催花",
    "care",
)
PRODUCT_SOURCE_TABLES = {
    "orchid_varieties",
    "orchid_products",
    "orchid_sales_copy",
}
PRODUCT_CATALOG_SOURCE_TABLES = {
    "youzan_products",
    "youzan_product_knowledge",
    "orchid_varieties",
    "orchid_variety_traits",
    "orchid_products",
}
PRODUCT_VALUE_SOURCE_TABLES = {
    "orchid_value_points",
    "orchid_sales_copy",
}
SKU_SOURCE_TABLES = {"youzan_product_skus", "orchid_skus"}
PROMOTION_SOURCE_TABLES = {"activities", "activity_library", "promotions"}
LEGACY_ORCHID_COMMON_TABLE = "orchid_common_knowledge"
UNVERIFIED_CAPABILITY_PATTERNS = (
    re.compile(
        r"(?:购买后|下单后|到时候|会|可以|给您|帮您|为您|随单|随货)"
        r".{0,16}(?:送|赠送|开通|拉进|加入|安排|提供)"
        r".{0,16}(?:课程|教程|视频|群|一对一|指导|植料|花盆)"
    ),
    re.compile(r"(?:送您|赠送).{0,16}(?:课程|教程|视频|群|指导|植料|花盆)"),
    re.compile(r"(?:课程|教程|视频|群).{0,12}(?:包含|赠送|免费|开通|都有)"),
    re.compile(r"(?:花盆|植料).{0,12}(?:一起|打包|随货).{0,8}(?:发|寄|送)"),
)
CAPABILITY_CAVEAT_PATTERN = re.compile(
    r"(?:不确定|未确认|未核实|不能承诺|需要核实|需核实|是否|以订单为准)"
)
EDUCATION_RESOURCE_PATTERN = re.compile(
    r"(?:课程|教程|视频|社群|群指导|一对一|养护教学)"
)
AFFIRMATIVE_RESOURCE_CLAIM_PATTERN = re.compile(
    r"(?:配套|包含|提供|赠送|送您|开通|都有|也有|有详细|有专门|可以看|能看)"
)
PRODUCT_RECOMMENDATION_REQUEST_MARKERS = (
    "推荐",
    "哪款",
    "哪种",
    "想找",
    "想要一款",
)
PRODUCT_DOCUMENT_SECTION_MARKERS = (
    "产品基础",
    "产品介绍",
    "品种介绍",
    "交易认知",
    "商品",
    "卖点",
    "优势",
)
PRODUCT_CLAIM_PATTERN = re.compile(
    r"(?:推荐|建议选|可以看看).{0,30}(?:兰|商品|这款|该款)"
    r"|(?:这款|该款|商品卡|购买链接|下单)"
)
SKU_CLAIM_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*元|售价|价格|库存|现货")
ORDER_CLAIM_PATTERN = re.compile(
    r"(?:订单|物流|快递|付款|支付).{0,24}(?:成功|完成|已|已经|未|没有|同步|发出|到达)"
)
CARE_PAIN_MARKERS = (
    "烂根",
    "空根",
    "黑根",
    "腐苗",
    "黄叶",
    "焦尖",
    "黑斑",
    "不开花",
    "养死",
    "反复",
)
REGIONAL_ENVIRONMENT_CLAIM_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{2,6}(?:现在|目前|这边|当地)(?:的)?"
    r"(?:气候|空气|湿度|气温|温度).{0,10}"
    r"(?:干燥|潮湿|闷热|湿度大|湿度高|湿度低|多雨|"
    r"偏高|很高|较高|高|偏低|很低|较低|低|炎热|寒冷|热|冷)"
)
ENVIRONMENT_ASSERTION_PATTERN = re.compile(
    r"(?:天气|气候|空气|湿度|气温|温度).{0,10}"
    r"(?:干燥|潮湿|闷热|湿度大|湿度高|湿度低|多雨|"
    r"偏高|很高|较高|高|偏低|很低|较低|低|炎热|寒冷|热|冷)"
)
PROFILE_LOCATION_PATTERN = re.compile(
    r"(?:客户)?(?:在|来自)([\u4e00-\u9fff]{2,8})(?=[，,。；;\s]|$)"
)
TRAILING_CARE_QUESTION_PATTERN = re.compile(
    r"([^。！!\n]{2,80}(?:"
    r"[？?]|吗(?:[啊呢呀吧])?|呢|什么|怎么|如何|为什么|多少|"
    r"哪(?:个|些|种|款|里|儿)?|是.{1,16}还是.{1,16}"
    r"))\s*$"
)


PROMPT_TEMPLATE = """
# 角色

你是一名顶尖的私域销售客服，专注于兰花养护与兰花销售转化。

# 任务

读取【参考资料】与【用户问题】，结合资料内容，生成一段可以直接发送给用户的客服回复。

## 参考资料

{context}

## 用户问题

{question}

# 输出要求

只输出最终客服回复，不要输出分析过程、判断依据、规则解释、标题或多余说明，如果最终判断结果为需要转人工，选择不进行回答。

# 回答原则

1. 必须优先参考【参考资料】回答，综合资料内容生成自然、温和、可执行的回复，不要机械照搬原文。
2. 回复要像真实客服在私域里沟通：先回应用户关切，再给出判断、建议或下一步操作。
3. 可以给出养护方向、判断思路和操作建议，但不要承诺资料未明确支持的内容，包括具体天数、药量、疗效、库存、价格、赔付、退款、补发、售后结论或订单处理结果。
4. 不要出现“参考资料”“知识库”“资料显示”“根据资料”“系统判断”“推荐回复”“来源文件”“页码”“标签”“适用场景”等内部说明词。
5. 如果资料中有多个信息块，优先采用与用户问题最直接相关、内容更完整、非泛泛而谈、的内容。
6. 如果资料内容存在轻微差异，但能提炼共同原则，则先给出共同原则，避免武断下结论。
7. 信息不足、资料与问题无关、资料严重冲突或缺少关键判断信息时，不要追问或编造，只输出 __HANDOFF__。
8. __HANDOFF__ 是内部控制标记，不是客服话术，不得添加任何其他文字。

# 回答风格

1. 语气自然、真诚、温和，有私域客服的亲和感。
2. 表达清楚、直接，不说教，不制造焦虑。
3. 回复尽量简洁，一般控制在 1 到 3 句话。
4. 如果是复杂养护问题，可以适当分点说明，但不要过度展开。

# 回复格式

直接输出可发送给用户的完整客服话术。

不要加前缀。

不要加引号。

不要加编号，除非用户问题确实需要分步骤说明。

如果参考资料不足以可靠回答，只输出 __HANDOFF__。
"""


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": doc["doc_id"],
        "file_name": doc["file_name"],
        "page": doc.get("page"),
        "section": doc.get("section"),
        "score": doc.get("score"),
    }


def remove_unverified_capability_claims(
    answer: str,
    verified_capabilities: list[str] | None = None,
) -> str:
    """Remove business commitments that product/care RAG is not allowed to make."""

    parts = re.split(r"(?<=[。！？!?；;\n])", str(answer or ""))
    kept = []
    for part in parts:
        if not part.strip():
            continue
        resource_claim = bool(
            EDUCATION_RESOURCE_PATTERN.search(part)
            and AFFIRMATIVE_RESOURCE_CLAIM_PATTERN.search(part)
        )
        verified_claim = _matches_verified_capability_claim(
            part,
            verified_capabilities or [],
        )
        if (
            resource_claim
            and not verified_claim
            and not CAPABILITY_CAVEAT_PATTERN.search(part)
        ):
            clauses = re.split(r"(?<=[，,])", part)
            safe_clauses = [
                clause
                for clause in clauses
                if not (
                    EDUCATION_RESOURCE_PATTERN.search(clause)
                    and AFFIRMATIVE_RESOURCE_CLAIM_PATTERN.search(clause)
                )
            ]
            if safe_clauses:
                kept.append("".join(safe_clauses))
            continue
        unsupported = any(pattern.search(part) for pattern in UNVERIFIED_CAPABILITY_PATTERNS)
        if (
            unsupported
            and not verified_claim
            and not CAPABILITY_CAVEAT_PATTERN.search(part)
        ):
            continue
        kept.append(part)
    cleaned = "".join(kept).strip()
    return cleaned or "__HANDOFF__"


def _matches_verified_capability_claim(
    text: str,
    verified_capabilities: list[str],
) -> bool:
    if "萧岚苑" not in text:
        return False
    return any(
        capability in text
        or ("视频课程" in capability and any(term in text for term in ("视频", "课程")))
        or (
            "一对一指导" in capability
            and any(term in text for term in ("一对一", "老师带着", "老师指导"))
        )
        for capability in verified_capabilities
    )


def remove_disallowed_business_claims(
    answer: str,
    allowed_source_groups: set[str] | None,
) -> str:
    """Fail closed when RAG emits business facts outside its source contract."""

    if allowed_source_groups is None:
        return answer
    forbidden = []
    if "product_catalog" not in allowed_source_groups:
        forbidden.append(PRODUCT_CLAIM_PATTERN)
    if "sku_facts" not in allowed_source_groups:
        forbidden.append(SKU_CLAIM_PATTERN)
    if "order_facts" not in allowed_source_groups:
        forbidden.append(ORDER_CLAIM_PATTERN)
    if not forbidden:
        return answer
    parts = re.split(r"(?<=[。！？!?；;\n])", str(answer or ""))
    kept = [
        part
        for part in parts
        if part.strip() and not any(pattern.search(part) for pattern in forbidden)
    ]
    return "".join(kept).strip() or "__HANDOFF__"


def care_reply_violations(
    answer: str,
    *,
    message: str,
    context: ContextPackage | None,
) -> list[str]:
    """Return repairable care-copy violations grounded in current evidence."""

    if context is None:
        return []
    violations = []
    if _has_unsupported_regional_environment_claim(answer, context):
        violations.append("unsupported_regional_environment_claim")
    if _repeats_recent_follow_up(answer=answer, context=context):
        violations.append("repeated_follow_up_question")
    if _requires_brand_bridge(message=message, context=context) and not (
        _has_verified_brand_bridge(answer=answer, context=context)
    ):
        violations.append("missing_verified_brand_bridge")
    return violations


def _requires_brand_bridge(*, message: str, context: ContextPackage) -> bool:
    sales_action = context.session_state.get("sales_action")
    if (
        not isinstance(sales_action, dict)
        or sales_action.get("sales_action") != "discover_pain"
        or not sales_action.get("brand_value_facts")
        or not any(marker in message for marker in CARE_PAIN_MARKERS)
    ):
        return False
    return not any(
        isinstance(turn, dict)
        and str(turn.get("role") or "") == "assistant"
        and "萧岚苑" in str(turn.get("content") or "")
        for turn in context.recent_turns
    )


def _has_unsupported_regional_environment_claim(
    answer: str,
    context: ContextPackage,
) -> bool:
    if REGIONAL_ENVIRONMENT_CLAIM_PATTERN.search(answer):
        return True
    profile_text = " ".join(
        str(value)
        for value in context.profile_summary.values()
        if value not in (None, "", [], {})
    )
    locations = PROFILE_LOCATION_PATTERN.findall(profile_text)
    return any(
        re.search(
            rf"{re.escape(location)}.{{0,8}}{ENVIRONMENT_ASSERTION_PATTERN.pattern}",
            answer,
        )
        for location in locations
    )


def _repeats_recent_follow_up(
    *,
    answer: str,
    context: ContextPackage,
) -> bool:
    current = _trailing_care_question(answer)
    if not current:
        return False
    return any(
        isinstance(turn, dict)
        and str(turn.get("role") or "") == "assistant"
        and _trailing_care_question(str(turn.get("content") or "")) == current
        for turn in context.recent_turns
    )


def _trailing_care_question(text: str) -> str:
    match = TRAILING_CARE_QUESTION_PATTERN.search(str(text or "").strip())
    if match is None:
        return ""
    return re.sub(r"[\s，,。！？!?你您]", "", match.group(1))


def _has_verified_brand_bridge(*, answer: str, context: ContextPackage) -> bool:
    if "萧岚苑" not in answer:
        return False
    return _matches_verified_capability_claim(
        answer,
        _verified_service_capabilities(context),
    )


def _verified_service_capabilities(context: ContextPackage | None) -> list[str]:
    if context is None:
        return []
    sales_action = context.session_state.get("sales_action")
    facts = (
        sales_action.get("brand_value_facts")
        if isinstance(sales_action, dict)
        else []
    )
    return [
        str(capability)
        for fact in facts or []
        if isinstance(fact, dict)
        for capability in fact.get("service_capabilities", [])
        if str(capability).strip()
    ]


def _care_repair_prompt(prompt: str, violations: list[str]) -> str:
    instructions = []
    if "unsupported_regional_environment_claim" in violations:
        instructions.append(
            "删除所有仅凭城市或地区得出的干燥、潮湿、闷热、湿度或气候判断；"
            "只能使用客户明确说过的环境事实。"
        )
    if "missing_verified_brand_bridge" in violations:
        instructions.append(
            "在专业分析和安全建议之后，补一句自然的萧岚苑服务价值："
            "只能使用 Session state 中 brand_value_facts 已核实的视频课程或"
            "一对一指导，并说明它如何帮助客户减少反复试错；不要立即逼单。"
        )
    if "repeated_follow_up_question" in violations:
        instructions.append(
            "不要重复 Recent conversation 里已经问过、但客户尚未回答的追问；"
            "先根据客户本轮新增信息继续分析，必要时换成一个新的高信息量问题，"
            "否则不追问。"
        )
    return (
        f"{prompt}\n\n"
        "# 质检退回\n"
        "上一版未通过发送前质检，请完整重写一次，不要解释修改过程：\n"
        + "\n".join(f"- {instruction}" for instruction in instructions)
    )


def _finalize_repaired_care_answer(
    answer: str,
    *,
    message: str,
    context: ContextPackage | None,
) -> str:
    remaining = care_reply_violations(
        answer,
        message=message,
        context=context,
    )
    if "unsupported_regional_environment_claim" in remaining:
        answer = _remove_regional_environment_claims(answer, context)
        remaining = care_reply_violations(
            answer,
            message=message,
            context=context,
        )
    if (
        "missing_verified_brand_bridge" in remaining
        and context is not None
        and (bridge := _verified_brand_bridge(context))
    ):
        answer = f"{answer.rstrip()} {bridge}".strip()
    if "repeated_follow_up_question" in remaining:
        answer = _remove_trailing_care_question(answer)
    return answer or "__HANDOFF__"


def _remove_regional_environment_claims(
    answer: str,
    context: ContextPackage | None,
) -> str:
    parts = re.split(r"(?<=[。！？!?；;\n])", str(answer or ""))
    return "".join(
        part
        for part in parts
        if part.strip()
        and not (
            context is not None
            and _has_unsupported_regional_environment_claim(part, context)
        )
    ).strip()


def _verified_brand_bridge(context: ContextPackage) -> str:
    capabilities = _verified_service_capabilities(context)
    labels = []
    if any("视频课程" in capability for capability in capabilities):
        labels.append("系统视频课")
    if any("一对一指导" in capability for capability in capabilities):
        labels.append("针对具体问题的一对一指导")
    if not labels:
        return ""
    return (
        f"萧岚苑有{'和'.join(labels)}，"
        "能帮您少走些反复试错的弯路。"
    )


def _remove_trailing_care_question(answer: str) -> str:
    match = TRAILING_CARE_QUESTION_PATTERN.search(str(answer or "").strip())
    if match is None:
        return answer
    return answer[: match.start(1)].rstrip()


def _merge_usage(first: dict, second: dict) -> dict:
    result = dict(second or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [
            usage.get(key)
            for usage in (first or {}, second or {})
            if isinstance(usage.get(key), int)
        ]
        if values:
            result[key] = sum(values)
    return result


def _rag_model_purpose(
    policy: PolicyDecision | None,
    docs: list[dict[str, Any]],
) -> str:
    settings = get_settings()
    if not settings.reply_model_router_enabled:
        return "rag"
    retrieval_mode = (
        str(policy.retrieval_policy.get("mode") or "")
        if policy and isinstance(policy.retrieval_policy, dict)
        else ""
    )
    if retrieval_mode == "product_recommendation":
        return "rag"
    source_files = {
        str(doc.get("file_name") or doc.get("doc_id") or "")
        for doc in docs
        if doc.get("file_name") or doc.get("doc_id")
    }
    return "rag_fast" if len(source_files) <= 1 else "rag"


def _default_search_kb_ids(kb_id: str) -> list[str]:
    if kb_id == "kb_default":
        return [DEFAULT_ORCHID_KB_ID]
    ids = [kb_id]
    if DEFAULT_ORCHID_KB_ID not in ids:
        ids.append(DEFAULT_ORCHID_KB_ID)
    return ids


def select_care_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for doc in docs:
        if _is_sales_doc(doc) or _is_legacy_product_doc(doc):
            continue
        source_table = str(doc.get("source_table") or "").strip()
        if source_table in PRODUCT_SOURCE_TABLES:
            continue
        section = str(doc.get("section") or "")
        entity_type = str(doc.get("entity_type") or "")
        if (
            any(marker in section for marker in PRODUCT_DOCUMENT_SECTION_MARKERS)
            or "product" in entity_type.lower()
        ):
            continue
        searchable = " ".join(
            str(doc.get(key) or "")
            for key in ("section", "chunk_type", "entity_type", "text")
        )
        if not any(marker in searchable for marker in CARE_MARKERS):
            continue
        selected.append(doc)
    return selected


def _select_non_sales_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        doc
        for doc in docs
        if not _is_sales_doc(doc) and not _is_legacy_product_doc(doc)
    ]


def select_product_recommendation_docs(
    docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = []
    for doc in docs:
        section = str(doc.get("section") or "").strip().upper()
        if section.startswith(SALES_SECTION_PREFIXES):
            continue
        selected.append(doc)
    return selected


def _is_legacy_product_doc(doc: dict[str, Any]) -> bool:
    source_table = str(doc.get("source_table") or "").strip()
    return source_table.startswith("orchid_") and source_table != LEGACY_ORCHID_COMMON_TABLE


def _catalog_product_docs(
    products: list[dict[str, Any]],
    allowed_source_groups: set[str] | None = None,
) -> list[dict[str, Any]]:
    docs = []
    allowed = (
        {"product_catalog", "product_value", "sku_facts"}
        if allowed_source_groups is None
        else allowed_source_groups
    )
    for product in products:
        knowledge = product.get("knowledge")
        knowledge = knowledge if isinstance(knowledge, dict) else {}
        catalog_values = (
            ("产品名称", knowledge.get("product_name") or product.get("title")),
            ("产品别名", knowledge.get("aliases")),
            ("所属类别", knowledge.get("category")),
            ("花色", knowledge.get("flower_color")),
            ("香味", knowledge.get("fragrance")),
            ("是否带花", knowledge.get("flowering_status")),
            ("价格预算", knowledge.get("price_budget")),
            ("适合养护场景", knowledge.get("care_scenes")),
            ("花期", knowledge.get("bloom_period")),
            ("适合人群", knowledge.get("audience_tag")),
        )
        value_values = (
            ("突出特征", knowledge.get("highlighted_features")),
            ("塑品话术", knowledge.get("sales_copy")),
        )
        sku_values = (
            ("当前售价", _price_text(product.get("price_cent"))),
            ("当前库存", product.get("stock")),
        )
        values = (
            *(catalog_values if "product_catalog" in allowed else ()),
            *(value_values if "product_value" in allowed else ()),
            *(sku_values if "sku_facts" in allowed else ()),
        )
        if not values:
            continue
        item_id = str(product.get("item_id") or "").strip()
        docs.append(
            {
                "text": "\n".join(
                    f"{label}：{value}"
                    for label, value in values
                    if value not in (None, "")
                ),
                "kb_id": "product_catalog",
                "doc_id": f"product_catalog_{item_id}",
                "chunk_id": f"product_catalog_{item_id}",
                "file_name": "产品知识库",
                "file_type": "db",
                "page": None,
                "section": knowledge.get("product_name") or product.get("title"),
                "tenant_id": "tenant_default",
                "permission": "public",
                "score": 1.0,
                "source_table": "youzan_product_knowledge",
                "entity_type": "catalog_product",
                "item_id": item_id,
                "source_groups": sorted(
                    allowed & {"product_catalog", "product_value", "sku_facts"}
                ),
            }
        )
    return docs


def _price_text(value: Any) -> str:
    try:
        return f"¥{int(value) / 100:.2f}"
    except (TypeError, ValueError):
        return "以当前商品页为准"


def _is_sales_doc(doc: dict[str, Any]) -> bool:
    section = str(doc.get("section") or "").strip()
    return bool(
        section.upper().startswith(SALES_SECTION_PREFIXES)
        or doc.get("source_table") == "orchid_sales_copy"
        or doc.get("entity_type") == "sales_copy"
        or section.endswith("话术")
    )


def select_stage_allowed_docs(
    docs: list[dict[str, Any]],
    allowed_source_groups: set[str] | None,
) -> list[dict[str, Any]]:
    if allowed_source_groups is None:
        return docs
    selected = []
    for doc in docs:
        source_groups = doc.get("source_groups")
        if (
            isinstance(source_groups, list)
            and set(source_groups) & allowed_source_groups
        ):
            selected.append(doc)
            continue
        if _doc_source_group(doc) in allowed_source_groups:
            selected.append(doc)
    return selected


def _doc_source_group(doc: dict[str, Any]) -> str:
    source_table = str(doc.get("source_table") or "").strip()
    if source_table in PRODUCT_VALUE_SOURCE_TABLES or _is_sales_doc(doc):
        return "product_value"
    if source_table in PRODUCT_CATALOG_SOURCE_TABLES:
        return "product_catalog"
    if source_table in SKU_SOURCE_TABLES:
        return "sku_facts"
    if source_table in PROMOTION_SOURCE_TABLES:
        return "promotion"
    if source_table in {"service_sops", "unpurchased_sops"}:
        return "service_sop"
    return "care_safe"


def _requires_care_only_docs(
    message: str, policy: PolicyDecision | None
) -> bool:
    lowered = message.lower()
    if any(marker.lower() in lowered for marker in CARE_MARKERS):
        return True
    if policy is None:
        return False
    if any(
        block_id in {"scenario.orchid_care", "intent.orchid_problem"}
        for block_id in policy.prompt_block_ids
    ):
        return True
    focus = policy.retrieval_policy.get("focus", [])
    return any(item in {"symptoms", "safe_checks", "care_constraints"} for item in focus)


def _context(docs: list[dict[str, Any]]) -> str:
    blocks = []
    for index, doc in enumerate(docs, start=1):
        page = f"，第 {doc['page']} 页" if doc.get("page") else ""
        blocks.append(
            f"[{index}] 来源：{doc.get('file_name', '未知文件')}{page}\n"
            f"{doc.get('text', '')}"
        )
    return "\n\n".join(blocks)


def build_retrieval_question(
    question: str,
    context: ContextPackage | None,
    policy: PolicyDecision | None,
) -> str:
    if (
        policy is None
        or policy.retrieval_policy.get("mode") != "product_recommendation"
        or context is None
    ):
        return question.strip()
    previous = []
    for turn in context.recent_turns[-4:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            previous.append(f"{role}: {content[:300]}")
    if not previous:
        return question.strip()
    return "\n".join([*previous, f"user: {question.strip()}"])


async def build_rag_prompt(
    *,
    question: str,
    docs: list[dict[str, Any]],
    policy: PolicyDecision | None = None,
    context: ContextPackage | None = None,
    templates: list[str] | None = None,
) -> str:
    if policy is None:
        return PROMPT_TEMPLATE.format(context=_context(docs), question=question.strip())

    snippets = [
        {
            "source": doc.get("file_name", "unknown"),
            "text": doc.get("text", ""),
        }
        for doc in docs
    ]
    return await build_prompt(
        PromptBuildInput(
            prompt_block_ids=policy.prompt_block_ids,
            templates=templates or [],
            context=context or ContextPackage(),
            knowledge_snippets=snippets,
            user_message=question,
        )
    )


async def rag_chat(
    user_id: str,
    message: str,
    kb_id: str,
    session_id: str | None = None,
    channel: str = "api",
    metadata: dict | None = None,
    policy: PolicyDecision | None = None,
    context: ContextPackage | None = None,
    templates: list[str] | None = None,
) -> dict:
    started = time.perf_counter()
    request_id = generate_id("request")
    active_session_id = session_id or generate_id("session")
    metadata = metadata or {}
    status = "success"
    answer = ""
    sources: list[dict[str, Any]] = []
    stage_latencies = {
        "embedding_ms": 0,
        "search_ms": 0,
        "rerank_ms": 0,
        "prompt_ms": 0,
        "generation_ms": 0,
    }

    if not message or not message.strip():
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    try:
        settings = get_settings()
        if not settings.rag_knowledge_enabled:
            return {
                "answer": "",
                "sources": sources,
                "session_id": active_session_id,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "stage_latencies": stage_latencies,
            }

        retrieval_question = build_retrieval_question(message, context, policy)
        product_recommendation = bool(
            (
                policy
                and policy.retrieval_policy.get("mode") == "product_recommendation"
            )
            or any(marker in message for marker in PRODUCT_RECOMMENDATION_REQUEST_MARKERS)
        )
        vector = []
        if not product_recommendation:
            stage_started = time.perf_counter()
            vector = await embedding_service.embed_text(retrieval_question)
            stage_latencies["embedding_ms"] = round(
                (time.perf_counter() - stage_started) * 1000
            )
        knowledge_base_ids = policy.knowledge_base_ids if policy else []
        search_kb_ids = knowledge_base_ids or _default_search_kb_ids(kb_id)
        allowed_source_groups = (
            set(policy.retrieval_policy.get("allowed_source_groups", []))
            if policy and "allowed_source_groups" in policy.retrieval_policy
            else None
        )
        candidates = []
        stage_started = time.perf_counter()
        if product_recommendation:
            candidates = _catalog_product_docs(
                search_catalog_products(retrieval_question, limit=settings.rag_top_k),
                allowed_source_groups,
            )
        else:
            for search_kb_id in search_kb_ids:
                candidates.extend(
                    await qdrant_service.search_chunks(
                        vector,
                        kb_id=search_kb_id,
                        tenant_id=metadata.get("tenant_id", "tenant_default"),
                        permission=metadata.get("permission", "public"),
                        top_k=settings.rag_top_k,
                    )
                )
                candidates.extend(
                    await search_orchid_knowledge_chunks(
                        vector,
                        kb_id=search_kb_id,
                        top_k=settings.rag_top_k,
                    )
                )
        stage_latencies["search_ms"] = round(
            (time.perf_counter() - stage_started) * 1000
        )
        stage_started = time.perf_counter()
        if product_recommendation:
            filtered_candidates = select_product_recommendation_docs(candidates)
        elif _requires_care_only_docs(message, policy):
            filtered_candidates = select_care_docs(candidates)
        else:
            filtered_candidates = _select_non_sales_docs(candidates)
        filtered_candidates = select_stage_allowed_docs(
            filtered_candidates,
            allowed_source_groups,
        )
        docs = await rerank_service.rerank(
            retrieval_question, filtered_candidates, settings.rag_top_n
        )
        stage_latencies["rerank_ms"] = round(
            (time.perf_counter() - stage_started) * 1000
        )
        sources = [_source(doc) for doc in docs]
        if docs:
            stage_started = time.perf_counter()
            prompt = await build_rag_prompt(
                question=message.strip(),
                docs=docs,
                policy=policy,
                context=context,
                templates=templates,
            )
            stage_latencies["prompt_ms"] = round(
                (time.perf_counter() - stage_started) * 1000
            )
            stage_started = time.perf_counter()
            model_purpose = _rag_model_purpose(policy, docs)
            result = await llm_service.generate_answer(
                prompt,
                purpose=model_purpose,
            )
            verified_capabilities = _verified_service_capabilities(context)
            answer = remove_unverified_capability_claims(
                result["answer"],
                verified_capabilities,
            )
            answer = remove_disallowed_business_claims(
                answer,
                allowed_source_groups,
            )
            violations = care_reply_violations(
                answer,
                message=message,
                context=context,
            )
            if violations:
                repaired = await llm_service.generate_answer(
                    _care_repair_prompt(prompt, violations),
                    purpose=model_purpose,
                )
                answer = remove_unverified_capability_claims(
                    repaired["answer"],
                    verified_capabilities,
                )
                answer = remove_disallowed_business_claims(
                    answer,
                    allowed_source_groups,
                )
                answer = _finalize_repaired_care_answer(
                    answer,
                    message=message,
                    context=context,
                )
                result["usage"] = _merge_usage(
                    result.get("usage", {}),
                    repaired.get("usage", {}),
                )
            stage_latencies["generation_ms"] = round(
                (time.perf_counter() - stage_started) * 1000
            )
            usage = result.get("usage", {})
        else:
            answer = ""
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
        return {
            "answer": answer,
            "sources": sources,
            "session_id": active_session_id,
            "usage": usage,
            "stage_latencies": stage_latencies,
        }
    except Exception:
        status = "failed"
        raise
    finally:
        log_event(
            {
                "request_id": request_id,
                "channel": channel,
                "user_id": user_id,
                "session_id": active_session_id,
                "kb_id": kb_id,
                "policy_kb_ids": policy.knowledge_base_ids if policy else [],
                "question": message,
                "answer": answer,
                "sources": sources,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "status": status,
                "created_at": now_iso(),
            }
        )


async def answer_knowledge(message, user_state, policy_decision: PolicyDecision | None = None) -> dict:
    context = None
    if policy_decision is not None:
        context = await select_context(
            ContextSelectionInput(
                profile=user_state.metadata.get("profile", {}),
                state=user_state.model_dump(),
                memories=user_state.metadata.get("recent_turns", []),
                memory_context=user_state.metadata.get("memory_v2_context", {}),
                context_policy=policy_decision.context_policy,
            )
        )
    policy_kb_id = (
        policy_decision.knowledge_base_ids[0]
        if policy_decision and policy_decision.knowledge_base_ids
        else message.kb_id
    )
    result = await rag_chat(
        user_id=message.user_id,
        message=message.message,
        kb_id=policy_kb_id,
        session_id=message.session_id,
        channel=message.channel,
        metadata={
            **message.metadata,
            "tenant_id": message.tenant_id,
            "permission": message.permission,
        },
        policy=policy_decision,
        context=context,
        templates=policy_decision.template_ids if policy_decision else [],
    )
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "usage": result.get("usage", {}),
        "stage_latencies": result.get("stage_latencies", {}),
    }
