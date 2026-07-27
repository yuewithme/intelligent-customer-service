import copy

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.decisioning.schemas.reply_plan import ReplyPlan
from app.domains.decisioning.schemas.reply_shadow import ReplyShadowAnnotationRequest
from app.domains.decisioning.services import reply_shadow_service
from app.main import app


@pytest.fixture
def reply_shadow_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLY_SHADOW_ENABLED", "true")
    monkeypatch.setenv("REPLY_SHADOW_SAMPLE_PERCENT", "0")
    monkeypatch.setenv("REPLY_SHADOW_HIGH_RISK_ALWAYS", "true")
    monkeypatch.setenv("REPLY_SHADOW_LLM_PROVIDER", "mock")
    monkeypatch.setenv("REPLY_SHADOW_LLM_MODEL", "shadow-test")
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL",
        f"sqlite:///{(tmp_path / 'reply-shadow.db').as_posix()}",
    )
    get_settings.cache_clear()
    reply_shadow_service._sessionmakers.clear()
    yield
    reply_shadow_service._sessionmakers.clear()
    get_settings.cache_clear()


def _inputs():
    message = NormalizedMessage(
        trace_id="trace_shadow_1",
        channel="api",
        user_id="customer_1",
        session_id="session_1",
        message="这个价格我再考虑一下，手机号是13800138000",
        kb_id="kb_default",
        metadata={},
    )
    user_state = UserState(
        user_id="customer_1",
        session_id="session_1",
        sales_stage="value_built",
        metadata={
            "recent_turns": [
                {"role": "user", "content": "可以发到13800138000吗？"},
            ],
            "sales_action": {"sales_action": "build_value"},
        },
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent="hesitation",
        primary_domain="commerce",
        primary_goal="consider_purchase",
        issues=["price_value"],
        sales_stage="value_built",
        confidence=0.9,
    )
    plan = ReplyPlan(
        action="template_reply",
        reason="sales_stage_policy",
    )
    reply = FinalReply(
        answer="可以的，您慢慢考虑。",
        reply_type="template",
        route="template_reply",
        metadata={
            "sales_action": {
                "sales_action": "build_value",
                "reply_goal": "回应价格顾虑",
            }
        },
    )
    return message, user_state, intent, plan, reply


def test_reply_shadow_sampling_keeps_high_risk_when_random_sample_is_zero(
    reply_shadow_env,
):
    assert reply_shadow_service.reply_shadow_selected(
        "trace_high_risk",
        "这个价格我再考虑一下",
    )
    assert not reply_shadow_service.reply_shadow_selected(
        "trace_normal",
        "你好",
    )


@pytest.mark.asyncio
async def test_shadow_decision_is_persisted_without_mutating_production_state(
    reply_shadow_env,
    monkeypatch,
):
    message, user_state, intent, plan, reply = _inputs()
    before_state = copy.deepcopy(user_state.model_dump())
    before_reply = copy.deepcopy(reply.model_dump())
    snapshot = reply_shadow_service.build_reply_shadow_snapshot(
        message=message,
        user_state=user_state,
        intent=intent,
        plan=plan,
        reply=reply,
    )

    async def fake_generate_json(*args, **kwargs):
        assert kwargs["shadow"] is True
        assert kwargs["purpose"] == "reply_shadow"
        return {
            "sales_stage": "value_built",
            "route": "template_reply",
            "sales_action": "resolve_blocker",
            "reply": "可以慢慢考虑。您主要是在比较价格，还是担心新手不好养？",
            "need_human": False,
            "next_action": None,
            "follow_up": {
                "needed": True,
                "action": "确认客户尚未下单后，了解剩余顾虑",
                "due_in_hours": 48,
                "cancel_conditions": [
                    "客户已经下单",
                    "客户明确拒绝",
                    "客户再次主动联系",
                    "人工已经接管",
                ],
            },
            "facts_used": [],
            "confidence": 0.82,
            "reason": "客户表达犹豫，需要先识别阻碍，并保留低压力跟进。",
        }

    monkeypatch.setattr(reply_shadow_service, "generate_json", fake_generate_json)
    await reply_shadow_service.evaluate_and_record_reply_shadow(snapshot)

    assert user_state.model_dump() == before_state
    assert reply.model_dump() == before_reply
    assert "13800138000" not in str(snapshot)
    detail = await reply_shadow_service.get_reply_shadow_run("trace_shadow_1")
    assert detail is not None
    assert detail["primary"]["reply"] == "可以的，您慢慢考虑。"
    assert detail["shadow"]["follow_up"]["needed"] is True
    assert detail["review_priority"] == "high"
    assert "sales_action_disagreement" in detail["auto_issues"]


@pytest.mark.asyncio
async def test_reviewed_shadow_winner_can_be_exported_as_evaluation_data(
    reply_shadow_env,
):
    message, user_state, intent, plan, reply = _inputs()
    snapshot = reply_shadow_service.build_reply_shadow_snapshot(
        message=message,
        user_state=user_state,
        intent=intent,
        plan=plan,
        reply=reply,
    )
    from app.domains.decisioning.schemas.reply_shadow import ReplyShadowDecision

    decision = ReplyShadowDecision.model_validate(
        {
            "sales_stage": "value_built",
            "route": "template_reply",
            "sales_action": "resolve_blocker",
            "reply": "可以慢慢考虑，您更顾虑价格还是养护难度？",
            "need_human": False,
            "next_action": None,
            "follow_up": {
                "needed": False,
                "action": None,
                "due_in_hours": None,
                "cancel_conditions": [],
            },
            "facts_used": [],
            "confidence": 0.8,
            "reason": "先识别真实顾虑。",
        }
    )
    reply_shadow_service.record_reply_shadow(
        snapshot=snapshot,
        shadow=decision,
        latency_ms=20,
    )
    annotation = await reply_shadow_service.create_reply_shadow_annotation(
        "trace_shadow_1",
        ReplyShadowAnnotationRequest(
            verdict="shadow_better",
            error_tags=["生产版未识别顾虑"],
            note="影子版更能推进判断。",
            annotator_id="reviewer_1",
        ),
    )
    assert annotation["verdict"] == "shadow_better"

    dataset = await reply_shadow_service.build_reply_shadow_dataset()
    assert len(dataset) == 1
    assert dataset[0]["label"]["verdict"] == "shadow_better"
    assert (
        dataset[0]["preferred_decision"]["reply"]
        == "可以慢慢考虑，您更顾虑价格还是养护难度？"
    )
    assert "13800138000" not in str(dataset[0])

    response = TestClient(app).get(
        "/api/v1/admin/reply-shadows/trace_shadow_1"
    )
    assert response.status_code == 200
    assert response.json()["data"]["latest_annotation"]["verdict"] == "shadow_better"
