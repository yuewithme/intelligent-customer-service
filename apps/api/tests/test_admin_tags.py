import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.infrastructure.database.models import (
    PromptBlockModel,
    TagCatalogMetaModel,
    TagCategoryModel,
    TagDefinitionModel,
    TagPromptBindingModel,
    UserProfileModel,
)
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
    assert data["total_categories"] == 14
    assert data["total_tags"] > 60
    categories = {item["id"]: item for item in data["items"]}
    assert categories["purchase_status"]["ai_assignable"] is False
    assert categories["service_status"]["ai_assignable"] is False
    assert categories["purchase_status"]["profile_assignable"] is False
    assert categories["customer_sentiment"]["profile_assignable"] is False
    assert {"customer_sentiment", "risk_level", "pain_point"} <= set(categories)
    assert categories["favorite_orchid_type"]["exclusive"] is False
    assert categories["product_demand"]["exclusive"] is False
    assert categories["price_range"]["exclusive"] is True
    assert categories["growing_environment"]["exclusive"] is True
    assert "intent" not in categories
    assert "sales_stage" not in categories
    quantity = next(
        tag for tag in categories["orchid_quantity"]["tags"] if tag["value"] == "1-9盆"
    )
    assert quantity["prompts"][0]["content"].startswith("The user keeps a small")
    hainan = next(
        tag for tag in categories["province"]["tags"] if tag["value"] == "海南省"
    )
    assert hainan["prompts"][0]["block_id"] == "region.south_china.variety"


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
    tag = next(item for item in quantity["tags"] if item["value"] == "1-9盆")

    response = client.put(
        f"/api/v1/admin/tags/items/{tag['id']}",
        json={"value": tag["value"], "prompts": []},
    )

    assert response.status_code == 200
    assert response.json()["data"]["prompts"] == []
    assert client.get("/api/v1/admin/tags").status_code == 200
    assert business_tag_prompt_service.get_business_tag_prompt_block_ids(
        ["customer_tag:1-9盆"]
    ) == []


def test_catalog_upgrade_removes_obsolete_stage_bindings_and_orphan_blocks():
    client = TestClient(app)
    assert client.get("/api/v1/admin/tags").status_code == 200

    with tag_catalog._get_session() as session:
        session.add(
            TagDefinitionModel(
                category_id="sales_stage",
                value="stage:greeting",
                position=99,
            )
        )
        session.add(
            PromptBlockModel(
                block_id="legacy.sales_stage.greeting",
                title="旧破冰话术",
                content="legacy",
                enabled=True,
            )
        )
        session.add(
            TagPromptBindingModel(
                category_id="sales_stage",
                tag_value="stage:greeting",
                prompt_block_id="legacy.sales_stage.greeting",
                priority=0,
                enabled=True,
            )
        )
        session.get(TagCatalogMetaModel, "seed_version").value = "3"
        session.commit()

    tag_catalog.invalidate_cache()
    assert client.get("/api/v1/admin/tags").status_code == 200

    with tag_catalog._get_session() as session:
        assert session.scalar(
            select(TagDefinitionModel.id).where(
                TagDefinitionModel.value == "stage:greeting"
            )
        ) is None
        assert session.scalar(
            select(TagPromptBindingModel.id).where(
                TagPromptBindingModel.tag_value == "stage:greeting"
            )
        ) is None
        assert session.get(PromptBlockModel, "legacy.sales_stage.greeting") is None


def test_catalog_upgrade_adds_manual_service_status_category():
    client = TestClient(app)
    assert client.get("/api/v1/admin/tags").status_code == 200
    with tag_catalog._get_session() as session:
        session.execute(
            delete(TagDefinitionModel).where(
                TagDefinitionModel.category_id == "service_status"
            )
        )
        session.execute(
            delete(TagCategoryModel).where(TagCategoryModel.id == "service_status")
        )
        session.get(TagCatalogMetaModel, "seed_version").value = "5"
        session.commit()

    tag_catalog.invalidate_cache()
    categories = tag_catalog.get_tag_categories()

    assert categories["service_status"].name == "服务标签"
    assert [value.name for value in categories["service_status"].values] == ["服务中"]
    assert categories["service_status"].ai_assignable is False


def test_catalog_upgrade_renames_legacy_values_and_profile_tags():
    client = TestClient(app)
    client.patch(
        "/api/v1/users/catalog-upgrade/profile",
        json={
            "customer_tags": [
                "1-9盆",
                "广西壮族自治区",
                "51-100元",
            ]
        },
    )
    assert client.get("/api/v1/admin/tags").status_code == 200

    with tag_catalog._get_session() as session:
        quantity = session.scalar(
            select(TagDefinitionModel).where(TagDefinitionModel.value == "1-9盆")
        )
        province = session.scalar(
            select(TagDefinitionModel).where(
                TagDefinitionModel.value == "广西壮族自治区"
            )
        )
        large_quantity = session.scalar(
            select(TagDefinitionModel).where(
                TagDefinitionModel.value == "500-999盆"
            )
        )
        legacy_exact_price = session.scalar(
            select(TagDefinitionModel).where(
                TagDefinitionModel.value == "50元以内"
            )
        )
        quantity.value = "1-10盆"
        province.value = "广西省"
        large_quantity.value = "500+盆"
        legacy_exact_price.category_id = "product_demand"
        session.add(
            TagDefinitionModel(
                category_id="product_demand",
                value="接受50-100以内",
                position=99,
            )
        )
        session.add(
            TagDefinitionModel(
                category_id="product_demand",
                value="接受50元以内",
                position=98,
            )
        )
        session.add(
            TagDefinitionModel(
                category_id="orchid_quantity",
                value="800+盆",
                position=99,
            )
        )
        for binding in session.scalars(
            select(TagPromptBindingModel).where(
                TagPromptBindingModel.tag_value.in_(
                    ["1-9盆", "广西壮族自治区", "500-999盆"]
                )
            )
        ):
            binding.tag_value = {
                "1-9盆": "1-10盆",
                "广西壮族自治区": "广西省",
                "500-999盆": "500+盆",
            }[binding.tag_value]
        profile = session.get(UserProfileModel, "catalog-upgrade")
        profile.customer_tags_json = json.dumps(
            ["500+盆", "广西省", "接受50-100以内"], ensure_ascii=False
        )
        session.get(TagCatalogMetaModel, "seed_version").value = "6"
        session.commit()

    tag_catalog.invalidate_cache()
    categories = tag_catalog.get_tag_categories()
    profile = client.get("/api/v1/users/catalog-upgrade/profile").json()["data"]["profile"]

    assert "product_demand" in categories
    assert categories["favorite_orchid_type"].exclusive is False
    assert "50元以内" in {
        value.name for value in categories["price_range"].values
    }
    assert profile["customer_tags"] == [
        "广西壮族自治区",
        "500-999盆",
        "51-100元",
    ]


def test_tag_admin_requires_api_authorization(monkeypatch):
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY", "tag-admin-key")
    get_settings.cache_clear()

    response = TestClient(app).get("/api/v1/admin/tags")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
