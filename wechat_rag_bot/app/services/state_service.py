from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.schemas.state import UserState
from app.services.sales_action_service import evolve_opportunity


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
    tag_result = reply.metadata.get("tag_result")
    if isinstance(tag_result, dict):
        labels = tag_result.get("labels")
        if isinstance(labels, list):
            state.customer_tags = _merge_strings(state.customer_tags, labels)
        entities = tag_result.get("entities")
        if isinstance(entities, dict):
            state.metadata["known_slots"] = {
                **_dict_value(state.metadata.get("known_slots")),
                **entities,
            }
    sales_action = reply.metadata.get("sales_action")
    if isinstance(sales_action, dict):
        action = dict(sales_action)
        action["known_slots"] = {
            **_dict_value(state.metadata.get("known_slots")),
            **intent.slots,
            **_dict_value(action.get("known_slots")),
        }
        state.metadata["active_opportunity"] = evolve_opportunity(
            _dict_value(state.metadata.get("active_opportunity")),
            sales_stage=intent.sales_stage,
            sales_action=action,
        )


async def patch_user_state(user_id: str, updates: dict) -> UserState:
    state = await get_user_state(user_id)
    for field, value in updates.items():
        if field in _allowed_update_fields:
            setattr(state, field, value)
    return state


def _dict_value(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _merge_strings(existing: list[str], incoming: list) -> list[str]:
    result = list(existing)
    for value in incoming:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result
