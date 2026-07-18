import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import (
    admin_tag_service,
    business_tag_prompt_service,
    customer_level_service,
    state_service,
    tag_catalog,
    user_profile_service,
)


@pytest.fixture(autouse=True)
def isolated_tag_admin_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'admin_tags.db').as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    admin_tag_service.clear_cache()
    business_tag_prompt_service.clear_cache()
    customer_level_service.clear_cache()
    tag_catalog.clear_cache()
    user_profile_service._sessionmakers.clear()
    state_service._state_store.clear()
    yield
    state_service._state_store.clear()
    user_profile_service._sessionmakers.clear()
    tag_catalog.clear_cache()
    customer_level_service.clear_cache()
    business_tag_prompt_service.clear_cache()
    admin_tag_service.clear_cache()
    get_settings.cache_clear()


def test_tag_admin_lists_all_categories_and_prompt_configuration():
    response = TestClient(app).get("/api/v1/admin/tags")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_categories"] == 5
    assert data["total_tags"] > 40
    categories = {item["id"]: item for item in data["items"]}
    assert categories["purchase_status"]["ai_assignable"] is False
    quantity = next(
        tag for tag in categories["orchid_quantity"]["tags"] if tag["value"] == "1-10盆"
    )
    assert quantity["prompts"][0]["content"].startswith("The user keeps a small")


def test_tag_crud_updates_live_prompt_policy_and_customer_profiles():
    client = TestClient(app)
    category = client.post(
        "/api/v1/admin/tags/categories",
        json={
            "id": "service_preference",
            "name": "服务偏好",
            "prompt_rule": "用于调整回复方式",
            "ai_assignable": True,
            "exclusive": True,
        },
    )
    assert category.status_code == 200

    created = client.post(
        "/api/v1/admin/tags/categories/service_preference/items",
        json={
            "value": "喜欢简洁回复",
            "prompts": [
                {"title": "简洁沟通", "content": "Keep the answer concise and actionable."}
            ],
        },
    )
    assert created.status_code == 200
    tag = created.json()["data"]
    assert business_tag_prompt_service.get_business_tag_prompt_block_ids(
        ["customer_tag:喜欢简洁回复"]
    ) == [tag["prompts"][0]["block_id"]]

    profile = client.patch(
        "/api/v1/users/tag-user/profile",
        json={"customer_tags": ["喜欢简洁回复"]},
    )
    assert profile.json()["data"]["profile"]["customer_tags"] == ["喜欢简洁回复"]

    updated = client.put(
        f"/api/v1/admin/tags/items/{tag['id']}",
        json={
            "value": "偏好简洁回复",
            "prompts": [
                {
                    "block_id": tag["prompts"][0]["block_id"],
                    "title": "简洁沟通",
                    "content": "Keep every answer short and actionable.",
                }
            ],
        },
    )
    assert updated.status_code == 200
    renamed_profile = client.get("/api/v1/users/tag-user/profile").json()["data"]["profile"]
    assert renamed_profile["customer_tags"] == ["偏好简洁回复"]

    deleted = client.delete(f"/api/v1/admin/tags/items/{tag['id']}")
    assert deleted.status_code == 200
    cleaned_profile = client.get("/api/v1/users/tag-user/profile").json()["data"]["profile"]
    assert cleaned_profile["customer_tags"] == []
    assert business_tag_prompt_service.get_business_tag_prompt_block_ids(
        ["customer_tag:偏好简洁回复"]
    ) == []


def test_removing_last_business_prompt_does_not_reseed_it():
    client = TestClient(app)
    catalog = client.get("/api/v1/admin/tags").json()["data"]
    quantity = next(item for item in catalog["items"] if item["id"] == "orchid_quantity")
    tag = next(item for item in quantity["tags"] if item["value"] == "1-10盆")

    response = client.put(
        f"/api/v1/admin/tags/items/{tag['id']}",
        json={"value": tag["value"], "prompts": []},
    )

    assert response.status_code == 200
    assert response.json()["data"]["prompts"] == []
    assert client.get("/api/v1/admin/tags").status_code == 200
    assert business_tag_prompt_service.get_business_tag_prompt_block_ids(
        ["customer_tag:1-10盆"]
    ) == []


def test_tag_admin_requires_api_authorization(monkeypatch):
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY", "tag-admin-key")
    get_settings.cache_clear()

    response = TestClient(app).get("/api/v1/admin/tags")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
