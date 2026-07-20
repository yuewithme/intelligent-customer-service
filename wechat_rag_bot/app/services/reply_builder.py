from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.config import get_settings
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


def build_opening_reply() -> FinalReply:
    settings = get_settings()
    answer = settings.eyun_opening_text
    outbound_messages = [{"type": "text", "content": answer}]
    if settings.eyun_opening_material_id:
        outbound_messages.append(
            {
                "type": "material",
                "content": "[开场白图片]",
                "material_id": settings.eyun_opening_material_id,
            }
        )
    elif settings.eyun_opening_image_url:
        outbound_messages.append(
            {"type": "image", "content": settings.eyun_opening_image_url}
        )
    return FinalReply(
        answer=answer,
        outbound_messages=outbound_messages,
        reply_type="chitchat",
        route="chitchat",
    )

def build_chitchat_reply(intent: IntentResult) -> FinalReply:
    if intent.primary_intent == "profile_answer":
        return FinalReply(
            answer=(
                "收到，已经记下您的养兰规模和主要品种。"
                "您现在最想解决哪一类养护问题？"
            ),
            reply_type="chitchat",
            route="chitchat",
        )
    return FinalReply(
        answer="你好，我在的。你可以直接问产品、价格、养护、发货或售后问题。",
        reply_type="chitchat",
        route="chitchat",
    )
