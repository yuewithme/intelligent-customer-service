import json
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import create_engine, func, inspect, select, text
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
        _ensure_template_library_columns(engine)
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


def list_sales_templates(
    *,
    sales_stage: str | None = None,
    sales_action: str | None = None,
    branch_code: str | None = None,
    status: str | None = "active",
) -> list[TemplateLibraryModel]:
    filters = [TemplateLibraryModel.sales_stage.is_not(None)]
    if sales_stage:
        filters.append(TemplateLibraryModel.sales_stage == sales_stage)
    if sales_action:
        filters.append(TemplateLibraryModel.sales_action == sales_action)
    if branch_code:
        filters.append(TemplateLibraryModel.branch_code == branch_code)
    if status:
        filters.append(TemplateLibraryModel.status == status)
    with get_session() as session:
        return list(
            session.scalars(
                select(TemplateLibraryModel)
                .where(*filters)
                .order_by(
                    TemplateLibraryModel.priority.desc(),
                    TemplateLibraryModel.template_id,
                )
            )
        )


def upsert_sales_templates(templates: Iterable[dict]) -> int:
    count = 0
    with get_session() as session:
        for row in templates:
            payload = _template_payload(row)
            existing = session.get(TemplateLibraryModel, payload["template_id"])
            if existing is None:
                session.add(TemplateLibraryModel(**payload))
            else:
                for key, value in payload.items():
                    setattr(existing, key, value)
            count += 1
        session.commit()
    return count


