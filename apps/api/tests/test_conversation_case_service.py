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

    assert result["total"] == 47
    assert result["source_counts"] == {
        "case_01_10": 10,
        "first_order_cases": 20,
        "case_library_2": 17,
    }
    detail = conversation_case_service.get_conversation_case("case12")
    assert detail is not None
    assert detail["schema_version"] == "conversation_case.v1"
    assert detail["turn_count"] == 75
    assert detail["checkpoint_count"] == detail["customer_turn_count"]
    assert detail["turns"][0]["turn_id"].startswith("case12:turn:")
    assert all(
        turn["reference_only"] is (turn["role"] == "merchant")
        for turn in detail["turns"]
    )


def test_case_api_returns_full_transcript_and_jsonl_export():
    client = TestClient(app)

    listing = client.get("/api/v1/admin/conversation-cases")
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 47

    detail = client.get("/api/v1/admin/conversation-cases/case2_01")
    assert detail.status_code == 200
    assert detail.json()["data"]["case_id"] == "case2_01"
    assert len(detail.json()["data"]["turns"]) == 4

    exported = client.get("/api/v1/admin/conversation-cases/export")
    assert exported.status_code == 200
    assert exported.text.count("\n") == 47


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
    started = conversation_case_service.start_case_shadow_run("case2_01")
    await asyncio.gather(*list(conversation_case_service._tasks))

    detail = conversation_case_service.get_case_shadow_run(started["run_id"])
    assert detail is not None
    assert detail["status"] == "completed"
    assert detail["completed_checkpoints"] == 2
    assert len(detail["result"]["turn_results"]) == 2
    assert "candidate reply 1" in prompts[1]
    assert detail["result"]["turn_results"][0]["reference_is_gold"] is False
    assert (
        detail["result"]["turn_results"][0]["reference_reply"]
        not in prompts[0]
    )
