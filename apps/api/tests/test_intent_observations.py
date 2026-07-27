import json
from datetime import timedelta

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.infrastructure.database.models import ConversationMessageModel
from app.main import app
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.services.intent_observation_service import record_intent_observation
from app.domains.decisioning.services.intent_case_import_service import (
    import_intent_labeling_case,
    list_intent_labeling_cases,
    normalize_case_turns,
)


@pytest.fixture(autouse=True)
def _clear_settings_after_test():
    yield
    get_settings.cache_clear()


def _reset(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "intent-observations.db"
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("INTENT_OBSERVATION_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


async def _import_case_without_ai(case_id: str):
    return await import_intent_labeling_case(
        case_id,
        classify_with_ai=False,
    )


async def _record_sample(trace_id: str = "trace-intent-1"):
    message = NormalizedMessage(
        trace_id=trace_id,
        channel="api",
        user_id="customer-1",
        session_id="session-1",
        message="我手机号13800138000，觉得有点贵，也怕养不好",
        kb_id="kb_default",
    )
    intent = IntentResult(
        route="template_then_rag",
        primary_intent="price_objection",
        secondary_intents=["care_question"],
        primary_domain="commercial_decision",
        secondary_domains=["care_service"],
        primary_goal="express_objection",
        issues=["price", "care_confidence"],
        classifier_source="llm",
        classifier_provider="mock",
        classifier_model="mock-intent",
        raw_prediction={"primary_goal": "express_objection"},
        confidence=0.91,
        need_template=True,
        need_rag=True,
        reason="价格与养护顾虑",
    )
    await record_intent_observation(
        message=message,
        intent=intent,
        candidates=[
            {"kind": "goal", "id": "express_objection", "score": 0.88}
        ],
        context=[{"role": "assistant", "content": "您比较在意哪方面？"}],
    )


def test_observation_annotation_history_and_training_export(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)
    client = TestClient(app)

    accepted = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "confirmed"},
    ).json()["data"]
    assert accepted["total"] == 1
    assert accepted["items"][0]["primary_goal"] == "express_objection"
    assert accepted["items"][0]["annotation_status"] == "confirmed"
    assert accepted["items"][0]["annotation_origin"] == "automatic"
    assert accepted["items"][0]["needs_review"] is False

    confirmed = client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "confirmed",
            "annotator_id": "reviewer-a",
            "note": "预测正确",
        },
    )
    assert confirmed.status_code == 200

    corrected = client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "corrected",
            "primary_domain": "commercial_decision",
            "secondary_domains": [],
            "primary_goal": "negotiate",
            "secondary_goals": [],
            "issues": ["discount"],
            "scope": "in_scope",
            "annotator_id": "reviewer-b",
            "note": "实际是在议价",
        },
    )
    assert corrected.status_code == 200

    detail = client.get(
        "/api/v1/admin/intent-observations/trace-intent-1"
    ).json()["data"]
    assert detail["annotation_status"] == "corrected"
    assert len(detail["annotation_history"]) == 2
    assert detail["raw_prediction"] == {"primary_goal": "express_objection"}
    assert detail["candidate_labels"][0]["id"] == "express_objection"

    exported = client.get("/api/v1/admin/intent-training-data").json()["data"]
    assert exported["total"] == 1
    sample = exported["items"][0]
    assert sample["labels"]["primary_goal"] == "negotiate"
    assert sample["labels"]["issues"] == ["discount"]
    assert "13800138000" not in sample["text"]
    assert "[MOBILE]" in sample["text"]

    jsonl = client.get("/api/v1/admin/intent-training-data/export")
    assert jsonl.status_code == 200
    assert json.loads(jsonl.text.strip())["sample_id"] == "trace-intent-1"


def test_only_low_confidence_prediction_needs_review(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)

    async def record_low_confidence():
        message = NormalizedMessage(
            trace_id="trace-low-confidence",
            channel="wechat",
            user_id="customer-low",
            session_id="default",
            message="都想看，可以发给我吗？",
            kb_id="kb_default",
            metadata={
                "conversation_id": "wechat:customer-low:default",
                "conversation_message_ids": [42],
            },
        )
        await record_intent_observation(
            message=message,
            intent=IntentResult(
                route="clarify",
                primary_intent="unknown",
                primary_domain="conversation",
                primary_goal="unclear",
                confidence=0.45,
                reason="low confidence",
            ),
        )

    anyio.run(record_low_confidence)
    result = TestClient(app).get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "pending"},
    ).json()["data"]

    assert result["total"] == 1
    assert result["pending_count"] == 1
    assert result["accepted_count"] == 0
    assert result["items"][0]["needs_review"] is True
    assert result["items"][0]["conversation_id"] == "wechat:customer-low:default"
    assert result["items"][0]["conversation_message_ids"] == [42]


