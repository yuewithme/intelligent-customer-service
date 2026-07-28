from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.domains.decisioning.services.intent_taxonomy_service import (
    prepare_intent_payload,
)
from app.domains.decisioning.services.intent_taxonomy_v21_migration_service import (
    TARGET_TAXONOMY_VERSION,
    migrate_dgi_to_v21,
)
from app.infrastructure.database.models import (
    IntentAnnotationModel,
    IntentObservationModel,
)


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _observation_payload(row: IntentObservationModel) -> dict[str, Any]:
    return {
        "primary_domain": row.primary_domain,
        "secondary_domains": _json(row.secondary_domains_json, []),
        "primary_goal": row.primary_goal,
        "secondary_goals": _json(row.secondary_goals_json, []),
        "issues": _json(row.issues_json, []),
        "scope": row.scope,
    }


def _annotation_payload(row: IntentAnnotationModel) -> dict[str, Any]:
    return {
        "primary_domain": row.primary_domain,
        "secondary_domains": _json(row.secondary_domains_json, []),
        "primary_goal": row.primary_goal,
        "secondary_goals": _json(row.secondary_goals_json, []),
        "issues": _json(row.issues_json, []),
        "scope": row.scope,
    }


def run(*, apply: bool, channels: list[str]) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(
        settings.chat_log_db_url,
        connect_args=(
            {"timeout": 30}
            if settings.chat_log_db_url.startswith("sqlite")
            else {}
        ),
    )
    with Session(engine) as session:
        row_ids = list(
            session.scalars(
                select(IntentObservationModel.id)
                .where(
                    IntentObservationModel.channel.in_(channels),
                    IntentObservationModel.taxonomy_version
                    != TARGET_TAXONOMY_VERSION,
                )
                .order_by(IntentObservationModel.id)
            ).all()
        )

    summary = {
        "scanned": len(row_ids),
        "updated": 0,
        "material_restored": 0,
        "annotation_migrated": 0,
        "channels": channels,
        "target_version": TARGET_TAXONOMY_VERSION,
        "applied": apply,
    }
    for row_id in row_ids:
        with Session(engine) as session:
            row = session.get(IntentObservationModel, row_id)
            if row is None or row.taxonomy_version == TARGET_TAXONOMY_VERSION:
                continue
            source_version = row.taxonomy_version
            previous_payload = _observation_payload(row)
            migrated = migrate_dgi_to_v21(
                previous_payload,
                source_version=source_version,
            )
            if (
                previous_payload.get("primary_goal") != "request_material"
                and migrated["primary_goal"] == "request_material"
            ):
                summary["material_restored"] += 1
            if not apply:
                summary["updated"] += 1
                continue

            prepared = prepare_intent_payload(migrated)
            row.primary_domain = prepared["primary_domain"]
            row.secondary_domains_json = _dump(prepared["secondary_domains"])
            row.primary_goal = prepared["primary_goal"]
            row.secondary_goals_json = _dump(prepared["secondary_goals"])
            row.issues_json = _dump(prepared["issues"])
            row.scope = prepared["scope"]
            row.taxonomy_version = TARGET_TAXONOMY_VERSION
            row.predicted_route = prepared["route"]
            row.primary_intent = prepared["primary_intent"]
            row.candidate_labels_json = "[]"
            row.updated_at = datetime.now(timezone.utc)

            latest_annotation = session.scalar(
                select(IntentAnnotationModel)
                .where(IntentAnnotationModel.observation_id == row.id)
                .order_by(IntentAnnotationModel.id.desc())
                .limit(1)
            )
            if (
                latest_annotation is not None
                and latest_annotation.taxonomy_version
                != TARGET_TAXONOMY_VERSION
            ):
                corrected = latest_annotation.status == "corrected"
                annotation_migrated = (
                    migrate_dgi_to_v21(
                        _annotation_payload(latest_annotation),
                        source_version=latest_annotation.taxonomy_version,
                    )
                    if corrected
                    else migrated
                )
                session.add(
                    IntentAnnotationModel(
                        observation_id=row.id,
                        trace_id=row.trace_id,
                        status=latest_annotation.status,
                        primary_domain=(
                            annotation_migrated["primary_domain"]
                            if corrected
                            else None
                        ),
                        secondary_domains_json=_dump(
                            annotation_migrated["secondary_domains"]
                            if corrected
                            else []
                        ),
                        primary_goal=(
                            annotation_migrated["primary_goal"]
                            if corrected
                            else None
                        ),
                        secondary_goals_json=_dump(
                            annotation_migrated["secondary_goals"]
                            if corrected
                            else []
                        ),
                        issues_json=_dump(
                            annotation_migrated["issues"] if corrected else []
                        ),
                        scope=annotation_migrated["scope"] if corrected else None,
                        note="恢复“索要资料”独立意图卡并迁移至 taxonomy 2.1。",
                        annotator_id="taxonomy-v21-migration",
                        taxonomy_version=TARGET_TAXONOMY_VERSION,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                summary["annotation_migrated"] += 1
            session.commit()
            summary["updated"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        choices=("case", "wechat"),
    )
    args = parser.parse_args()
    result = run(
        apply=args.apply,
        channels=args.channels or ["case", "wechat"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
