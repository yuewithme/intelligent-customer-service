from fastapi.testclient import TestClient

from app.domains.decisioning.services import conversation_case_service
from app.main import app


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
