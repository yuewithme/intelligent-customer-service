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
    del intent
    return FinalReply(
        answer="我理解你的意思，不过想确认一下，你主要是想了解价格、养护方法、发货售后，还是想直接咨询人工？",
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
