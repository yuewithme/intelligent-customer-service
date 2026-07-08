import pytest

from app.config import get_settings
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.tag import TagResult
from app.services import customer_level_service
from app.services.customer_level_service import (
    classify_customer_level,
    get_customer_level_prompt_block_ids,
    seed_customer_level_policy,
)
from app.services.policy_engine import decide_policy
from app.services.tagger_service import build_tag_result


@pytest.fixture(autouse=True)
def isolated_customer_level_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'customer_level.db').as_posix()}")
    get_settings.cache_clear()
    customer_level_service.clear_cache()
    yield
    customer_level_service.clear_cache()
    get_settings.cache_clear()


def test_seed_customer_level_policy_stores_l1_to_l6_and_l1_l3_prompt_bindings():
    seed_customer_level_policy()

    assert get_customer_level_prompt_block_ids("L1") == [
        "customer_level.l1.identity",
        "customer_level.l1.communication",
        "customer_level.l1.recommendation",
    ]
    assert get_customer_level_prompt_block_ids("L3") == [
        "customer_level.l3.identity",
        "customer_level.l3.communication",
        "customer_level.l3.recommendation",
    ]
    assert get_customer_level_prompt_block_ids("L4") == []


def test_classify_l1_from_beginner_evidence():
    result = classify_customer_level(
        message="我是新手，刚开始养兰花，能用泥土种吗？预算30元左右试试。",
        user_state=UserState(user_id="user_1", customer_tags=[]),
    )

    assert result.level == "L1"
    assert result.route == "rag_answer"
    assert result.confidence > 0
    assert "新手" in result.matched_evidence


def test_classify_l5_from_advanced_art_orchid_evidence_routes_human():
    result = classify_customer_level(
        message="我想问下虎斑艺和中透艺后续进化方向，艺草返青怎么判断？",
        user_state=UserState(user_id="user_2", customer_tags=[]),
    )

    assert result.level == "L5"
    assert result.route == "human"
    assert result.handoff_reason == "advanced_customer_level"
    assert {"虎斑", "中透艺", "艺草"} <= set(result.matched_evidence)


@pytest.mark.asyncio
async def test_tagger_adds_customer_level_label_from_classifier():
    message = NormalizedMessage(
        trace_id="trace_1",
        channel="api",
        user_id="user_1",
        session_id="sess_1",
        message="我养了一百多盆，最近夏天病虫害多，想看看经典品种。",
        kb_id="kb_default",
    )
    user_state = UserState(user_id="user_1", customer_tags=["L1 青铜期"])
    intent = IntentResult(
        route="rag_answer",
        primary_intent="orchid_care",
        sales_stage="pain_confirmed",
        confidence=0.88,
        need_rag=True,
    )

    tag = await build_tag_result(message=message, user_state=user_state, intent=intent)

    assert "customer_tag:L3 黄金期" in tag.labels
    assert "customer_tag:L1 青铜期" not in tag.labels
    assert tag.entities["customer_level"]["level"] == "L3"


@pytest.mark.asyncio
async def test_tagger_keeps_existing_customer_level_when_message_has_no_new_evidence():
    message = NormalizedMessage(
        trace_id="trace_2",
        channel="api",
        user_id="user_2",
        session_id="sess_2",
        message="今天想看看有什么兰花。",
        kb_id="kb_default",
    )
    user_state = UserState(user_id="user_2", customer_tags=["L2 白银期"])
    intent = IntentResult(
        route="rag_answer",
        primary_intent="orchid_care",
        sales_stage="pain_confirmed",
        confidence=0.88,
        need_rag=True,
    )

    tag = await build_tag_result(message=message, user_state=user_state, intent=intent)

    assert "customer_tag:L2 白银期" in tag.labels


@pytest.mark.asyncio
async def test_policy_uses_l1_l3_prompt_blocks_and_hands_off_l4_l6():
    l2_tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="unknown",
        confidence=0.9,
        labels=["customer_tag:L2 白银期"],
    )
    l5_tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="advanced",
        confidence=0.9,
        labels=["customer_tag:L5 宗师期"],
    )

    l2_decision = await decide_policy(l2_tag)
    l5_decision = await decide_policy(l5_tag)

    assert "customer_level.l2.identity" in l2_decision.prompt_block_ids
    assert "customer_level.l2.recommendation" in l2_decision.prompt_block_ids
    assert l5_decision.route == "human"
    assert l5_decision.action == "human"
    assert l5_decision.reason == "advanced_customer_level_to_human"
