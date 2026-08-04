import json
from pathlib import Path

from app.domains.catalog.services.orchid_material_service import (
    ORCHID_MATERIAL_CARD,
    ORCHID_MATERIAL_DISCOVERY_TEXT,
    ORCHID_MATERIAL_IMAGE_URL,
    ORCHID_MATERIAL_ORDER_SCREENSHOT_REQUEST,
    ORCHID_MATERIAL_PURCHASE_QUESTION,
    ORCHID_MATERIAL_TEXT,
    is_douyin_purchase_confirmation,
    is_orchid_material_followup,
    is_orchid_material_request,
    orchid_material_chat_result,
    orchid_material_discovery_chat_result,
    orchid_material_order_screenshot_context,
    orchid_material_video_access_chat_result,
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


def test_orchid_material_discovery_reply_asks_for_the_customer_need():
    result = orchid_material_discovery_chat_result()

    assert result["answer"] == ORCHID_MATERIAL_DISCOVERY_TEXT
    assert result["route"] == "orchid_material_discovery"
    assert result["metadata"]["material_request_phase"] == "discovery"
    assert result["outbound_messages"] == [
        {"type": "text", "content": ORCHID_MATERIAL_DISCOVERY_TEXT}
    ]
    assert "最想解决哪方面的问题" in result["answer"]


def test_douyin_purchase_confirmation_requires_an_explicit_positive_answer():
    for content in (
        "是的",
        "对的",
        "嗯嗯",
        "有的",
        "有订单",
        "买过",
        "我在抖音买的",
        "抖音下单了",
    ):
        assert is_douyin_purchase_confirmation(content) is True

    for content in ("不是", "没有", "没买", "不是抖音买的", "我再看看"):
        assert is_douyin_purchase_confirmation(content) is False


def test_material_video_access_replies_are_sequential_and_fixed():
    purchase_check = orchid_material_video_access_chat_result(
        "confirm_douyin_purchase"
    )
    screenshot_request = orchid_material_video_access_chat_result(
        "request_order_screenshot"
    )

    assert purchase_check["answer"] == ORCHID_MATERIAL_PURCHASE_QUESTION
    assert "订单截图" not in purchase_check["answer"]
    assert purchase_check["route"] == "orchid_material_purchase_check"
    assert screenshot_request["answer"] == ORCHID_MATERIAL_ORDER_SCREENSHOT_REQUEST
    assert "订单截图" in screenshot_request["answer"]
    assert screenshot_request["route"] == "orchid_material_order_screenshot_request"
    assert orchid_material_video_access_chat_result("unknown") is None


def test_order_screenshot_context_starts_only_after_douyin_confirmation():
    assert orchid_material_order_screenshot_context("不是抖音买的") is None

    context = orchid_material_order_screenshot_context("是的，我在抖音买的")

    assert context is not None
    assert context["tool_state"]["purchase_channel"] == "douyin_confirmed"
    assert (
        context["tool_state"]["material_video_access_action"]
        == "request_order_screenshot"
    )


def test_opening_context_turns_short_replies_into_material_requests():
    recent_turns = [
        {
            "role": "assistant",
            "route": "opening",
            "content": "我们会给兰友提供养兰资料、视频课程和一对一养护指导。",
        }
    ]

    for content in (
        "1",
        "[强]",
        "怎么领？",
        "没领到",
        "还没收到",
        "好的",
        "好的，好的",
        "养护",
    ):
        assert is_orchid_material_followup(content, recent_turns) is True
    assert (
        is_orchid_material_followup(
            "[强]",
            [
                {
                    "role": "assistant",
                    "route": "chitchat",
                    "content": "我们也会给兰友提供养兰资料和视频课程。",
                }
            ],
        )
        is True
    )


def test_material_followup_requires_opening_context_and_respects_blockers():
    opening_turns = [
        {
            "role": "assistant",
            "route": "opening",
            "content": "我们会给兰友提供养兰资料。",
        }
    ]
    ordinary_turns = [
        {
            "role": "assistant",
            "route": "template_reply",
            "content": "我帮您查一下订单。",
        }
    ]

    assert is_orchid_material_followup("没领到", ordinary_turns) is False
    assert is_orchid_material_followup("订单一直没收到", opening_turns) is False
    assert is_orchid_material_followup("不用发资料", opening_turns) is False
    assert is_orchid_material_followup("资料链接打不开", opening_turns) is False


def test_confirmed_contextual_request_builds_fixed_material_reply():
    result = orchid_material_chat_result("没领到", confirmed_request=True)

    assert result is not None
    assert result["route"] == "orchid_material_delivery"


def test_non_material_message_does_not_build_fixed_reply():
    assert orchid_material_chat_result("建兰现在怎么浇水？") is None
