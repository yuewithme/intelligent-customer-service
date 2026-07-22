from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.reply_builder import build_template_reply
from app.domains.decisioning.services.template_service import render_template, select_template


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