def test_high_confidence_prediction_is_exported_with_automatic_origin(
    monkeypatch, tmp_path
):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)

    result = TestClient(app).get("/api/v1/admin/intent-training-data").json()["data"]

    assert result["total"] == 1
    assert result["items"][0]["annotation"]["status"] == "auto_confirmed"
    assert result["items"][0]["annotation"]["origin"] == "confidence_threshold"


def test_internal_workbench_title_is_not_captured(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)

    async def record_title():
        message = NormalizedMessage(
            trace_id="trace-workbench-title",
            channel="wechat",
            user_id="customer-1",
            session_id="default",
            message="销售工作台 - 销售 Agent",
            kb_id="kb_default",
        )
        await record_intent_observation(
            message=message,
            intent=IntentResult(
                route="chitchat",
                primary_intent="unknown",
                confidence=0.95,
            ),
        )

    anyio.run(record_title)
    result = TestClient(app).get("/api/v1/admin/intent-observations").json()["data"]
    assert result["total"] == 0


def test_historical_conversation_gap_is_reconciled_and_locatable(
    monkeypatch, tmp_path
):
    _reset(monkeypatch, tmp_path)

    async def arrange():
        from app.domains.conversations.services.conversation_service import record_customer_message

        await record_customer_message(
            channel="wechat",
            user_id="customer-gap",
            session_id="default",
            content="中午好",
            message_id="provider-1",
            status="ai_waiting",
            route="inbound_text",
        )
        await record_customer_message(
            channel="wechat",
            user_id="customer-gap",
            session_id="default",
            content="想买一盆好养的兰花",
            message_id="provider-2",
            status="ai_waiting",
            route="inbound_text",
        )
        await record_customer_message(
            channel="wechat",
            user_id="customer-gap",
            session_id="default",
            content="直播间说有师傅教，有视频资料免费领取",
            message_id="provider-material",
            status="ai_waiting",
            route="inbound_text",
        )
        await record_intent_observation(
            message=NormalizedMessage(
                trace_id="trace-captured",
                channel="wechat",
                user_id="customer-gap",
                session_id="default",
                message="想买一盆好养的兰花",
                kb_id="kb_default",
            ),
            intent=IntentResult(
                route="rag_answer",
                primary_intent="recommend_product",
                primary_domain="customer_need",
                primary_goal="seek_recommendation",
                confidence=0.9,
                reason="购买推荐",
            ),
        )

    anyio.run(arrange)

    from app.domains.decisioning.services import intent_observation_service as service

    factory = service._sessionmakers[get_settings().chat_log_db_url]
    with factory() as session:
        material_message = session.scalar(
            select(ConversationMessageModel).where(
                ConversationMessageModel.message_id == "provider-material"
            )
        )
        material_message.created_at -= timedelta(days=2)
        session.commit()
    service._backfill_observation_locators_and_gaps(factory)
    result = TestClient(app).get("/api/v1/admin/intent-observations").json()["data"]

    assert result["total"] == 2
    captured = next(item for item in result["items"] if item["trace_id"] == "trace-captured")
    material = next(
        item
        for item in result["items"]
        if item["classifier_source"] == "historical_rule"
    )
    assert captured["conversation_id"] == "wechat:customer-gap:default"
    assert len(captured["conversation_message_ids"]) == 1
    assert all(item["classifier_source"] != "capture_gap" for item in result["items"])
    assert material["primary_domain"] == "care_service"
    assert material["primary_goal"] == "request_material"
    assert material["issues"] == ["care_general"]
    assert material["needs_review"] is False


def test_invalid_corrected_label_is_rejected(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)

    response = TestClient(app).post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "corrected",
            "primary_domain": "not_a_domain",
            "primary_goal": "negotiate",
            "scope": "in_scope",
            "annotator_id": "reviewer",
        },
    )

    assert response.status_code == 422


def test_only_latest_annotation_controls_training_eligibility(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    anyio.run(_record_sample)
    client = TestClient(app)
    client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={"status": "confirmed", "annotator_id": "reviewer"},
    )
    client.post(
        "/api/v1/admin/intent-observations/trace-intent-1/annotations",
        json={
            "status": "excluded",
            "annotator_id": "reviewer",
            "note": "包含无法判断的上下文",
        },
    )

    result = client.get("/api/v1/admin/intent-training-data").json()["data"]
    assert result["total"] == 0


