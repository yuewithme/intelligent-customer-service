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


def remove_unverified_capability_claims(answer: str) -> str:
    """Remove business commitments that product/care RAG is not allowed to make."""

    parts = re.split(r"(?<=[。！？!?；;\n])", str(answer or ""))
    kept = []
    for part in parts:
        if not part.strip():
            continue
        unsupported = any(pattern.search(part) for pattern in UNVERIFIED_CAPABILITY_PATTERNS)
        if unsupported and not CAPABILITY_CAVEAT_PATTERN.search(part):
            continue
        kept.append(part)
    cleaned = "".join(kept).strip()
    return cleaned or "__HANDOFF__"


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
        if policy and policy.retrieval_policy.get("mode") == "product_recommendation":
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
        if policy and policy.retrieval_policy.get("mode") == "product_recommendation":
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
            result = await llm_service.generate_answer(
                prompt,
                purpose=_rag_model_purpose(policy, docs),
            )
            stage_latencies["generation_ms"] = round(
                (time.perf_counter() - stage_started) * 1000
            )
            answer = remove_unverified_capability_claims(result["answer"])
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
