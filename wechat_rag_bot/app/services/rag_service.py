import time
from typing import Any

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.schemas.context import ContextPackage, ContextSelectionInput
from app.schemas.policy import PolicyDecision
from app.schemas.prompt import PromptBuildInput
from app.services import (
    embedding_service,
    llm_service,
    qdrant_service,
    rerank_service,
)
from app.services.context_selector import select_context
from app.services.prompt_builder import build_prompt
from app.utils.ids import generate_id
from app.utils.logger import log_event
from app.utils.time import now_iso


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
8. 在明显觉察到用户问题信息缺失，需要进一步确认或补充信息时，需要转人工处理，不进行回答。
9. 如果资料明显与用户问题无关、资料冲突严重且无法提炼共同原则，或缺少关键判断信息，需温和说明需要用户补充信息，或建议转人工进一步确认。

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
"""


LLM_FALLBACK_PROMPT_TEMPLATE = """我先按通用养护原则给你一个参考。

你是兰花私域客服助手。当前不要调用本地知识库，也不要写来源。
请直接回答用户问题，语气自然、温和，像客服在微信里回复客户。

要求：
1. 可以基于通用兰花养护原则给建议，但不要编造具体商品、库存、价格、订单、赔付或药剂疗效。
2. 涉及图片判断、病害严重程度、药剂搭配、售后赔付、订单物流等关键信息不足时，要温和说明需要进一步确认或转人工。
3. 不要在回答正文或结尾写“来源”，不要提“知识库”“资料”“推荐回复”。
4. 回答要简洁可执行，尽量说明边界条件，例如根系状态、植料干湿、通风、光照、季节或现场情况。

用户问题：
{question}
"""


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": doc["doc_id"],
        "file_name": doc["file_name"],
        "page": doc.get("page"),
        "section": doc.get("section"),
        "score": doc.get("score"),
    }


def _context(docs: list[dict[str, Any]]) -> str:
    blocks = []
    for index, doc in enumerate(docs, start=1):
        page = f"，第 {doc['page']} 页" if doc.get("page") else ""
        blocks.append(
            f"[{index}] 来源：{doc.get('file_name', '未知文件')}{page}\n"
            f"{doc.get('text', '')}"
        )
    return "\n\n".join(blocks)


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

    if not message or not message.strip():
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    try:
        settings = get_settings()
        if not settings.rag_knowledge_enabled:
            result = await llm_service.generate_answer(
                LLM_FALLBACK_PROMPT_TEMPLATE.format(question=message.strip())
            )
            answer = result["answer"]
            usage = result.get("usage", {})
            return {
                "answer": answer,
                "sources": sources,
                "session_id": active_session_id,
                "usage": usage,
            }

        vector = await embedding_service.embed_text(message.strip())
        knowledge_base_ids = policy.knowledge_base_ids if policy else []
        search_kb_ids = knowledge_base_ids or [kb_id]
        candidates = []
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
        docs = await rerank_service.rerank(
            message.strip(), candidates, settings.rag_top_n
        )
        sources = [_source(doc) for doc in docs]
        if docs:
            prompt = await build_rag_prompt(
                question=message.strip(),
                docs=docs,
                policy=policy,
                context=context,
                templates=templates,
            )
            result = await llm_service.generate_answer(prompt)
            answer = result["answer"]
            usage = result.get("usage", {})
        else:
            answer = "知识库中没有找到明确答案。"
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
        return {
            "answer": answer,
            "sources": sources,
            "session_id": active_session_id,
            "usage": usage,
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
    }
