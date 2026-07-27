import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.services.intent_taxonomy_migration_service import (
    migrate_dgi_v1_to_v2,
    requires_v2_reclassification,
)
from app.infrastructure.database.models import Base, IntentObservationModel


def test_merged_labels_are_mapped_without_reclassification():
    migrated = migrate_dgi_v1_to_v2(
        {
            "primary_domain": "commercial_decision",
            "secondary_domains": ["care_service"],
            "primary_goal": "negotiate",
            "secondary_goals": ["affirm"],
            "issues": ["price", "discount", "service_guarantee"],
            "scope": "in_scope",
        }
    )

    assert migrated == {
        "primary_domain": "commerce",
        "secondary_domains": ["care"],
        "primary_goal": "express_objection",
        "secondary_goals": ["confirm"],
        "issues": ["price_value", "trust_guarantee"],
        "scope": "in_scope",
        "taxonomy_version": "2.0",
    }
    assert requires_v2_reclassification(migrated) is False


def test_split_labels_require_ai_reclassification():
    assert requires_v2_reclassification(
        {
            "primary_goal": "request_material",
            "issues": ["care_general"],
        }
    )
    assert requires_v2_reclassification(
        {
            "primary_goal": "confirm_choice",
            "issues": ["sku_quantity"],
        }
    )


def test_out_of_scope_moves_to_scope_instead_of_domain():
    migrated = migrate_dgi_v1_to_v2(
        {
            "primary_domain": "out_of_scope",
            "primary_goal": "unclear",
            "issues": [],
            "scope": "in_scope",
        }
    )

    assert migrated["primary_domain"] == "conversation"
    assert migrated["scope"] == "out_of_scope"


def test_database_migration_maps_merges_and_reruns_splits(
    monkeypatch, tmp_path
):
    from scripts import migrate_intent_taxonomy_v2 as migration

    db_path = tmp_path / "taxonomy-migration.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("CHAT_LOG_DB_URL", database_url)
    get_settings.cache_clear()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add_all(
            [
                _observation(
                    trace_id="mapped",
                    domain="commercial_decision",
                    goal="negotiate",
                    issues=["discount"],
                    now=now,
                ),
                _observation(
                    trace_id="split",
                    domain="product_solution",
                    goal="confirm_choice",
                    issues=["sku_quantity"],
                    now=now,
                ),
            ]
        )
        session.commit()

    async def fake_reclassify(row):
        assert row.trace_id == "split"
        return IntentResult(
            route="rag_answer",
            primary_intent="knowledge_question",
            primary_domain="product",
            primary_goal="ask_information",
            issues=["product_information"],
            confidence=0.96,
            classifier_source="llm",
        )

    monkeypatch.setattr(migration, "_reclassify", fake_reclassify)
    result = asyncio.run(
        migration.run(
            apply=True,
            channels=["case"],
            reclassify_splits=True,
        )
    )

    assert result["mapped"] == 1
    assert result["reclassified"] == 1
    with Session(engine) as session:
        mapped = session.scalar(
            select(IntentObservationModel).where(
                IntentObservationModel.trace_id == "mapped"
            )
        )
        split = session.scalar(
            select(IntentObservationModel).where(
                IntentObservationModel.trace_id == "split"
            )
        )
        assert mapped.taxonomy_version == "2.0"
        assert mapped.primary_domain == "commerce"
        assert mapped.primary_goal == "express_objection"
        assert mapped.issues_json == '["price_value"]'
        assert split.taxonomy_version == "2.0"
        assert split.primary_domain == "product"
        assert split.primary_goal == "ask_information"
        assert split.issues_json == '["product_information"]'
        assert split.classifier_source == "taxonomy_v2_rerun"


def _observation(
    *,
    trace_id: str,
    domain: str,
    goal: str,
    issues: list[str],
    now: datetime,
) -> IntentObservationModel:
    return IntentObservationModel(
        trace_id=trace_id,
        channel="case",
        user_id="customer",
        user_message="测试消息",
        context_json="[]",
        taxonomy_version="1.0",
        classifier_source="case_import",
        candidate_labels_json="[]",
        primary_domain=domain,
        secondary_domains_json="[]",
        primary_goal=goal,
        secondary_goals_json="[]",
        issues_json=json.dumps(issues, ensure_ascii=False),
        scope="in_scope",
        evidence_json="[]",
        confidence=0.95,
        predicted_route="rag_answer",
        primary_intent="knowledge_question",
        status="observed",
        created_at=now,
        updated_at=now,
    )
