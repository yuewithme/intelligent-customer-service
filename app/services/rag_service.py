import time
from typing import Any

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.services import (
    embedding_service,
    llm_service,
    qdrant_service,
    rerank_service,
)
from app.utils.ids import generate_id
from app.utils.logger import log_event
from app.utils.time import now_iso


PROMPT_TEMPLATE = """你是兰花/蝴蝶兰私域客服的知识库问答助手。

你的任务是：只根据【知识库资料】回答【用户问题】，生成一段客服可以直接发送给用户的话。

【知识库资料】
{context}

【用户问题】
{question}

【回答原则】

1. 参考【知识库资料】回答，组合生成自然、温和、可执行的客服回答。
2. 综合生成时只能做原则性建议，不要承诺资料没有支持的具体天数、药量、疗效、库存、价格、赔付、退款、补发、售后结论或订单处理结果。
3. 优先使用资料块中的【推荐回复】或“推荐回复”字段内容；如果推荐回复完整、直接、适合用户问题，应优先改写为自然客服话术。
4. 如果推荐回复包含省略号、明显断句、内容不完整，或明显偏离用户问题，不要照抄，应优先参考其他完整内容综合回答。
5. 不要复述资料块中的标题、知识类型、适用场景、标签、下一步动作、来源文件、页码等内部信息。
6. 回答正文不要出现“知识库”“资料”“来源”“推荐回复”“根据资料”“系统判断”“暂按综合回应”等内部说明词。
7. 如果多个资料块内容冲突，优先选择与用户问题最直接相关、内容更完整、非销售推进、非泛泛而谈的资料。
8. 如果问题涉及养护判断，应在回答中适当说明边界条件，例如需要结合根系状态、叶片状态、植料干湿、通风、光照、温度、季节或现场情况判断；不确定时用“建议再确认一下……”表达。
9. 如果问题涉及图片、订单、具体药剂名称、物流、退款、赔付、售后责任等关键信息，而资料无法支持明确判断，不要自行推断，转人工处理。
10. 如果资料明显低相关、资料之间冲突且无法判断、或必须依赖缺失的关键信息才能回答，请转人工处理。
11. 只输出最终客服回复，不要输出分析过程、判断依据、规则解释或多余内容。

【回答风格】

1. 语气像真实客服，先回应用户关切，再给出建议。
2. 表达清楚、直接、温和，不要生硬说教。
3. 不要输出与用户问题无关的内容。
4. 能综合回答时，不要说“知识库中没有找到明确答案”。
5. 回答尽量简洁，一般控制在 1 到 3 段；复杂养护问题可以适当分点，但不要过度展开。
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


async def rag_chat(
    user_id: str,
    message: str,
    kb_id: str,
    session_id: str | None = None,
    channel: str = "api",
    metadata: dict | None = None,
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
        candidates = await qdrant_service.search_chunks(
            vector,
            kb_id=kb_id,
            tenant_id=metadata.get("tenant_id", "tenant_default"),
            permission=metadata.get("permission", "public"),
            top_k=settings.rag_top_k,
        )
        docs = await rerank_service.rerank(
            message.strip(), candidates, settings.rag_top_n
        )
        sources = [_source(doc) for doc in docs]
        if docs:
            result = await llm_service.generate_answer(
                PROMPT_TEMPLATE.format(
                    context=_context(docs),
                    question=message.strip(),
                )
            )
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
                "question": message,
                "answer": answer,
                "sources": sources,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "status": status,
                "created_at": now_iso(),
            }
        )


async def answer_knowledge(message, user_state) -> dict:
    del user_state
    result = await rag_chat(
        user_id=message.user_id,
        message=message.message,
        kb_id=message.kb_id,
        session_id=message.session_id,
        channel=message.channel,
        metadata={
            **message.metadata,
            "tenant_id": message.tenant_id,
            "permission": message.permission,
        },
    )
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "usage": result.get("usage", {}),
    }
