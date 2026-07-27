from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.intent_service import classify_by_llm
from app.domains.decisioning.services.intent_taxonomy_migration_service import (
    migrate_dgi_v1_to_v2,
    requires_v2_reclassification,
)
from app.domains.decisioning.services.intent_taxonomy_service import (
    prepare_intent_payload,
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


def _row_payload(row: IntentObservationModel) -> dict[str, Any]:
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


async def _reclassify(row: IntentObservationModel):
    context = _json(row.context_json, [])
    message = NormalizedMessage(
        trace_id=row.trace_id,
        channel=row.channel,
        user_id=row.user_id,
        session_id=row.session_id or "default",
        message_id=row.message_id,
        message=row.user_message,
        kb_id=get_settings().wechat_default_kb_id,
        metadata={"origin": "taxonomy_v2_migration"},
    )
    return await classify_by_llm(
        message,
        UserState(
            user_id=row.user_id,
            session_id=row.session_id or "default",
            metadata={"recent_turns": context},
        ),
        [],
    )


def _update_observation(
    row: IntentObservationModel,
    payload: dict[str, Any],
    *,
    confidence: float | None = None,
    classifier_source: str | None = None,
    raw_prediction: dict[str, Any] | None = None,
) -> None:
    previous_taxonomy_version = row.taxonomy_version
    prepared = prepare_intent_payload(payload)
    row.primary_domain = prepared["primary_domain"]
    row.secondary_domains_json = _dump(prepared["secondary_domains"])
    row.primary_goal = prepared["primary_goal"]
    row.secondary_goals_json = _dump(prepared["secondary_goals"])
    row.issues_json = _dump(prepared["issues"])
    row.scope = prepared["scope"]
    row.taxonomy_version = "2.0"
    row.predicted_route = prepared["route"]
    row.primary_intent = prepared["primary_intent"]
    if confidence is not None:
        row.confidence = confidence
    if classifier_source:
        row.classifier_source = classifier_source
    if raw_prediction is not None:
        previous = _json(row.raw_prediction_json, {})
        row.raw_prediction_json = _dump(
            {
                "taxonomy_migration": {
                    "from": previous_taxonomy_version,
                    "to": "2.0",
                    "previous": previous,
                },
                "prediction": raw_prediction,
            }
        )
    row.candidate_labels_json = "[]"
    row.evidence_json = "[]"
    row.updated_at = datetime.now(timezone.utc)


def _append_v2_annotation(
    session: Session,
    observation: IntentObservationModel,
    previous: IntentAnnotationModel,
    payload: dict[str, Any],
    *,
    status: str | None = None,
) -> None:
    session.add(
        IntentAnnotationModel(
            observation_id=observation.id,
            trace_id=observation.trace_id,
            status=status or previous.status,
            primary_domain=payload.get("primary_domain"),
            secondary_domains_json=_dump(payload.get("secondary_domains", [])),
            primary_goal=payload.get("primary_goal"),
            secondary_goals_json=_dump(payload.get("secondary_goals", [])),
            issues_json=_dump(payload.get("issues", [])),
            scope=payload.get("scope"),
            note="由 taxonomy v1 标注确定性迁移至 v2；原标注历史已保留。",
            annotator_id="taxonomy-v2-migration",
            taxonomy_version="2.0",
            created_at=datetime.now(timezone.utc),
        )
    )


async def run(
    *,
    apply: bool,
    channels: list[str],
    reclassify_splits: bool,
) -> dict[str, Any]:
    engine = create_engine(get_settings().chat_log_db_url)
    summary = {
        "scanned": 0,
        "mapped": 0,
        "reclassified": 0,
        "needs_reclassification": 0,
        "annotation_migrated": 0,
        "channels": channels,
        "applied": apply,
    }
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(IntentObservationModel)
                .where(IntentObservationModel.channel.in_(channels))
                .order_by(IntentObservationModel.id)
            )
        )
        for row in rows:
            if row.taxonomy_version == "2.0":
                continue
            summary["scanned"] += 1
            old_payload = _row_payload(row)
            split = requires_v2_reclassification(old_payload)
            if split and reclassify_splits:
                predicted = await _reclassify(row)
                payload = {
                    "primary_domain": predicted.primary_domain,
                    "secondary_domains": predicted.secondary_domains,
                    "primary_goal": predicted.primary_goal,
                    "secondary_goals": predicted.secondary_goals,
                    "issues": predicted.issues,
                    "scope": predicted.scope,
                }
                summary["reclassified"] += 1
            else:
                predicted = None
                payload = migrate_dgi_v1_to_v2(old_payload)
                summary["mapped"] += 1
                if split:
                    summary["needs_reclassification"] += 1
            if not apply:
                continue
            _update_observation(
                row,
                payload,
                confidence=predicted.confidence if predicted else None,
                classifier_source="taxonomy_v2_rerun" if predicted else None,
                raw_prediction=predicted.raw_prediction if predicted else None,
            )
            latest_annotation = session.scalar(
                select(IntentAnnotationModel)
                .where(IntentAnnotationModel.observation_id == row.id)
                .order_by(IntentAnnotationModel.id.desc())
                .limit(1)
            )
            if (
                latest_annotation is not None
                and latest_annotation.taxonomy_version != "2.0"
            ):
                annotation_payload = (
                    payload
                    if split
                    else migrate_dgi_v1_to_v2(
                        _annotation_payload(latest_annotation)
                    )
                )
                _append_v2_annotation(
                    session,
                    row,
                    latest_annotation,
                    annotation_payload,
                    status=(
                        latest_annotation.status
                        if not split or latest_annotation.status == "excluded"
                        else (
                            "confirmed"
                            if (predicted and predicted.confidence >= get_settings().intent_auto_confirm_threshold)
                            else "uncertain"
                        )
                    ),
                )
                summary["annotation_migrated"] += 1
        if apply:
            session.commit()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reclassify-splits", action="store_true")
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        choices=("case", "wechat"),
    )
    args = parser.parse_args()
    if args.apply and not args.reclassify_splits:
        parser.error("--apply requires --reclassify-splits")
    summary = asyncio.run(
        run(
            apply=args.apply,
            channels=args.channels or ["case", "wechat"],
            reclassify_splits=args.reclassify_splits,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
