from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_pain_discovery_keeps_service_brand_value_after_product_context():
    from app.services import chat_orchestrator

    action = SimpleNamespace(
        sales_action="discover_pain",
        known_slots={"need_track": "product"},
        question_slot=None,
    )

    assert chat_orchestrator._should_attach_membership_brand_value(action) is True


def test_pain_discovery_does_not_build_brand_value_before_pain_is_clear():
    from app.services import chat_orchestrator

    action = SimpleNamespace(
        sales_action="discover_pain",
        known_slots={"need_track": "product"},
        question_slot="pain_point",
    )

    assert chat_orchestrator._should_attach_membership_brand_value(action) is False


def _chat(
    message: str,
    user_id: str = "intent_route_user",
    *,
    metadata: dict | None = None,
) -> dict:
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": user_id,
            "message": message,
            "kb_id": "kb_default",
            "metadata": metadata or {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    return payload["data"]


def test_rag_no_answer_detection_catches_empty_and_legacy_sentinels():
    from app.domains.decisioning.services.reply_workflow_graph import _is_rag_no_answer

    assert _is_rag_no_answer({"answer": "可以先放在通风散光处观察。", "sources": []}) is False
    assert _is_rag_no_answer({"answer": "", "sources": []}) is True
    assert _is_rag_no_answer({"answer": "知识库中没有找到明确答案。", "sources": []}) is True


def test_demo_product_interest_stays_in_discovery_without_handoff():
    data = _chat(
        "我想找好养活的，我不是很懂这个怎么养",
        f"intent_route_product_no_answer_{uuid4().hex}",
        metadata={"demo": True},
    )

    assert data["answer"]
    assert data["route"] == "template_reply"
    assert data["need_human"] is False
    assert data["intent"]["primary_intent"] == "product_query"
    assert data["intent"]["slots"]["original_route"] == "template_reply"
    assert data["metadata"]["demo_llm_fallback"]["reason"] == (
        "template_not_found_to_handoff"
    )
    assert data["metadata"].get("commerce_action", {}).get("card_sent") is not True
    assert "挑选" not in data["answer"]
    assert "室内摆放还是阳台" not in data["answer"]
    assert any(
        marker in data["answer"] for marker in ("黄叶", "黑斑", "烂根", "腐苗")
    )
    assert data["metadata"]["sales_action"]["emitted_question_slot"] == "pain_point"


def test_unclear_input_continues_without_handoff():
    data = _chat("乱七八糟不明确输入", "intent_route_clarify")

    assert data["answer"] == "没关系，您接着说就行，我继续帮您。"
    assert data["route"] == "chitchat"
    assert data["need_human"] is False
    assert data["intent"]["slots"]["original_route"] == "clarify"
    assert data["intent"]["reason"] == "ambiguous_continue_automatically"


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


def test_price_objection_continues_without_empty_default_template():
    data = _chat("这个有点贵", "intent_route_price")

    assert data["route"] == "template_reply"
    assert data["answer"]
    assert data["answer"] != "我理解您会关注价格。"
    assert data["template"].get("template_id") is None
    assert data["metadata"]["llm_fallback"]["reason"] == (
        "template_not_found_to_handoff"
    )


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
