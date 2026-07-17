import ast
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest
from sqlalchemy import func, select


APP_DIR = Path(__file__).resolve().parents[1] / "app"
RAW_SENDERS = {
    "send_eyun_text",
    "send_eyun_image",
    "send_eyun_video",
    "send_eyun_received_media",
    "send_eyun_mini_program",
}
EYUN_SEND_ENDPOINT = re.compile(r"/(?:send|forward)[A-Z]")


def test_production_modules_cannot_bypass_eyun_outbound_gateway():
    violations: list[str] = []
    raw_transport = APP_DIR / "services" / "eyun_callback_service.py"
    queue_worker = APP_DIR / "services" / "message_risk_control_service.py"

    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(APP_DIR)

        if path != queue_worker:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if function_name in RAW_SENDERS:
                    violations.append(f"{relative_path}:{node.lineno} calls {function_name}")

        if path != raw_transport:
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if EYUN_SEND_ENDPOINT.search(node.value):
                        violations.append(
                            f"{relative_path}:{node.lineno} references a raw Eyun send endpoint"
                        )

    assert violations == [], (
        "All WeChat sends must call enqueue_wechat_outbound; raw Eyun transports are "
        f"worker-only. Violations: {violations}"
    )


def test_thirty_per_minute_configuration_has_a_rolling_window_safety_margin(monkeypatch):
    from app.config import get_settings
    from app.services.message_risk_control_service import (
        _minimum_outbound_interval_seconds,
    )

    monkeypatch.setenv("EYUN_SEND_MAX_PER_MINUTE", "30")
    monkeypatch.setenv("EYUN_SEND_MIN_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()
    try:
        assert _minimum_outbound_interval_seconds() > 2.0
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_hundreds_of_messages_queue_without_bypassing_rate_limit(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.db.models import EyunOutboundMessageModel
    from app.services import eyun_callback_service
    from app.services import message_risk_control_service as service

    db_path = tmp_path / "bulk-outbound.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("EYUN_SEND_MAX_PER_MINUTE", "30")
    monkeypatch.setenv("EYUN_SEND_MIN_INTERVAL_SECONDS", "2.1")
    monkeypatch.setenv("EYUN_SEND_MAX_INTERVAL_SECONDS", "3.0")
    get_settings.cache_clear()
    service._sessionmakers.clear()
    service._initialized_urls.clear()

    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    sent: list[str] = []

    async def fake_send_text(*, w_id, wc_id, content):
        del w_id, wc_id
        sent.append(content)
        return {"code": "1000"}

    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", fake_send_text)

    for index in range(300):
        await service.enqueue_wechat_outbound(
            w_id="wid-bulk",
            wc_id=f"customer-{index}",
            content=f"message-{index}",
            source_batch_key=f"bulk:{index}",
            due_at=now,
        )

    assert sent == []
    assert await service.process_due_eyun_outbound_messages(limit=20) == 1
    assert await service.process_due_eyun_outbound_messages(limit=20) == 0
    assert sent == ["message-0"]

    with service._get_session() as session:
        counts = dict(
            session.execute(
                select(EyunOutboundMessageModel.status, func.count())
                .group_by(EyunOutboundMessageModel.status)
            ).all()
        )
    assert counts == {"queued": 299, "sent": 1}
