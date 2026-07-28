import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domains.decisioning.services.intent_taxonomy_v21_migration_service import (
    migrate_dgi_to_v21,
)
from app.infrastructure.database.models import (
    Base,
    IntentAnnotationModel,
    IntentObservationModel,
)


def test_material_goal_is_restored_without_changing_normal_questions():
    material = migrate_dgi_to_v21(
        {
            "primary_domain": "care",
            "primary_goal": "ask_information",
            "issues": ["material_resource"],
            "scope": "in_scope",
        },
        source_version="2.0",
    )
    question = migrate_dgi_to_v21(
        {
            "primary_domain": "care",
            "primary_goal": "ask_information",
            "issues": ["routine_care"],
            "scope": "in_scope",
        },
        source_version="2.0",
    )

    assert material["primary_goal"] == "request_material"
    assert material["issues"] == ["material_resource"]
    assert material["taxonomy_version"] == "2.1"
    assert question["primary_goal"] == "ask_information"


def test_v21_database_migration_is_deterministic_and_idempotent(
    monkeypatch, tmp_path
):
    from scripts import migrate_intent_taxonomy_v21 as migration

    database_url = f"sqlite:///{(tmp_path / 'taxonomy-v21.db').as_posix()}"
    monkeypatch.setenv("CHAT_LOG_DB_URL", database_url)
    get_settings.cache_clear()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        material = _observation(
            trace_id="material",
            goal="ask_information",
            issues=["material_resource"],
            now=now,
        )
        ordinary = _observation(
            trace_id="ordinary",
            goal="ask_information",
            issues=["routine_care"],
            now=now,
        )
        session.add_all([material, ordinary])
        session.flush()
        session.add(
            IntentAnnotationModel(
                observation_id=material.id,
                trace_id=material.trace_id,
                status="corrected",
                primary_domain="care",
                secondary_domains_json="[]",
                primary_goal="ask_information",
                secondary_goals_json="[]",
                issues_json='["material_resource"]',
                scope="in_scope",
                annotator_id="reviewer",
                taxonomy_version="2.0",
                created_at=now,
            )
        )
        session.commit()

    dry_run = migration.run(apply=False, channels=["case"])
    applied = migration.run(apply=True, channels=["case"])
    repeated = migration.run(apply=True, channels=["case"])

    assert dry_run["material_restored"] == 1
    assert applied["updated"] == 2
    assert applied["material_restored"] == 1
    assert applied["annotation_migrated"] == 1
    assert repeated["scanned"] == 0

    with Session(engine) as session:
        rows = {
            row.trace_id: row
            for row in session.scalars(select(IntentObservationModel)).all()
        }
        assert rows["material"].primary_goal == "request_material"
        assert rows["material"].taxonomy_version == "2.1"
        assert rows["ordinary"].primary_goal == "ask_information"
        assert rows["ordinary"].taxonomy_version == "2.1"
        latest = session.scalar(
            select(IntentAnnotationModel)
            .where(IntentAnnotationModel.trace_id == "material")
            .order_by(IntentAnnotationModel.id.desc())
            .limit(1)
        )
        assert latest.primary_goal == "request_material"
        assert latest.taxonomy_version == "2.1"


def _observation(
    *,
    trace_id: str,
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
        taxonomy_version="2.0",
        classifier_source="case_import",
        candidate_labels_json="[]",
        primary_domain="care",
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