def test_chat_pipeline_records_each_classified_user_turn(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "customer-pipeline",
            "session_id": "session-pipeline",
            "message": "这个多少钱？",
            "kb_id": "kb_default",
        },
    )

    assert response.status_code == 200
    observations = client.get("/api/v1/admin/intent-observations").json()["data"]
    assert observations["total"] == 1
    item = observations["items"][0]
    assert item["user_message"] == "这个多少钱？"
    assert item["primary_domain"] == "commercial_decision"
    assert item["primary_goal"] == "ask_information"
    assert item["issues"] == ["price"]


def test_case_import_creates_pending_customer_turns_with_context(
    monkeypatch, tmp_path
):
    _reset(monkeypatch, tmp_path)

    first_result = anyio.run(_import_case_without_ai, "case01")
    second_result = anyio.run(_import_case_without_ai, "case01")
    client = TestClient(app)
    observations = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "pending", "classifier_source": "case_import"},
    ).json()["data"]

    assert first_result["observation_count"] == 7
    assert second_result["trace_ids"] == first_result["trace_ids"]
    assert observations["total"] == 7
    assert all(item["conversation_id"] is None for item in observations["items"])
    assert all(item["needs_review"] is True for item in observations["items"])

    first = client.get(
        "/api/v1/admin/intent-observations/intent-case-case01-001"
    ).json()["data"]
    assert first["user_message"] == "我是甘肃天水的，我养的全是建兰。"
    assert first["context"] == [
        {
            "role": "assistant",
            "content": "兰友您好，我是萧兰苑养兰老师（兰悦），专业养兰20年。"
            "请问您目前养了几盆兰花？都是什么品种？所在省份？",
        }
    ]
    assert first["primary_domain"] is None
    assert first["primary_goal"] is None
    assert first["scope"] == "ambiguous"

    last = client.get(
        "/api/v1/admin/intent-observations/intent-case-case01-007"
    ).json()["data"]
    assert last["user_message"] == "已购买完成。"
    assert last["context"][-1] == {
        "role": "assistant",
        "content": "我发您会员专属链接，填写地址下单即可。",
    }


def test_case_turn_normalization_merges_message_fragments_and_adjacent_roles():
    turns = normalize_case_turns(
        [
            {"role": "merchant", "messages": ["请问您养什么品种？"]},
            {"role": "customer", "messages": ["建兰", "还有春兰"]},
            {"role": "customer", "messages": ["都是直播间买的"]},
        ]
    )

    assert turns == [
        {
            "role": "assistant",
            "messages": ["请问您养什么品种？"],
            "content": "请问您养什么品种？",
        },
        {
            "role": "user",
            "messages": ["建兰", "还有春兰", "都是直播间买的"],
            "content": "建兰\n还有春兰\n都是直播间买的",
        },
    ]


def test_all_bundled_cases_import_expected_customer_turns(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    expected_counts = {
        "case01": 7,
        "case02": 10,
        "case03": 7,
        "case04": 7,
        "case05": 9,
        "case06": 8,
        "case07": 4,
        "case08": 8,
        "case09": 5,
        "case10": 5,
        "case11": 11,
        "case12": 37,
        "case13": 26,
        "case14": 11,
        "case15": 17,
        "case15_2": 12,
        "case16": 28,
        "case17": 14,
        "case18": 10,
        "case18_2": 3,
        "case19": 11,
        "case20": 23,
        "case20_2": 13,
        "case21": 13,
        "case22": 12,
        "case23": 13,
        "case24": 9,
        "case25": 18,
        "case26": 6,
        "case27": 7,
        "case2_01": 2,
        "case2_02": 4,
        "case2_02_2": 4,
        "case2_03": 18,
        "case2_04": 10,
        "case2_05": 8,
        "case2_06": 35,
        "case2_07": 11,
        "case2_08": 7,
        "case2_09": 9,
        "case2_10": 11,
        "case2_11": 7,
        "case2_12": 7,
        "case2_13": 9,
        "case2_14": 13,
        "case2_15": 24,
        "case2_16": 11,
    }

    async def import_all():
        return {
            case_id: await import_intent_labeling_case(
                case_id,
                classify_with_ai=False,
            )
            for case_id in list_intent_labeling_cases()
        }

    results = anyio.run(import_all)
    client = TestClient(app)
    observations = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "pending", "classifier_source": "case_import"},
    ).json()["data"]

    assert list_intent_labeling_cases() == list(expected_counts)
    assert {
        case_id: result["observation_count"]
        for case_id, result in results.items()
    } == expected_counts
    assert observations["total"] == sum(expected_counts.values()) == 554

    merged_customer_turn = client.get(
        "/api/v1/admin/intent-observations/intent-case-case07-004"
    ).json()["data"]
    assert merged_customer_turn["user_message"] == (
        "不知道。\n另外端午节会员活动还有吗？如果可以，我想参加学习。"
    )
    assert merged_customer_turn["raw_prediction"]["source_message_count"] == 2

    merged_merchant_context = client.get(
        "/api/v1/admin/intent-observations/intent-case-case03-003"
    ).json()["data"]["context"][-1]["content"]
    assert "请把订单截图发给我" in merged_merchant_context
    assert "经核实，您购买的是其他店铺的兰花" in merged_merchant_context

    reconstructed = client.get(
        "/api/v1/admin/intent-observations/intent-case-case10-002"
    ).json()["data"]
    assert reconstructed["raw_prediction"]["content_quality"] == (
        "reconstructed_from_summary"
    )


