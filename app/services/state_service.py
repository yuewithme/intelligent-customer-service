from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState


_state_store: dict[str, UserState] = {}
_allowed_update_fields = {
    "sales_stage",
    "customer_tags",
    "interested_products",
    "order_status",
    "risk_level",
    "metadata",
}


def _key(user_id: str) -> str:
    return user_id


async def get_user_state(user_id: str, session_id: str | None = None) -> UserState:
    state = _state_store.get(_key(user_id))
    if state is None:
        state = UserState(user_id=user_id, session_id=session_id)
        _state_store[_key(user_id)] = state
    elif session_id and not state.session_id:
        state.session_id = session_id
    return state


async def update_user_state(
    user_id: str,
    session_id: str,
    intent: IntentResult,
    reply: FinalReply,
) -> None:
    state = await get_user_state(user_id, session_id)
    state.session_id = session_id
    state.last_intent = intent.primary_intent
    state.last_route = reply.route
    state.last_template_id = reply.template_id
    state.last_bot_message = reply.answer
    if intent.sales_stage != "unknown":
        state.sales_stage = intent.sales_stage
    if reply.need_human:
        state.risk_level = "elevated"


async def patch_user_state(user_id: str, updates: dict) -> UserState:
    state = await get_user_state(user_id)
    for field, value in updates.items():
        if field in _allowed_update_fields:
            setattr(state, field, value)
    return state
