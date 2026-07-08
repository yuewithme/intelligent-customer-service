from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.services.reply_builder import build_template_reply
from app.services.template_service import render_template, select_template


async def build_default_template_reply(
    message: NormalizedMessage,
    intent: IntentResult,
    user_state: UserState,
) -> FinalReply | None:
    template = await select_template(message, intent, user_state)
    if template is None:
        return None
    template_reply = await render_template(template, message, user_state)
    return build_template_reply(template_reply, intent)
