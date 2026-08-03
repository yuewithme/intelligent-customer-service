from datetime import datetime, timezone

import pytest


def test_opening_reply_contains_configured_text_and_image(monkeypatch):
    from app.core.config import get_settings
    from app.domains.decisioning.services.reply_builder import build_opening_reply

    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "https://bot.example.com/static/xiaolanyuan-opening.jpg")
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "0")
    get_settings.cache_clear()

    reply = build_opening_reply()

    opening_text = (
        "兰友您好！欢迎来到萧岚苑，我是养兰师傅兰画🌹"
        "我们专注国兰培育和养护，也会给兰友提供养兰资料、"
        "视频课程和一对一养护指导。"
    )
    followup_text = (
        "为了给您提供适合您的学习资料，请告诉我以下两点信息：\n"
        "1. 家里目前养了多少盆兰花？（还没养扣“0”😝）\n"
        "2. 具体养了哪些品种？"
    )
    assert reply.answer == f"{opening_text}\n\n{followup_text}"
    assert reply.answer_segments == [opening_text, followup_text]
    assert [message.model_dump() for message in reply.outbound_messages] == [
        {"type": "text", "content": opening_text, "material_id": None},
        {
            "type": "image",
            "content": "https://bot.example.com/static/xiaolanyuan-opening.jpg",
            "material_id": None,
        },
        {"type": "text", "content": followup_text, "material_id": None},
    ]
    get_settings.cache_clear()


def test_opening_material_keeps_image_semantics_for_workbench(monkeypatch):
    from app.core.config import get_settings
    from app.domains.decisioning.services.reply_builder import build_opening_reply

    monkeypatch.setenv(
        "EYUN_OPENING_IMAGE_URL",
        "https://bot.example.com/static/xiaolanyuan-opening.jpg",
    )
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "7")
    get_settings.cache_clear()

    reply = build_opening_reply()

    assert reply.outbound_messages[1].model_dump() == {
        "type": "image",
        "content": "https://bot.example.com/static/xiaolanyuan-opening.jpg",
        "material_id": 7,
    }
    assert reply.outbound_messages[2].type == "text"
    assert "家里目前养了多少盆兰花" in reply.outbound_messages[2].content
    get_settings.cache_clear()


def test_first_inbound_message_is_the_only_opening_trigger(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.infrastructure.database.models import EyunInboundMessageModel
    from app.services import message_risk_control_service as risk_control

    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'opening.db').as_posix()}")
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()

    with risk_control._get_session() as session:
        session.add(
            EyunInboundMessageModel(
                provider_message_id="first",
                batch_key="wid:customer",
                content="第一条",
                payload_json="{}",
                created_at=risk_control.utcnow(),
            )
        )
        session.commit()
        assert risk_control.is_first_eyun_inbound_message(session, "wid:customer") is True

        session.add(
            EyunInboundMessageModel(
                provider_message_id="second",
                batch_key="wid:customer",
                content="第二条",
                payload_json="{}",
                created_at=risk_control.utcnow(),
            )
        )
        session.commit()
        assert risk_control.is_first_eyun_inbound_message(session, "wid:customer") is False

    get_settings.cache_clear()
    risk_control._sessionmakers.clear()


@pytest.mark.asyncio
async def test_first_inbound_message_skips_debounce_delay(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.services import message_risk_control_service as risk_control

    now = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'opening-delay.db').as_posix()}")
    monkeypatch.setattr(risk_control, "utcnow", lambda: now)
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()

    batch = await risk_control.enqueue_eyun_inbound(
        {
            "messageType": "60001",
            "wcId": "sales",
            "data": {"wId": "wid", "fromUser": "customer", "content": "想学养兰", "newMsgId": 1},
        }
    )

    assert batch["due_at"] == now
    get_settings.cache_clear()
    risk_control._sessionmakers.clear()
