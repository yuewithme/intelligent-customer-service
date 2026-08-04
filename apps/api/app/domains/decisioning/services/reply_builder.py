from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.core.config import get_settings
from app.domains.decisioning.schemas.template import TemplateReply


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
    opening_text = settings.eyun_opening_text.strip()
    followup_text = settings.eyun_opening_followup_text.strip()
    answer_segments = [text for text in (opening_text, followup_text) if text]
    outbound_messages = [{"type": "text", "content": opening_text}]
    if settings.eyun_opening_material_id and settings.eyun_opening_image_url:
        outbound_messages.append(
            {
                "type": "image",
                "content": settings.eyun_opening_image_url,
                "material_id": settings.eyun_opening_material_id,
            }
        )
    elif settings.eyun_opening_material_id:
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
    if followup_text:
        outbound_messages.append({"type": "text", "content": followup_text})
    return FinalReply(
        answer="\n\n".join(answer_segments),
        answer_segments=answer_segments,
        outbound_messages=outbound_messages,
        reply_type="chitchat",
        route="chitchat",
    )

def build_chitchat_reply(intent: IntentResult) -> FinalReply:
    if intent.primary_goal == "end_conversation":
        return FinalReply(
            answer="好的，随时找我。",
            reply_type="chitchat",
            route="chitchat",
        )
    if intent.slots.get("chitchat_kind") == "thanks":
        return FinalReply(
            answer="不客气。",
            reply_type="chitchat",
            route="chitchat",
        )
    if intent.slots.get("chitchat_kind") == "identity_question":
        return FinalReply(
            answer="我是兰花在线顾问，有什么可以帮您？",
            reply_type="chitchat",
            route="chitchat",
        )
    if intent.primary_intent == "profile_answer":
        return FinalReply(
            answer=_profile_answer_acknowledgement(intent.slots),
            reply_type="chitchat",
            route="chitchat",
        )
    if intent.primary_intent in {"unknown", "unsupported"}:
        return FinalReply(
            answer="没关系，您接着说就行，我继续帮您。",
            reply_type="chitchat",
            route="chitchat",
        )
    return FinalReply(
        answer="您好，我在的。",
        reply_type="chitchat",
        route="chitchat",
    )


def _profile_answer_acknowledgement(slots: dict) -> str:
    region = str(slots.get("region") or "").strip()
    plant_count = slots.get("plant_count")
    varieties = slots.get("owned_varieties")
    variety_text = "、".join(
        str(item).strip()
        for item in (varieties if isinstance(varieties, list) else [])
        if str(item).strip()
    )
    if region and plant_count not in (None, "") and variety_text:
        return f"了解了，您在{region}养了{plant_count}盆{variety_text}。"
    if plant_count not in (None, "") and variety_text:
        return f"了解了，您现在养了{plant_count}盆{variety_text}。"
    if region and variety_text:
        return f"了解了，您在{region}主要养{variety_text}。"
    if plant_count not in (None, ""):
        return f"了解了，您现在养了{plant_count}盆兰花。"
    if variety_text:
        return f"了解了，您现在主要养{variety_text}。"
    return "好的，已经记下了。"
