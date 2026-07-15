import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.state import UserState
from app.services.intent_service import classify_intent
from app.services.shipping_contact_service import extract_shipping_contact
from app.services.template_reply_service import build_default_template_reply


HANGZHOU_MESSAGE = (
    "我的地址是：杭州市滨江区春风大厦四栋，黄贵杰，"
    "13297892233，给我安排发货"
)
URUMQI_MESSAGE = "收件地址，新疆乌鲁木齐市新市区百园路88号通嘉一期，温家军15099657718"


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_contact",
        channel="wechat",
        user_id="customer",
        session_id="session",
        message=text,
        kb_id="kb_default",
    )


def test_extracts_structured_shipping_contact_from_natural_messages():
    assert extract_shipping_contact(HANGZHOU_MESSAGE) == {
        "recipient_name": "黄贵杰",
        "mobile": "13297892233",
        "shipping_address": "杭州市滨江区春风大厦四栋",
        "shipping_city": "杭州市",
    }
    assert extract_shipping_contact(URUMQI_MESSAGE) == {
        "recipient_name": "温家军",
        "mobile": "15099657718",
        "shipping_address": "新疆乌鲁木齐市新市区百园路88号通嘉一期",
        "shipping_city": "新疆乌鲁木齐市",
    }
    assert extract_shipping_contact("13297892233") == {}
    assert extract_shipping_contact(
        "13297892233",
        allow_mobile_only=True,
    ) == {"mobile": "13297892233"}


@pytest.mark.asyncio
async def test_complete_address_routes_to_order_confirmation_before_logistics():
    intent = await classify_intent(
        _message(HANGZHOU_MESSAGE),
        UserState(user_id="customer", session_id="session"),
    )

    assert intent.primary_intent == "order_intent"
    assert intent.route == "template_reply"
    assert intent.reason == "structured_shipping_contact"


@pytest.mark.asyncio
async def test_confirmation_uses_known_contact_without_reasking_or_exposing_mobile():
    message = _message(HANGZHOU_MESSAGE)
    state = UserState(
        user_id="customer",
        metadata={
            "profile": {
                "basic_info": extract_shipping_contact(HANGZHOU_MESSAGE),
            }
        },
    )
    intent = await classify_intent(message, state)
    reply = await build_default_template_reply(message, intent, state)

    assert reply is not None
    assert reply.template_id == "tpl_order_information_received"
    assert "杭州市滨江区" in reply.answer
    assert "132****2233" in reply.answer
    assert "13297892233" not in reply.answer
    assert "把收货城市发我" not in reply.answer


@pytest.mark.asyncio
async def test_logistics_reply_does_not_reask_city_when_profile_already_has_it():
    from app.services.intent_service import classify_by_soft_rules

    message = _message("什么时候发货？")
    intent = classify_by_soft_rules(message.message)
    state = UserState(
        user_id="customer",
        metadata={
            "profile": {
                "basic_info": {
                    "mobile": "13297892233",
                    "shipping_city": "杭州市",
                    "shipping_address": "杭州市滨江区春风大厦四栋",
                }
            }
        },
    )
    reply = await build_default_template_reply(message, intent, state)

    assert reply is not None
    assert reply.template_id == "tpl_logistics_default"
    assert "把收货城市发我" not in reply.answer
    assert "继续为您跟进" in reply.answer


@pytest.mark.asyncio
async def test_shipping_contact_is_persisted_in_user_profile(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import user_profile_service

    db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    user_profile_service._sessionmakers.clear()

    contact = extract_shipping_contact(HANGZHOU_MESSAGE)
    await user_profile_service.save_shipping_contact("customer", contact)
    profile = (await user_profile_service.get_profile_bundle("customer"))["profile"]

    assert profile["basic_info"]["recipient_name"] == "黄贵杰"
    assert profile["basic_info"]["mobile"] == "13297892233"
    assert profile["basic_info"]["shipping_address"].startswith("杭州市")


@pytest.mark.asyncio
async def test_context_exposes_only_known_contact_field_names_to_llm():
    from app.schemas.context import ContextSelectionInput
    from app.services.context_selector import select_context

    context = await select_context(
        ContextSelectionInput(
            profile={
                "basic_info": {
                    "recipient_name": "黄贵杰",
                    "mobile": "13297892233",
                    "shipping_address": "杭州市滨江区春风大厦四栋",
                    "shipping_city": "杭州市",
                }
            },
            state={},
            memories=[],
            context_policy={},
        )
    )

    assert context.session_state["known_contact_fields"] == [
        "recipient_name",
        "mobile",
        "shipping_address",
        "shipping_city",
    ]
    assert "13297892233" not in str(context.model_dump())
