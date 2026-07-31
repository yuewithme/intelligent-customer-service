import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.customers.schemas.state import UserState
from app.domains.sales.services.sales_action_service import evolve_opportunity
from app.domains.sales.services.tag_catalog import (
    filter_profile_tags,
    normalize_system_value,
)
from app.infrastructure.database.models import Base, OrderWorkflowStateModel


_state_store: dict[str, UserState] = {}
_state_sessionmakers: dict[str, sessionmaker] = {}
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
        state.metadata.update(_load_order_workflow(user_id))
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
    state.last_intent = normalize_system_value(
        "intent", intent.primary_intent, fallback="unknown"
    )
    state.last_route = reply.route
    state.last_template_id = reply.template_id
    state.last_bot_message = reply.answer
    sales_stage = normalize_system_value(
        "sales_stage", intent.sales_stage, fallback="unknown"
    )
    if sales_stage != "unknown":
        state.sales_stage = sales_stage
    if reply.need_human:
        state.risk_level = normalize_system_value(
            "risk_level", "elevated", fallback="normal"
        )
    tag_result = reply.metadata.get("tag_result")
    if isinstance(tag_result, dict):
        labels = tag_result.get("labels")
        if isinstance(labels, list):
            incoming_tags = [
                label.split(":", 1)[1]
                for label in labels
                if isinstance(label, str) and label.startswith("customer_tag:")
            ]
            state.customer_tags = _merge_strings(
                filter_profile_tags(state.customer_tags),
                filter_profile_tags(incoming_tags),
            )
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
            stage_decision=_dict_value(reply.metadata.get("sales_stage_decision")),
        )
    _persist_order_workflow(state)


async def patch_user_state(user_id: str, updates: dict) -> UserState:
    state = await get_user_state(user_id)
    for field, value in updates.items():
        if field not in _allowed_update_fields:
            continue
        if field == "sales_stage":
            value = normalize_system_value("sales_stage", value, fallback="unknown")
        elif field == "risk_level":
            value = normalize_system_value("risk_level", value, fallback="normal")
        elif field == "customer_tags":
            value = filter_profile_tags(value if isinstance(value, list) else [])
        setattr(state, field, value)
    _persist_order_workflow(state)
    return state


def replace_customer_tag(old_value: str, new_value: str) -> None:
    for state in _state_store.values():
        state.customer_tags = _replace_tag_values(
            state.customer_tags, {old_value}, new_value
        )


def remove_customer_tags(values: set[str]) -> None:
    for state in _state_store.values():
        state.customer_tags = _replace_tag_values(state.customer_tags, values, None)


def replace_system_tag(category_id: str, old_value: str, new_value: str | None) -> None:
    prefix_by_category = {
        "intent": "intent:",
        "sales_stage": "stage:",
        "risk_level": "risk:",
        "product_interest": "product_interest:",
    }
    prefix = prefix_by_category.get(category_id)
    if not prefix or not old_value.startswith(prefix):
        return
    old_raw = old_value[len(prefix):]
    new_raw = new_value[len(prefix):] if new_value and new_value.startswith(prefix) else ""
    for state in _state_store.values():
        if category_id == "intent" and state.last_intent == old_raw:
            state.last_intent = new_raw or "unknown"
        elif category_id == "sales_stage" and state.sales_stage == old_raw:
            state.sales_stage = new_raw or "unknown"
        elif category_id == "risk_level" and state.risk_level == old_raw:
            state.risk_level = new_raw or "normal"
        elif category_id == "product_interest":
            state.interested_products = _replace_tag_values(
                state.interested_products, {old_raw}, new_raw or None
            )


def _dict_value(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _merge_strings(existing: list[str], incoming: list) -> list[str]:
    result = list(existing)
    for value in incoming:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result


def _replace_tag_values(
    tags: list[str], old_values: set[str], replacement: str | None
) -> list[str]:
    result: list[str] = []
    for tag in tags:
        prefix = "customer_tag:" if tag.startswith("customer_tag:") else ""
        value = tag.split(":", 1)[1] if prefix else tag
        if value in old_values:
            value = replacement or ""
        normalized = f"{prefix}{value}" if value else ""
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _state_session():
    settings = get_settings()
    factory = _state_sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(engine, tables=[OrderWorkflowStateModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _state_sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _load_order_workflow(user_id: str) -> dict:
    try:
        with _state_session() as session:
            row = session.get(OrderWorkflowStateModel, user_id)
            if row is None:
                return {}
            payload = json.loads(row.state_json or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def _persist_order_workflow(state: UserState) -> None:
    payload = _order_workflow_payload(state.metadata)
    if not payload:
        return
    active_task = payload.get("active_task")
    status = (
        str(active_task.get("status") or "")
        if isinstance(active_task, dict)
        else ""
    ) or (
        "awaiting_identity"
        if payload.get("commerce_pending") == "order_mobile"
        else "active"
    )
    now = datetime.now(timezone.utc)
    try:
        with _state_session() as session:
            row = session.get(OrderWorkflowStateModel, state.user_id)
            if row is None:
                row = OrderWorkflowStateModel(
                    user_id=state.user_id,
                    session_id=state.session_id,
                    status=status,
                    state_json="{}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            row.session_id = state.session_id
            row.status = status
            row.state_json = json.dumps(payload, ensure_ascii=False)
            row.updated_at = now
            session.commit()
    except Exception:  # noqa: BLE001
        return


def _order_workflow_payload(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        return {}
    payload = {}
    if metadata.get("commerce_pending") == "order_mobile":
        payload["commerce_pending"] = "order_mobile"
    active_task = metadata.get("active_task")
    if isinstance(active_task, dict) and active_task.get("domain") == "order":
        payload["active_task"] = {
            key: value
            for key, value in active_task.items()
            if key
            in {
                "domain",
                "task_type",
                "action",
                "status",
                "requested_action",
                "last_result_status",
                "last_queried_at",
            }
        }
    last_result = metadata.get("commerce_last_order_result")
    if isinstance(last_result, dict):
        payload["commerce_last_order_result"] = last_result
    return payload
