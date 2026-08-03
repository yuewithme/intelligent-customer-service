import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domains.decisioning.services import conversation_case_service
from app.main import app


@pytest.fixture
def conversation_case_env(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL",
        f"sqlite:///{(tmp_path / 'conversation-cases.db').as_posix()}",
    )
    monkeypatch.setenv("REPLY_SHADOW_LLM_PROVIDER", "mock")
    monkeypatch.setenv("REPLY_SHADOW_LLM_MODEL", "shadow-test")
    get_settings.cache_clear()
    conversation_case_service._sessionmakers.clear()
    yield
    conversation_case_service._sessionmakers.clear()
    get_settings.cache_clear()


def test_all_imported_conversations_are_exposed_as_whole_cases():
    result = conversation_case_service.list_conversation_cases()

    assert result["total"] == 77
    assert result["library_counts"] == {"complete": 77, "cleaned": 77}
    assert [item["case_id"] for item in result["items"]] == [
        f"case{number:03d}" for number in range(1, 78)
    ]
    detail = conversation_case_service.get_conversation_case("case012")
    assert detail is not None
    assert detail["schema_version"] == "conversation_case.v1"
    assert detail["library_type"] == "complete"
    assert detail["turn_count"] == 75
    assert detail["checkpoint_count"] == detail["customer_turn_count"]
    assert detail["turns"][0]["turn_id"].startswith("case012:turn:")
    assert all(
        turn["reference_only"] is (turn["role"] == "merchant")
        for turn in detail["turns"]
    )


def test_case_api_returns_full_transcript_and_jsonl_export():
    client = TestClient(app)

    listing = client.get("/api/v1/admin/conversation-cases")
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 77

    detail = client.get("/api/v1/admin/conversation-cases/case031")
    assert detail.status_code == 200
    assert detail.json()["data"]["case_id"] == "case031"
    assert detail.json()["data"]["library_type"] == "complete"

    cleaned = client.get(
        "/api/v1/admin/conversation-cases/case031",
        params={"library_type": "cleaned"},
    )
    assert cleaned.status_code == 200
    assert cleaned.json()["data"]["library_type"] == "cleaned"
    assert len(cleaned.json()["data"]["turns"]) == 4

    exported = client.get("/api/v1/admin/conversation-cases/export")
    assert exported.status_code == 200
    assert exported.text.count("\n") == 77


@pytest.mark.asyncio
async def test_full_case_shadow_run_keeps_candidate_history_independent(
    conversation_case_env,
    monkeypatch,
):
    prompts = []

    async def fake_generate_json(prompt, **kwargs):
        prompts.append(prompt)
        assert kwargs["shadow"] is True
        return {
            "sales_stage": "needs_discovery",
            "route": "clarify",
            "sales_action": "answer_then_clarify",
            "reply": f"candidate reply {len(prompts)}",
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
            "reason": "offline evaluation",
        }

    monkeypatch.setattr(
        conversation_case_service,
        "generate_json",
        fake_generate_json,
    )
    started = conversation_case_service.start_case_shadow_run("case031")
    await asyncio.gather(*list(conversation_case_service._tasks))

    detail = conversation_case_service.get_case_shadow_run(started["run_id"])
    assert detail is not None
    assert detail["status"] == "completed"
    assert detail["completed_checkpoints"] == 2
    assert len(detail["result"]["turn_results"]) == 2
    assert detail["result"]["summary"]["clean_checkpoints"] == 2
    assert detail["result"]["summary"]["repair_attempts"] == 0
    assert "candidate reply 1" in prompts[1]
    assert detail["result"]["turn_results"][0]["reference_is_gold"] is False
    assert (
        detail["result"]["turn_results"][0]["reference_reply"]
        not in prompts[0]
    )


@pytest.mark.asyncio
async def test_case_shadow_harness_repairs_unverified_rag_output(
    conversation_case_env,
    monkeypatch,
):
    calls = []

    async def fake_generate_json(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return {
                "sales_stage": "awareness",
                "route": "rag_answer",
                "sales_action": "answer",
                "reply": "具体用药是什么？剂量是多少？" + ("很长" * 120),
                "need_human": False,
                "next_action": None,
                "follow_up": {
                    "needed": False,
                    "action": None,
                    "due_in_hours": None,
                    "cancel_conditions": [],
                },
                "facts_used": ["invented_fact"],
                "confidence": 0.8,
                "reason": "first draft",
            }
        return {
            "sales_stage": "needs_analysis",
            "route": "clarify",
            "sales_action": "collect_evidence",
            "reply": "先隔离病株并保持通风。方便发一张叶片正反面和根部照片吗？",
            "need_human": False,
            "next_action": None,
            "follow_up": {
                "needed": False,
                "action": None,
                "due_in_hours": None,
                "cancel_conditions": [],
            },
            "facts_used": [],
            "confidence": 0.86,
            "reason": "no verified evidence",
        }

    monkeypatch.setattr(
        conversation_case_service,
        "generate_json",
        fake_generate_json,
    )
    case = conversation_case_service.get_conversation_case("case031", "cleaned")
    decision, issues, repaired = (
        await conversation_case_service._generate_case_shadow_decision(
            case=case,
            checkpoint=case["checkpoints"][0],
            candidate_history=[],
        )
    )

    assert repaired is True
    assert issues == []
    assert decision.route == "clarify"
    assert decision.facts_used == []
    assert len(calls) == 2
    assert "rag_without_evidence" in calls[1]
    assert "unverified_fact_usage" in calls[1]


@pytest.mark.asyncio
async def test_case_shadow_harness_normalizes_disabled_follow_up(
    conversation_case_env,
    monkeypatch,
):
    async def fake_generate_json(*args, **kwargs):
        return {
            "sales_stage": "needs_analysis",
            "route": "clarify",
            "sales_action": "collect_evidence",
            "reply": "方便发一张清晰照片吗？",
            "need_human": False,
            "next_action": None,
            "follow_up": {
                "needed": False,
                "action": "",
                "due_in_hours": 0,
                "cancel_conditions": ["unused"],
            },
            "facts_used": [],
            "confidence": 0.8,
            "reason": "need evidence",
        }

    monkeypatch.setattr(
        conversation_case_service,
        "generate_json",
        fake_generate_json,
    )
    case = conversation_case_service.get_conversation_case("case031", "cleaned")
    decision, issues, repaired = (
        await conversation_case_service._generate_case_shadow_decision(
            case=case,
            checkpoint=case["checkpoints"][0],
            candidate_history=[],
        )
    )

    assert repaired is False
    assert issues == []
    assert decision.follow_up.needed is False
    assert decision.follow_up.action is None
    assert decision.follow_up.due_in_hours is None
    assert decision.follow_up.cancel_conditions == []
