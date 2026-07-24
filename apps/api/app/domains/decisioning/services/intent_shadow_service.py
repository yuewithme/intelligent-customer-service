import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.decisioning.schemas.intent import IntentResult
from app.infrastructure.database.models import Base, IntentShadowRunModel


_sessionmakers: dict[str, sessionmaker] = {}
_tasks: set[asyncio.Task] = set()


def shadow_selected(trace_id: str) -> bool:
    settings = get_settings()
    if not settings.intent_shadow_enabled or settings.intent_shadow_sample_percent <= 0:
        return False
    bucket = int(hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < settings.intent_shadow_sample_percent


def schedule_intent_shadow(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def record_intent_shadow(
    *,
    trace_id: str,
    primary: IntentResult,
    shadow_provider: str,
    shadow_model: str,
    shadow: IntentResult | None,
    error_class: str | None = None,
) -> None:
    try:
        agreement = bool(
            shadow is not None
            and primary.primary_domain == shadow.primary_domain
            and primary.primary_goal == shadow.primary_goal
            and set(primary.issues) == set(shadow.issues)
            and primary.scope == shadow.scope
            and primary.route == shadow.route
        )
        with _get_session() as session:
            existing = session.scalar(
                select(IntentShadowRunModel).where(
                    IntentShadowRunModel.trace_id == trace_id,
                    IntentShadowRunModel.shadow_model == shadow_model,
                )
            )
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(
                IntentShadowRunModel(
                    trace_id=trace_id,
                    primary_source=primary.classifier_source or "unknown",
                    primary_domain=primary.primary_domain,
                    primary_goal=primary.primary_goal,
                    primary_issues_json=json.dumps(primary.issues, ensure_ascii=False),
                    primary_scope=primary.scope,
                    primary_route=primary.route,
                    shadow_provider=shadow_provider,
                    shadow_model=shadow_model,
                    shadow_domain=shadow.primary_domain if shadow else None,
                    shadow_goal=shadow.primary_goal if shadow else None,
                    shadow_issues_json=json.dumps(
                        shadow.issues if shadow else [], ensure_ascii=False
                    ),
                    shadow_scope=shadow.scope if shadow else None,
                    shadow_route=shadow.route if shadow else None,
                    shadow_confidence=shadow.confidence if shadow else None,
                    agreement=agreement,
                    status="success" if shadow else "failed",
                    error_class=(error_class or "")[:128] or None,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
    except Exception:
        return


def _get_session():
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(engine, tables=[IntentShadowRunModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()
