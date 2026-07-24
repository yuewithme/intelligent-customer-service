import hashlib
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.trace_context import get_trace_id
from app.infrastructure.database.models import AiModelCallLogModel, Base


_sessionmakers: dict[str, sessionmaker] = {}


def record_model_call(
    *,
    purpose: str,
    provider: str,
    model: str,
    prompt: str,
    duration_ms: int,
    attempt: int,
    status: str,
    prompt_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error_class: str | None = None,
    provider_request_id: str | None = None,
    shadow: bool = False,
    trace_id: str | None = None,
) -> None:
    settings = get_settings()
    if not settings.chat_log_enabled:
        return
    try:
        with _get_session() as session:
            session.add(
                AiModelCallLogModel(
                    trace_id=trace_id or get_trace_id(),
                    purpose=purpose[:32],
                    provider=provider[:64],
                    model=model[:256],
                    prompt_version=(prompt_version or "")[:32] or None,
                    prompt_chars=len(prompt),
                    prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=max(0, int(duration_ms)),
                    attempt=max(1, int(attempt)),
                    shadow=shadow,
                    status=status[:32],
                    error_class=(error_class or "")[:128] or None,
                    provider_request_id=(provider_request_id or "")[:256] or None,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
    except Exception:
        # Telemetry must never prevent a customer reply.
        return


def _get_session():
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(engine, tables=[AiModelCallLogModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()
