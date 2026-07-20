import json

from app.services.orchid_material_service import (
    ORCHID_MATERIAL_CARD,
    ORCHID_MATERIAL_TEXT,
    is_orchid_material_request,
    orchid_material_chat_result,
)


def test_orchid_material_keywords_and_negations():
    for content in (
        "发资料",
        "要资料",
        "养兰资料",
        "麻烦把养兰的资料发我一下",
        "我想要一份兰花养护资料",
    ):
        assert is_orchid_material_request(content) is True

    for content in (
        "这份资料写得怎么样",
        "不用发资料了",
        "我不需要养兰资料",
        "兰花怎么浇水",
    ):
        assert is_orchid_material_request(content) is False


def test_orchid_material_reply_uses_fixed_youzan_card():
    result = orchid_material_chat_result("亲，给我发一下养兰资料")

    assert result is not None
    assert result["answer"] == ORCHID_MATERIAL_TEXT
    assert result["route"] == "orchid_material_delivery"
    assert [message["type"] for message in result["outbound_messages"]] == [
        "text",
        "link_card",
    ]
    card = json.loads(result["outbound_messages"][1]["content"])
    assert card == {
        "title": "萧岚苑陪伴养兰资料",
        "url": (
            "https://h5.youzan.com/wscshop/shopnote/detail?"
            "noteAlias=0Ja8r3cajo"
        ),
        "description": ORCHID_MATERIAL_CARD["description"],
        "thumb_url": ORCHID_MATERIAL_CARD["thumb_url"],
    }


def test_non_material_message_does_not_build_fixed_reply():
    assert orchid_material_chat_result("建兰现在怎么浇水？") is None