def test_high_confidence_case_prediction_is_accepted_until_human_correction(
    monkeypatch, tmp_path
):
    _reset(monkeypatch, tmp_path)
    from app.domains.decisioning.services import intent_case_import_service as service

    seen_contexts = []

    async def retrieve_candidates(message: str, top_k: int):
        return [
            {
                "kind": "goal",
                "id": "provide_information",
                "name": "提供信息",
                "score": 0.9,
            }
        ]

    async def classify(message, user_state, candidates):
        seen_contexts.append(user_state.metadata["recent_turns"])
        return IntentResult(
            route="rag_answer",
            primary_intent="care_question",
            primary_domain="customer_need",
            primary_goal="provide_information",
            issues=["experience_level"],
            scope="in_scope",
            classifier_source="llm",
            classifier_provider="mock",
            classifier_model="mock-intent",
            raw_prediction={
                "primary_domain": "customer_need",
                "primary_goal": "provide_information",
            },
            confidence=0.96,
            need_rag=True,
            reason="客户提供地区和品种",
        )

    monkeypatch.setattr(service, "retrieve_intent_examples", retrieve_candidates)
    monkeypatch.setattr(service, "classify_by_llm", classify)

    async def import_with_ai():
        return await service.import_intent_labeling_case("case01")

    result = anyio.run(import_with_ai)
    client = TestClient(app)
    pending = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "pending", "classifier_source": "case_import"},
    ).json()["data"]
    accepted = client.get(
        "/api/v1/admin/intent-observations",
        params={"annotation_status": "confirmed", "classifier_source": "case_import"},
    ).json()["data"]
    first = client.get(
        "/api/v1/admin/intent-observations/intent-case-case01-001"
    ).json()["data"]

    assert result["classified_with_ai"] is True
    assert pending["total"] == 0
    assert accepted["total"] == 7
    assert first["primary_domain"] == "customer_need"
    assert first["primary_goal"] == "provide_information"
    assert first["issues"] == ["experience_level"]
    assert first["confidence"] == pytest.approx(0.96)
    assert first["classifier_source"] == "case_import"
    assert first["classifier_provider"] == "mock"
    assert first["annotation_status"] == "confirmed"
    assert first["annotation_origin"] == "automatic"
    assert first["needs_review"] is False
    assert first["raw_prediction"]["prediction_source"] == "llm"
    assert first["raw_prediction"]["prediction"]["primary_goal"] == (
        "provide_information"
    )
    assert seen_contexts[0] == first["context"]
    assert client.get("/api/v1/admin/intent-training-data").json()["data"]["total"] == 7

    corrected = client.post(
        "/api/v1/admin/intent-observations/intent-case-case01-001/annotations",
        json={
            "status": "corrected",
            "primary_domain": "care_service",
            "primary_goal": "ask_information",
            "issues": ["care_general"],
            "scope": "in_scope",
            "annotator_id": "reviewer",
        },
    )
    assert corrected.status_code == 200
    exported = client.get("/api/v1/admin/intent-training-data").json()["data"]
    assert exported["total"] == 7
    assert exported["items"][0]["labels"]["primary_domain"] == "care_service"
    assert exported["items"][0]["labels"]["primary_goal"] == "ask_information"
