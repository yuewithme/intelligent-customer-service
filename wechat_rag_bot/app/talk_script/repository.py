import json
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    QuestionClusterModel,
    SceneIndexModel,
    TalkScriptMatchLogModel,
    TemplateLibraryModel,
)


_sessionmakers: dict[str, sessionmaker] = {}
_TABLES = [
    SceneIndexModel.__table__,
    QuestionClusterModel.__table__,
    TemplateLibraryModel.__table__,
    TalkScriptMatchLogModel.__table__,
]


def get_session() -> Session:
    db_url = get_settings().database_url
    factory = _sessionmakers.get(db_url)
    if factory is None:
        engine = create_engine(db_url)
        Base.metadata.create_all(engine, tables=_TABLES)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[db_url] = factory
    return factory()


def replace_talk_script_library(
    *,
    scenes: list[dict],
    questions: list[dict],
    templates: list[dict],
) -> None:
    with get_session() as session:
        session.query(TalkScriptMatchLogModel).delete()
        session.query(TemplateLibraryModel).delete()
        session.query(QuestionClusterModel).delete()
        session.query(SceneIndexModel).delete()
        session.add_all(SceneIndexModel(**_scene_payload(row)) for row in scenes)
        session.add_all(
            QuestionClusterModel(**_question_payload(row)) for row in questions
        )
        session.add_all(
            TemplateLibraryModel(**_template_payload(row)) for row in templates
        )
        session.commit()


def list_active_scenes() -> list[SceneIndexModel]:
    with get_session() as session:
        return list(
            session.scalars(
                select(SceneIndexModel)
                .where(SceneIndexModel.status == "active")
                .order_by(SceneIndexModel.priority.desc(), SceneIndexModel.scene_id)
            )
        )


def list_active_questions(scene_id: str) -> list[QuestionClusterModel]:
    with get_session() as session:
        return list(
            session.scalars(
                select(QuestionClusterModel)
                .where(
                    QuestionClusterModel.scene_id == scene_id,
                    QuestionClusterModel.status == "active",
                )
                .order_by(
                    QuestionClusterModel.priority.desc(),
                    QuestionClusterModel.question_id,
                )
            )
        )


def get_active_question(question_id: str) -> QuestionClusterModel | None:
    with get_session() as session:
        return session.scalar(
            select(QuestionClusterModel).where(
                QuestionClusterModel.question_id == question_id,
                QuestionClusterModel.status == "active",
            )
        )


def get_active_template(template_id: str) -> TemplateLibraryModel | None:
    with get_session() as session:
        return session.scalar(
            select(TemplateLibraryModel).where(
                TemplateLibraryModel.template_id == template_id,
                TemplateLibraryModel.status == "active",
            )
        )


def get_active_template_by_question_id(question_id: str) -> TemplateLibraryModel | None:
    question = get_active_question(question_id)
    if question is None:
        return None
    return get_active_template(question.default_template_id)


def record_match_log(payload: dict) -> None:
    with get_session() as session:
        session.add(
            TalkScriptMatchLogModel(
                trace_id=payload.get("trace_id"),
                customer_id=payload.get("customer_id"),
                session_id=payload.get("session_id"),
                user_message=str(payload.get("user_message") or ""),
                normalized_message=payload.get("normalized_message"),
                status=str(payload.get("status") or "pass_through"),
                scene_id=payload.get("scene_id"),
                candidate_question_ids_json=json.dumps(
                    payload.get("candidate_question_ids", []), ensure_ascii=False
                ),
                matched_question_id=payload.get("matched_question_id"),
                template_id=payload.get("template_id"),
                confidence=payload.get("confidence"),
                need_slot_filling=bool(payload.get("need_slot_filling", False)),
                need_human=bool(payload.get("need_human", False)),
                final_answer=payload.get("final_answer"),
                match_reason=payload.get("match_reason"),
                created_at=payload.get("created_at") or datetime.now(timezone.utc),
            )
        )
        session.commit()


def _scene_payload(row: dict) -> dict:
    return {
        "scene_id": str(row.get("scene_id") or "").strip(),
        "scene_name": str(row.get("scene_name") or "").strip(),
        "scene_definition": _optional(row.get("scene_definition")),
        "enter_conditions": _optional(row.get("enter_conditions")),
        "typical_user_messages": _optional(row.get("typical_user_messages")),
        "exclude_conditions": _optional(row.get("exclude_conditions")),
        "priority": _int(row.get("priority")),
        "status": str(row.get("status") or "active").strip() or "active",
    }


def _question_payload(row: dict) -> dict:
    return {
        "question_id": str(row.get("question_id") or "").strip(),
        "scene_id": str(row.get("scene_id") or "").strip(),
        "sub_scene_name": _optional(row.get("sub_scene_name")),
        "standard_question": str(row.get("standard_question") or "").strip(),
        "core_intent": _optional(row.get("core_intent")),
        "user_question_aliases": _optional(row.get("user_question_aliases")),
        "positive_examples": _optional(row.get("positive_examples")),
        "negative_examples": _optional(row.get("negative_examples")),
        "keywords": _optional(row.get("keywords")),
        "required_conditions": _optional(row.get("required_conditions")),
        "exclude_conditions": _optional(row.get("exclude_conditions")),
        "confusable_questions": _optional(row.get("confusable_questions")),
        "default_template_id": str(row.get("default_template_id") or "").strip(),
        "confidence_threshold": _float(row.get("confidence_threshold"), 0.75),
        "priority": _int(row.get("priority")),
        "status": str(row.get("status") or "active").strip() or "active",
    }


def _template_payload(row: dict) -> dict:
    return {
        "template_id": str(row.get("template_id") or "").strip(),
        "question_id": str(row.get("question_id") or "").strip(),
        "template_name": _optional(row.get("template_name")),
        "answer_default": str(row.get("answer_default") or "").strip(),
        "answer_goal": _optional(row.get("answer_goal")),
        "need_slot_filling": str(row.get("need_slot_filling") or "no").strip() or "no",
        "handoff_rule": _optional(row.get("handoff_rule")),
        "status": str(row.get("status") or "active").strip() or "active",
        "version": _optional(row.get("version")),
        "change_note": _optional(row.get("change_note")),
    }


def _optional(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
