from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import EyunOutboundMessageModel


@pytest.mark.asyncio
async def test_outbound_becomes_failed_after_second_send_failure(monkeypatch, tmp_path):
    from app.services import message_risk_control_service as service

    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'retry.db').as_posix()}"
    )
    get_settings.cache_clear()
    service._sessionmakers.clear()
    monkeypatch.setattr(service, "utcnow", lambda: now)

    async def fail_send(**kwargs):
        raise RuntimeError("provider rejected permanent payload")

    monkeypatch.setattr("app.integrations.eyun.services.eyun_callback_service.send_eyun_text", fail_send)

    with service._get_session() as session:
        session.add(
            EyunOutboundMessageModel(
                w_id="wid",
                wc_id="customer",
                content="reply",
                source_batch_key="wid:customer",
                status="queued",
                due_at=now,
                attempts=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    assert await service.process_due_eyun_outbound_messages(limit=5) == 1

    with service._get_session() as session:
        row = session.query(EyunOutboundMessageModel).one()
        assert row.attempts == 2
        assert row.status == "failed"
        assert "provider rejected permanent payload" in row.last_error

    get_settings.cache_clear()
    service._sessionmakers.clear()
