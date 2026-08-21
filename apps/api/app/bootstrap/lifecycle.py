import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.domains.customers.workers.memory_worker import memory_worker
from app.domains.sales.services.service_material_touch_service import (
    service_material_touch_worker,
)
from app.integrations.eyun.services.message_risk_control_service import (
    eyun_risk_control_worker,
)
from app.integrations.eyun.services.eyun_login_monitor_service import (
    eyun_login_monitor_worker,
)
from app.integrations.youzan.services.youzan_product_sync_service import (
    youzan_product_sync_worker,
)
from app.integrations.youzan.services.youzan_order_sync_service import (
    youzan_order_sync_worker,
)
from app.integrations.mcp.server import run_sales_mcp_session_manager


logger = logging.getLogger("wechat_rag_bot.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().evaluation_mode:
        yield
        return

    stop_event = asyncio.Event()
    app.state.eyun_risk_control_stop_event = stop_event
    app.state.eyun_risk_control_task = asyncio.create_task(
        eyun_risk_control_worker(stop_event)
    )
    app.state.eyun_login_monitor_task = asyncio.create_task(
        eyun_login_monitor_worker(stop_event)
    )
    app.state.service_material_touch_task = asyncio.create_task(
        service_material_touch_worker(stop_event)
    )
    app.state.youzan_product_sync_task = asyncio.create_task(
        youzan_product_sync_worker(stop_event)
    )
    app.state.youzan_order_sync_task = asyncio.create_task(
        youzan_order_sync_worker(stop_event)
    )
    app.state.memory_v2_task = None
    if getattr(get_settings(), "memory_v2_write_enabled", False):
        app.state.memory_v2_task = asyncio.create_task(memory_worker(stop_event))

    async with run_sales_mcp_session_manager():
        try:
            yield
        finally:
            stop_event.set()
            await app.state.eyun_risk_control_task
            await app.state.eyun_login_monitor_task
            await app.state.service_material_touch_task
            await app.state.youzan_product_sync_task
            await app.state.youzan_order_sync_task
            if app.state.memory_v2_task is not None:
                await app.state.memory_v2_task
