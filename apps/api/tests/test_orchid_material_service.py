import json
from pathlib import Path

from app.domains.catalog.services.orchid_material_service import (
    ORCHID_MATERIAL_CARD,
    ORCHID_MATERIAL_IMAGE_URL,
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
        "养护教程发一下",
        "直播间说有师傅教，有视频资料免费领取",
    ):
        assert is_orchid_material_request(content) is True

    for content in (
        "这份资料写得怎么样",
        "不用发资料了",
        "我不需要养兰资料",
        "兰花怎么浇水",
        "养护教程写得很清楚",
        "视频资料很好看",
    ):
        assert is_orchid_material_request(content) is False


def test_orchid_material_reply_uses_fixed_youzan_card():
    result = orchid_material_chat_result("亲，给我发一下养兰资料")

    assert result is not None
    assert result["answer"] == ORCHID_MATERIAL_TEXT
    assert result["route"] == "orchid_material_delivery"
    assert [message["type"] for message in result["outbound_messages"]] == [
        "link_card",
        "text",
        "image",
    ]
    card = json.loads(result["outbound_messages"][0]["content"])
    assert card == {
        "title": "萧岚苑陪伴养兰资料",
        "url": (
            "https://h5.youzan.com/wscshop/shopnote/detail?"
            "noteAlias=0Ja8r3cajo"
        ),
        "description": ORCHID_MATERIAL_CARD["description"],
        "thumb_url": ORCHID_MATERIAL_CARD["thumb_url"],
    }
    assert card["thumb_url"] == (
        "http://150.158.52.233/static/orchid-material/"
        "companion-material-card-thumb.jpg"
    )
    thumb_path = (
        Path(__file__).parents[1]
        / "app/static/orchid-material/companion-material-card-thumb.jpg"
    )
    assert thumb_path.stat().st_size < 51_200
    assert result["outbound_messages"][1] == {
        "type": "text",
        "content": ORCHID_MATERIAL_TEXT,
        "split": False,
    }
    assert result["outbound_messages"][2] == {
        "type": "image",
        "content": ORCHID_MATERIAL_IMAGE_URL,
    }


def test_non_material_message_does_not_build_fixed_reply():
    assert orchid_material_chat_result("建兰现在怎么浇水？") is None
