from fastapi.testclient import TestClient

from app.main import app


def _chat(message: str, user_id: str = "intent_route_user") -> dict:
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": user_id,
            "message": message,
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    return payload["data"]


def test_rag_no_answer_detection_catches_empty_and_legacy_sentinels():
    from app.services.reply_workflow_graph import _is_rag_no_answer

    assert _is_rag_no_answer({"answer": "可以先放在通风散光处观察。", "sources": []}) is False
    assert _is_rag_no_answer({"answer": "", "sources": []}) is True
    assert _is_rag_no_answer({"answer": "知识库中没有找到明确答案。", "sources": []}) is True


def test_product_recommendation_no_answer_does_not_switch_to_disease_triage():
    data = _chat(
        "我想找好养活的，我不是很懂这个怎么养",
        "intent_route_product_no_answer",
    )

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["need_human"] is True


def test_unclear_input_silently_hands_off():
    data = _chat("乱七八糟不明确输入", "intent_route_clarify")

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["need_human"] is True
    assert data["intent"]["slots"]["original_route"] == "clarify"
    assert data["intent"]["reason"] == "clarify_to_handoff"


def test_identity_question_reaches_persona_reply_instead_of_handoff():
    data = _chat("你是真人还是机器人？", "intent_route_identity")

    assert data["route"] == "chitchat"
    assert data["need_human"] is False
    assert "在线顾问" in data["answer"]
    assert "智能客服" not in data["answer"]
    assert "我是真人" not in data["answer"]
    assert data["intent"]["slots"]["chitchat_kind"] == "identity_question"
    assert data["metadata"]["persona"]["version"] == "v1.1"


def test_refund_routes_to_human_without_normal_reply():
    data = _chat("我要退款", "intent_route_refund")

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["need_human"] is True
    assert data["metadata"]["handoff"]["status"] == "pending"


def test_price_objection_uses_template_when_template_matches():
    data = _chat("这个有点贵", "intent_route_price")

    assert data["route"] == "template_reply"
    assert data["answer"]
    assert data["template"]["template_id"] == "tpl_price_objection_default"


def test_purchase_rejection_closes_naturally_without_form_language():
    data = _chat("不要再给我推荐产品了", "intent_route_rejection")

    assert data["route"] == "template_reply"
    assert data["need_human"] is False
    assert "先不聊产品" in data["answer"]
    assert "停止营销" not in data["answer"]
    assert "最想解决" not in data["answer"]


def test_shipping_damage_collects_evidence_before_human_review():
    data = _chat("收到后花盆碎了，苗也歪了", "intent_route_damage")

    assert data["route"] == "template_reply"
    assert data["need_human"] is False
    assert "订单" in data["answer"]
    assert "照片" in data["answer"]
    assert "人工审核" in data["answer"]


def test_order_contact_information_stays_in_sales_flow_without_privacy_lecture():
    data = _chat(
        "我把身份证号、详细地址和电话都发群里了",
        "intent_route_order_info",
    )

    assert data["route"] == "template_reply"
    assert data["need_human"] is False
    assert "订单" in data["answer"]
    assert not any(word in data["answer"] for word in ("隐私", "敏感", "撤回"))
