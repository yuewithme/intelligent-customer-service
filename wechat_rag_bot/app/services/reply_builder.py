from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.template import TemplateReply


def build_template_reply(
    template_reply: TemplateReply, intent: IntentResult
) -> FinalReply:
    return FinalReply(
        answer=template_reply.answer,
        reply_type="template",
        route="template_reply",
        template_id=template_reply.template_id,
        next_action=template_reply.next_action,
        metadata={"intent": intent.primary_intent, "score": template_reply.score},
    )


def build_rag_reply(rag_result: dict, intent: IntentResult) -> FinalReply:
    del intent
    return FinalReply(
        answer=rag_result.get("answer", ""),
        reply_type="rag",
        route="rag_answer",
        sources=rag_result.get("sources", []),
        usage=rag_result.get("usage", {}),
    )


def build_template_then_rag_reply(
    template_reply: TemplateReply,
    rag_result: dict,
    intent: IntentResult,
) -> FinalReply:
    del intent
    rag_answer = rag_result.get("answer", "")
    answer = template_reply.answer
    if rag_answer:
        answer = f"{template_reply.answer}\n\n补充参考：{rag_answer}"
    return FinalReply(
        answer=answer,
        reply_type="template_then_rag",
        route="template_then_rag",
        template_id=template_reply.template_id,
        sources=rag_result.get("sources", []),
        usage=rag_result.get("usage", {}),
        next_action=template_reply.next_action,
    )


def build_clarify_reply(intent: IntentResult) -> FinalReply:
    if intent.primary_intent in {"care_question", "knowledge_question"}:
        answer = "我先帮您缩小排查范围。现在主要是黄叶、烂根、黑斑，还是不开花？"
    elif intent.primary_intent in {"order_intent", "ask_price", "price_objection"}:
        answer = "可以的，我先按您的情况帮您缩小范围。您更看重好养、花香，还是预算合适？"
    else:
        answer = "我先确认一下，您现在最想解决的是哪一个具体问题？可以直接说当前情况或想了解的内容。"
    return FinalReply(
        answer=answer,
        reply_type="clarify",
        route="clarify",
    )


def build_human_reply(intent: IntentResult) -> FinalReply:
    return FinalReply(
        answer="我这边先帮你记录一下，马上为你转人工处理。为了更快帮你解决，可以把订单号或具体问题发我一下。",
        reply_type="human",
        route="human",
        need_human=True,
        metadata={"intent": intent.primary_intent},
    )


def build_chitchat_reply(intent: IntentResult) -> FinalReply:
    del intent
    return FinalReply(
        answer="你好，我在的。你可以直接问产品、价格、养护、发货或售后问题。",
        reply_type="chitchat",
        route="chitchat",
    )


def build_unsupported_reply(intent: IntentResult) -> FinalReply:
    del intent
    return FinalReply(
        answer="这个问题我暂时无法准确处理，我可以先帮你转人工确认。",
        reply_type="unsupported",
        route="unsupported",
    )