def has_sent_template_to_customer(customer_id: str, template_id: str) -> bool:
    if not customer_id or not template_id:
        return False
    with get_session() as session:
        return (
            session.scalar(
                select(TalkScriptMatchLogModel.id)
                .where(
                    TalkScriptMatchLogModel.customer_id == customer_id,
                    TalkScriptMatchLogModel.template_id == template_id,
                    TalkScriptMatchLogModel.status == "matched",
                    TalkScriptMatchLogModel.final_answer.is_not(None),
                    TalkScriptMatchLogModel.final_answer != "",
                )
                .limit(1)
            )
            is not None
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


def list_match_logs(
    *,
    page: int = 1,
    page_size: int = 50,
    customer_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    scene_id: str | None = None,
    template_id: str | None = None,
    need_human: bool | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    filters = _match_log_filters(
        customer_id=customer_id,
        session_id=session_id,
        trace_id=trace_id,
        status=status,
        scene_id=scene_id,
        template_id=template_id,
        need_human=need_human,
    )

    with get_session() as session:
        total = session.scalar(
            select(func.count()).select_from(TalkScriptMatchLogModel).where(*filters)
        )
        rows = session.scalars(
            select(TalkScriptMatchLogModel)
            .where(*filters)
            .order_by(
                TalkScriptMatchLogModel.created_at.desc(),
                TalkScriptMatchLogModel.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {
        "items": [_match_log_to_item(row) for row in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


def get_match_log_stats(
    *,
    customer_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    scene_id: str | None = None,
    template_id: str | None = None,
    need_human: bool | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    low_confidence_threshold: float = 0.5,
    low_confidence_limit: int = 20,
) -> dict:
    filters = _match_log_filters(
        customer_id=customer_id,
        session_id=session_id,
        trace_id=trace_id,
        status=status,
        scene_id=scene_id,
        template_id=template_id,
        need_human=need_human,
        start_time=start_time,
        end_time=end_time,
    )
    with get_session() as session:
        total = int(
            session.scalar(
                select(func.count()).select_from(TalkScriptMatchLogModel).where(*filters)
            )
            or 0
        )
        avg_confidence = session.scalar(
            select(func.avg(TalkScriptMatchLogModel.confidence)).where(*filters)
        )
        human_count = int(
            session.scalar(
                select(func.count())
                .select_from(TalkScriptMatchLogModel)
                .where(*filters, TalkScriptMatchLogModel.need_human.is_(True))
            )
            or 0
        )
        status_counts = _count_by(session, TalkScriptMatchLogModel.status, filters)
        reason_counts = _count_by(session, TalkScriptMatchLogModel.match_reason, filters)
        scene_counts = _count_by(session, TalkScriptMatchLogModel.scene_id, filters)
        template_counts = _count_by(session, TalkScriptMatchLogModel.template_id, filters)
        low_confidence_rows = session.scalars(
            select(TalkScriptMatchLogModel)
            .where(
                *filters,
                TalkScriptMatchLogModel.confidence.is_not(None),
                TalkScriptMatchLogModel.confidence < low_confidence_threshold,
            )
            .order_by(
                TalkScriptMatchLogModel.created_at.desc(),
                TalkScriptMatchLogModel.id.desc(),
            )
            .limit(max(0, min(low_confidence_limit, 100)))
        ).all()

    return {
        "total": total,
        "matched_count": status_counts.get("matched", 0),
        "handoff_count": status_counts.get("handoff", 0),
        "pass_through_count": status_counts.get("pass_through", 0),
        "human_count": human_count,
        "avg_confidence": round(float(avg_confidence), 2)
        if avg_confidence is not None
        else None,
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "scene_counts": scene_counts,
        "template_counts": template_counts,
        "low_confidence_items": [_match_log_to_item(row) for row in low_confidence_rows],
    }


def _match_log_filters(
    *,
    customer_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    status: str | None = None,
    scene_id: str | None = None,
    template_id: str | None = None,
    need_human: bool | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list:
    filters = []
    if customer_id:
        filters.append(TalkScriptMatchLogModel.customer_id == customer_id)
    if session_id:
        filters.append(TalkScriptMatchLogModel.session_id == session_id)
    if trace_id:
        filters.append(TalkScriptMatchLogModel.trace_id == trace_id)
    if status:
        filters.append(TalkScriptMatchLogModel.status == status)
    if scene_id:
        filters.append(TalkScriptMatchLogModel.scene_id == scene_id)
    if template_id:
        filters.append(TalkScriptMatchLogModel.template_id == template_id)
    if need_human is not None:
        filters.append(TalkScriptMatchLogModel.need_human == need_human)
    start_dt = _parse_datetime(start_time)
    if start_dt is not None:
        filters.append(TalkScriptMatchLogModel.created_at >= start_dt)
    end_dt = _parse_datetime(end_time)
    if end_dt is not None:
        filters.append(TalkScriptMatchLogModel.created_at <= end_dt)
    return filters


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count_by(session: Session, column, filters: list) -> dict:
    rows = session.execute(
        select(column, func.count())
        .where(*filters, column.is_not(None), column != "")
        .group_by(column)
    ).all()
    return {str(key): int(count or 0) for key, count in rows}


def _match_log_to_item(row: TalkScriptMatchLogModel) -> dict:
    try:
        candidate_question_ids = json.loads(row.candidate_question_ids_json or "[]")
    except json.JSONDecodeError:
        candidate_question_ids = []
    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "customer_id": row.customer_id,
        "session_id": row.session_id,
        "user_message": row.user_message,
        "normalized_message": row.normalized_message,
        "status": row.status,
        "scene_id": row.scene_id,
        "candidate_question_ids": candidate_question_ids,
        "matched_question_id": row.matched_question_id,
        "template_id": row.template_id,
        "confidence": row.confidence,
        "need_slot_filling": row.need_slot_filling,
        "need_human": row.need_human,
        "final_answer": row.final_answer,
        "match_reason": row.match_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


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
        "sales_stage": _optional(row.get("sales_stage") or row.get("stage")),
        "sales_action": _optional(row.get("sales_action")),
        "branch_code": _optional(row.get("branch_code")),
        "required_conditions_json": _json_list(row.get("required_conditions_json") or row.get("required_conditions")),
        "exclude_conditions_json": _json_list(row.get("exclude_conditions_json") or row.get("exclude_conditions")),
        "required_fact_keys_json": _json_list(row.get("required_fact_keys_json") or row.get("required_fact_keys")),
        "variables_json": _json_list(row.get("variables_json") or row.get("variables")),
        "priority": _int(row.get("priority")),
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


def _json_list(value) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            values = [item.strip() for item in value.split("|") if item.strip()]
            return json.dumps(values, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    return "[]"


def _ensure_template_library_columns(engine) -> None:
    columns = {item["name"] for item in inspect(engine).get_columns("template_library")}
    definitions = {
        "sales_stage": "VARCHAR(64)",
        "sales_action": "VARCHAR(64)",
        "branch_code": "VARCHAR(128)",
        "required_conditions_json": "TEXT DEFAULT '[]'",
        "exclude_conditions_json": "TEXT DEFAULT '[]'",
        "required_fact_keys_json": "TEXT DEFAULT '[]'",
        "variables_json": "TEXT DEFAULT '[]'",
        "priority": "INTEGER DEFAULT 0",
    }
    missing = [(name, definition) for name, definition in definitions.items() if name not in columns]
    if not missing:
        return
    with engine.begin() as connection:
        for name, definition in missing:
            connection.execute(text(f"ALTER TABLE template_library ADD COLUMN {name} {definition}"))
