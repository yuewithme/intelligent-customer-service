import pytest

from app.core.config import get_settings
from app.domains.conversations.services import state_service


@pytest.mark.asyncio
async def test_order_workflow_survives_process_memory_reset(monkeypatch, tmp_path):
    database = tmp_path / "workflow.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    state_service._state_store.clear()
    state_service._state_sessionmakers.clear()

    await state_service.patch_user_state(
        "wxid_order_customer",
        {
            "metadata": {
                "commerce_pending": "order_mobile",
                "commerce_mobile": "13800138000",
                "active_task": {
                    "domain": "order",
                    "task_type": "order_query",
                    "status": "awaiting_identity",
                },
            }
        },
    )
    state_service._state_store.clear()

    restored = await state_service.get_user_state(
        "wxid_order_customer",
        "session-1",
    )

    assert restored.metadata["commerce_pending"] == "order_mobile"
    assert restored.metadata["active_task"]["status"] == "awaiting_identity"
    assert "commerce_mobile" not in restored.metadata
    get_settings.cache_clear()
